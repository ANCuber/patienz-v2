"""SQLite persistence for users, groups, progress saves, grading results and logs.

Tenancy model
-------------
There is one *system* database (``MAIN_DB_PATH``) holding only the directory:
``users``, ``user_groups`` and ``app_meta``. Everything a user produces lives in
a database that belongs to exactly one group::

    <GROUP_DB_DIR>/g<group_id>/data.db                      progress_saves, grading_results
    <GROUP_DB_DIR>/g<group_id>/log_db/session_logs_YYYYMM.db  session logs (monthly shards)
    <GROUP_DB_DIR>/root/...                                 the root admin's own data

Every data function takes the ``group_id`` it operates on as its first argument
(``None`` is the root admin's private store) and can only ever open that one
group's files, so a query cannot leak or modify another group's rows. Who is
allowed to pass which ``group_id`` is decided in ``util/access.py`` and
enforced by ``util/auth.py`` / ``util/admin_data.py`` / ``util/save_load.py``.

Concurrency model
-----------------
Streamlit runs every user session in its own thread inside one process, so the
store must be safe for many threads writing at once. Each call opens a short
lived connection (never shared between threads) and every connection is set to:

* WAL journal mode, so readers never block the single writer and vice versa,
* a busy timeout, so a writer that meets the lock waits instead of raising
  "database is locked",
* ``synchronous=NORMAL``, which is durable enough under WAL and much faster
  than FULL for the many small log inserts.

Schema DDL runs once per process per database file.

PostgreSQL portability
----------------------
SQLite is the only backend, but the SQL is kept to what PostgreSQL also
accepts so a later move is mechanical: upserts use ``ON CONFLICT`` (never
``INSERT OR IGNORE`` / ``REPLACE``), timestamps are ISO-8601 text, booleans are
0/1 integers, and ``user_id`` / ``group_id`` are globally unique because they
come from the single system database. The group databases therefore merge into
one PostgreSQL database by plain concatenation (add a ``group_id`` column or
use one schema per group). The SQLite-only pieces are confined to this module:
``_connect`` (PRAGMAs), ``?`` placeholders, ``AUTOINCREMENT`` in the DDL and the
``PRAGMA``-based helpers ``_column_exists`` / ``_unique_column_sets``.
"""
import datetime
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager

MAIN_DB_PATH = os.getenv("PATIENZ_DB_PATH", "data/app.db")
# Root directory of the per-group databases. Unset: "groups" next to the system DB.
GROUP_DB_DIR = os.getenv("PATIENZ_GROUP_DB_DIR") or None
# Pre-group layout: one shared set of monthly log shards. Only read now, to
# migrate old rows into the group databases.
LOG_DB_DIR = os.getenv("PATIENZ_LOG_DB_DIR", "data/log_db")
LOG_DB_PREFIX = os.getenv("PATIENZ_LOG_DB_PREFIX", "session_logs")
BUSY_TIMEOUT_SECONDS = float(os.getenv("PATIENZ_DB_BUSY_TIMEOUT", "30"))

ROLE_ROOT = "root"
ROLE_GROUP_ADMIN = "group_admin"
ROLE_USER = "user"
ROLES = (ROLE_ROOT, ROLE_GROUP_ADMIN, ROLE_USER)
DEFAULT_GROUP_NAME = "default"
# Owner id given to legacy rows that had no user_id.
UNOWNED_USER_ID = 0
ALL_GROUPS = object()            # list_users() sentinel: do not filter by group

_schema_lock = threading.RLock()
_INITIALIZED_FOR = None          # (system DB, group dir) whose schema is ready
_GROUP_SCHEMA_READY = set()      # abspaths of group data DBs whose schema is ready
_LOG_SCHEMA_READY = set()        # abspaths of log shards whose schema is ready


def _ensure_parent_dir(path):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def _log_bucket_from_sid(sid):
    """Month bucket (YYYYMM) for a session id.

    Supported SID shapes: ``YYYYMMDDHHMMSS[…]`` (legacy) and
    ``<user_id>-YYYYMMDDHHMMSS-<hex>`` (current). Falls back to the current
    month when the timestamp cannot be found.
    """
    if isinstance(sid, str):
        for part in sid.split("-"):
            if len(part) >= 8 and part[:8].isdigit():
                return part[:6]
    return datetime.datetime.now().strftime("%Y%m")


_SID_USER_RE = re.compile(r"^(\d+)-\d{14}-")


def _user_id_from_sid(sid):
    match = _SID_USER_RE.match(sid) if isinstance(sid, str) else None
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Paths and connections
# ---------------------------------------------------------------------------

def _group_root():
    return GROUP_DB_DIR or os.path.join(os.path.dirname(MAIN_DB_PATH) or ".", "groups")


def _scope_name(group_id):
    # int() keeps anything but a real id out of the path.
    return "root" if group_id is None else f"g{int(group_id)}"


def group_dir(group_id):
    """Directory holding every database file of one group (None: root's store)."""
    return os.path.join(_group_root(), _scope_name(group_id))


def _group_db_path(group_id):
    return os.path.join(group_dir(group_id), "data.db")


def _log_db_path(group_id, sid=None, bucket=None):
    bucket = bucket or _log_bucket_from_sid(sid)
    return os.path.join(group_dir(group_id), "log_db", f"{LOG_DB_PREFIX}_{bucket}.db")


@contextmanager
def _connect(path):
    _ensure_parent_dir(path)
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level="DEFERRED")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _connect_main():
    with _connect(MAIN_DB_PATH) as conn:
        yield conn


@contextmanager
def _connect_group(group_id):
    path = _group_db_path(group_id)
    with _connect(path) as conn:
        _ensure_group_schema(conn, path)
        yield conn


@contextmanager
def _connect_log(group_id, sid=None, bucket=None):
    path = _log_db_path(group_id, sid, bucket)
    with _connect(path) as conn:
        _ensure_log_schema(conn, path)
        yield conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def _ensure_group_schema(conn, path):
    key = os.path.abspath(path)
    if key in _GROUP_SCHEMA_READY:
        return
    with _schema_lock:
        if key in _GROUP_SCHEMA_READY:
            return
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS progress_saves (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                save_name TEXT NOT NULL,
                sid TEXT,
                patient_name TEXT,
                progress_label TEXT,
                progress_index INTEGER,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (user_id, save_name)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_progress_saves_sid ON progress_saves (sid)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS grading_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                record_name TEXT NOT NULL,
                sid TEXT,
                patient_name TEXT,
                disease TEXT,
                score_v2_percentage REAL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (user_id, record_name)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_grading_results_sid ON grading_results (sid)")
        conn.commit()
        _GROUP_SCHEMA_READY.add(key)


def _ensure_log_schema(conn, path):
    key = os.path.abspath(path)
    if key in _LOG_SCHEMA_READY:
        return
    with _schema_lock:
        if key in _LOG_SCHEMA_READY:
            return
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sid TEXT NOT NULL,
                user_id INTEGER,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                source_file TEXT,
                line_no INTEGER,
                ingested_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_logs_sid_created ON session_logs (sid, created_at)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_session_logs_user ON session_logs (user_id)")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_session_logs_source_line
            ON session_logs (sid, source_file, line_no)
            WHERE source_file IS NOT NULL AND line_no IS NOT NULL
            """
        )
        conn.commit()
        _LOG_SCHEMA_READY.add(key)


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
    ).fetchone()
    return row is not None


def _column_exists(conn, table_name, column_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def _ensure_column(conn, table_name, column_name, column_sql):
    if not _column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def _unique_column_sets(conn, table_name):
    rows = conn.execute(f"PRAGMA index_list('{table_name}')").fetchall()
    result = []
    for row in rows:
        if row["unique"] != 1:
            continue
        info_rows = conn.execute(f"PRAGMA index_info('{row['name']}')").fetchall()
        result.append({col["name"] for col in info_rows})
    return result


def _ensure_unique_index(conn, table_name, columns):
    if any(set(columns) == cols for cols in _unique_column_sets(conn, table_name)):
        return
    index_name = f"idx_{table_name}_{'_'.join(columns)}_u"
    conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table_name} ({', '.join(columns)})")


def _meta_get(conn, key):
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _meta_set(conn, key, value):
    conn.execute(
        """
        INSERT INTO app_meta (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )


def init_db():
    """Create/migrate the system schema. Idempotent and cheap to call repeatedly:
    the DDL only runs once per process per database location (tests that repoint
    MAIN_DB_PATH get a fresh run automatically). Group databases create their
    own schema lazily the first time they are opened."""
    global _INITIALIZED_FOR
    target = (os.path.abspath(MAIN_DB_PATH), os.path.abspath(_group_root()))
    if _INITIALIZED_FOR == target:
        return
    with _schema_lock:
        if _INITIALIZED_FOR == target:
            return
        _init_main_schema()
        _migrate_legacy_data()
        _INITIALIZED_FOR = target


def _init_main_schema():
    with _connect_main() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Bumped on logout / password change to revoke previously issued login tokens.
        _ensure_column(conn, "users", "token_version", "token_version INTEGER NOT NULL DEFAULT 0")
        # NULL only for the root admin, who belongs to no group.
        _ensure_column(conn, "users", "group_id", "group_id INTEGER")

        default_gid = _ensure_default_group(conn)
        _migrate_roles(conn, default_gid)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_role_active ON users (role, is_active)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_group ON users (group_id)")
        # The role model itself, enforced by the database: exactly one root
        # admin overall and at most one group admin per group.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_single_root ON users (role) WHERE role = 'root'"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_group_admin ON users (group_id) "
            "WHERE role = 'group_admin'"
        )


def _ensure_default_group(conn):
    stored = _meta_get(conn, "default_group_id")
    if stored is not None:
        row = conn.execute("SELECT id FROM user_groups WHERE id = ?", (int(stored),)).fetchone()
        if row:
            return row["id"]
    row = conn.execute("SELECT id FROM user_groups WHERE name = ?", (DEFAULT_GROUP_NAME,)).fetchone()
    if row:
        gid = row["id"]
    else:
        now = _now()
        gid = conn.execute(
            "INSERT INTO user_groups (name, created_at, updated_at) VALUES (?, ?, ?)",
            (DEFAULT_GROUP_NAME, now, now),
        ).lastrowid
    _meta_set(conn, "default_group_id", gid)
    return gid


def _migrate_roles(conn, default_gid):
    """Move a pre-group database (roles ``admin`` / ``user``) to the group model:
    one legacy admin becomes the root admin, everybody else lands in the
    default group as an ordinary user."""
    has_root = conn.execute("SELECT 1 FROM users WHERE role = ?", (ROLE_ROOT,)).fetchone()
    admins = conn.execute("SELECT id, username FROM users WHERE role = 'admin' ORDER BY id").fetchall()
    if admins and not has_root:
        preferred = os.getenv("PATIENZ_ADMIN_USERNAME", "admin")
        chosen = next((a for a in admins if a["username"] == preferred), admins[0])
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (ROLE_ROOT, chosen["id"]))
        print(f"[DB] '{chosen['username']}' is now the root admin.")
        for extra in admins:
            if extra["id"] != chosen["id"]:
                print(
                    f"[DB] Former admin '{extra['username']}' is now an ordinary user in the "
                    f"'{DEFAULT_GROUP_NAME}' group; the root admin can promote it to group admin."
                )
    conn.execute(
        "UPDATE users SET role = ? WHERE role NOT IN (?, ?, ?)",
        (ROLE_USER, ROLE_ROOT, ROLE_GROUP_ADMIN, ROLE_USER),
    )
    conn.execute("UPDATE users SET group_id = NULL WHERE role = ? AND group_id IS NOT NULL", (ROLE_ROOT,))
    conn.execute(
        """
        UPDATE users SET group_id = ?
        WHERE role <> ? AND (group_id IS NULL OR group_id NOT IN (SELECT id FROM user_groups))
        """,
        (default_gid, ROLE_ROOT),
    )


# ---------------------------------------------------------------------------
# One-time migration of the pre-group layout
# ---------------------------------------------------------------------------

def _owner_scope(user_id, users, default_gid):
    """Group whose database should hold a legacy row owned by ``user_id``."""
    user = users.get(user_id)
    if user is None:
        return default_gid
    return None if user["role"] == ROLE_ROOT else user["group_id"]


def _migrate_legacy_data():
    """Copy saves / grading results out of the system DB and log rows out of the
    shared log shards into the owning group's databases. Old tables are renamed
    to ``*_pre_groups`` and old log files are left untouched, so nothing is lost
    if the copy has to be repeated."""
    with _connect_main() as conn:
        users = {
            row["id"]: {"role": row["role"], "group_id": row["group_id"]}
            for row in conn.execute("SELECT id, role, group_id FROM users").fetchall()
        }
        default_gid = _ensure_default_group(conn)

        for table, key_column in (("progress_saves", "save_name"), ("grading_results", "record_name")):
            if not _table_exists(conn, table):
                continue
            has_owner = _column_exists(conn, table, "user_id")
            by_scope = {}
            for row in conn.execute(f"SELECT * FROM {table}").fetchall():
                data = dict(row)
                data.pop("id", None)
                owner = data.get("user_id") if has_owner else None
                scope = _owner_scope(owner, users, default_gid)
                data["user_id"] = owner if owner is not None else UNOWNED_USER_ID
                by_scope.setdefault(scope, []).append(data)
            copied = 0
            for scope, rows in by_scope.items():
                columns = list(rows[0].keys())
                sql = (
                    f"INSERT INTO {table} ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)}) "
                    f"ON CONFLICT(user_id, {key_column}) DO NOTHING"
                )
                with _connect_group(scope) as group_conn:
                    group_conn.executemany(sql, [[r[c] for c in columns] for r in rows])
                copied += len(rows)
            backup = f"{table}_pre_groups"
            if _table_exists(conn, backup):
                backup = f"{backup}_{int(time.time())}"
            conn.execute(f"ALTER TABLE {table} RENAME TO {backup}")
            print(f"[DB] Moved {copied} {table} rows into the group databases (backup table: {backup}).")

        _migrate_legacy_log_shards(conn, users, default_gid)


def _migrate_legacy_log_shards(conn, users, default_gid):
    if not os.path.isdir(LOG_DB_DIR):
        return
    pattern = re.compile(re.escape(LOG_DB_PREFIX) + r"_(\d{6})\.db")
    for file_name in sorted(os.listdir(LOG_DB_DIR)):
        match = pattern.fullmatch(file_name)
        if not match:
            continue
        path = os.path.abspath(os.path.join(LOG_DB_DIR, file_name))
        meta_key = f"legacy_log_shard:{path}"
        if _meta_get(conn, meta_key) is not None:
            continue
        legacy = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
        legacy.row_factory = sqlite3.Row
        try:
            if _table_exists(legacy, "session_logs"):
                rows = legacy.execute(
                    "SELECT sid, message, created_at, source_file, line_no, ingested_at FROM session_logs"
                ).fetchall()
            else:
                rows = []
        finally:
            legacy.close()

        by_scope = {}
        for row in rows:
            owner = _user_id_from_sid(row["sid"])
            scope = _owner_scope(owner, users, default_gid)
            by_scope.setdefault(scope, []).append(
                (row["sid"], owner, row["message"], row["created_at"],
                 row["source_file"], row["line_no"], row["ingested_at"])
            )
        for scope, values in by_scope.items():
            with _connect_log(scope, bucket=match.group(1)) as log_conn:
                log_conn.executemany(_LOG_INSERT_SQL, values)
        _meta_set(conn, meta_key, len(rows))
        if rows:
            print(f"[DB] Moved {len(rows)} log rows from {file_name} into the group databases.")


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------

_GROUP_LIST_SQL = """
    SELECT g.id, g.name, g.created_at, g.updated_at,
           (SELECT COUNT(*) FROM users u WHERE u.group_id = g.id) AS member_count,
           (SELECT u.username FROM users u
             WHERE u.group_id = g.id AND u.role = 'group_admin') AS admin_username
    FROM user_groups g
"""


def default_group_id():
    with _connect_main() as conn:
        return _ensure_default_group(conn)


def create_group(name):
    """Returns the new group's id. Raises sqlite3.IntegrityError on a duplicate name."""
    now = _now()
    with _connect_main() as conn:
        return conn.execute(
            "INSERT INTO user_groups (name, created_at, updated_at) VALUES (?, ?, ?)", (name, now, now)
        ).lastrowid


def get_group(group_id):
    if group_id is None:
        return None
    with _connect_main() as conn:
        row = conn.execute(_GROUP_LIST_SQL + " WHERE g.id = ?", (group_id,)).fetchone()
    return dict(row) if row else None


def get_group_by_name(name):
    with _connect_main() as conn:
        row = conn.execute(_GROUP_LIST_SQL + " WHERE g.name = ?", (name,)).fetchone()
    return dict(row) if row else None


def list_groups():
    with _connect_main() as conn:
        rows = conn.execute(_GROUP_LIST_SQL + " ORDER BY g.id ASC").fetchall()
    return [dict(row) for row in rows]


def rename_group(group_id, name):
    with _connect_main() as conn:
        cursor = conn.execute(
            "UPDATE user_groups SET name = ?, updated_at = ? WHERE id = ?", (name, _now(), group_id)
        )
        return cursor.rowcount > 0


def delete_group(group_id):
    """Delete an *empty* group. Returns False when it still has members (or does
    not exist). The group's database directory is renamed, not removed."""
    with _connect_main() as conn:
        if group_id == _ensure_default_group(conn):
            return False
        cursor = conn.execute(
            """
            DELETE FROM user_groups
            WHERE id = ? AND NOT EXISTS (SELECT 1 FROM users WHERE group_id = ?)
            """,
            (group_id, group_id),
        )
        if cursor.rowcount == 0:
            return False
    directory = group_dir(group_id)
    if os.path.isdir(directory):
        stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        os.rename(directory, f"{directory}.deleted-{stamp}")
        with _schema_lock:
            prefix = os.path.abspath(directory) + os.sep
            for ready in (_GROUP_SCHEMA_READY, _LOG_SCHEMA_READY):
                for key in [k for k in ready if k.startswith(prefix)]:
                    ready.discard(key)
    return True


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

_USER_COLUMNS = (
    "id, username, password_hash, role, group_id, is_active, token_version, created_at, updated_at"
)


def get_user_by_username(username):
    with _connect_main() as conn:
        row = conn.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id):
    with _connect_main() as conn:
        row = conn.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_root_user():
    with _connect_main() as conn:
        row = conn.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE role = ?", (ROLE_ROOT,)).fetchone()
    return dict(row) if row else None


def get_group_admin(group_id):
    with _connect_main() as conn:
        row = conn.execute(
            f"SELECT {_USER_COLUMNS} FROM users WHERE group_id = ? AND role = ?",
            (group_id, ROLE_GROUP_ADMIN),
        ).fetchone()
    return dict(row) if row else None


def create_user(username, password_hash, role=ROLE_USER, group_id=None):
    """Insert a user and return its id. ``group_id`` must be None for the root
    admin and a real group for everybody else (checked by util/auth.py)."""
    now = _now()
    with _connect_main() as conn:
        return conn.execute(
            """
            INSERT INTO users (username, password_hash, role, group_id, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (username, password_hash, role, group_id, now, now),
        ).lastrowid


def update_user(user_id, username, password_hash=None, role=ROLE_USER, is_active=1, group_id=None):
    """Update profile fields. A password change also bumps token_version so
    every previously issued login token for that user stops working. Does not
    move the user's data between group databases; see ``move_user_data``."""
    now = _now()
    if password_hash is None:
        sql = """
            UPDATE users
            SET username = ?, role = ?, group_id = ?, is_active = ?, updated_at = ?
            WHERE id = ?
        """
        params = (username, role, group_id, int(is_active), now, user_id)
    else:
        sql = """
            UPDATE users
            SET username = ?, password_hash = ?, role = ?, group_id = ?, is_active = ?, updated_at = ?,
                token_version = token_version + 1
            WHERE id = ?
        """
        params = (username, password_hash, role, group_id, int(is_active), now, user_id)

    with _connect_main() as conn:
        cursor = conn.execute(sql, params)
        return cursor.rowcount > 0


def bump_token_version(user_id):
    """Invalidate all outstanding login tokens for a user (logout everywhere)."""
    with _connect_main() as conn:
        conn.execute(
            "UPDATE users SET token_version = token_version + 1, updated_at = ? WHERE id = ?",
            (_now(), user_id),
        )


def list_users(group_id=ALL_GROUPS):
    """All users, or only the members of one group. Never returns password hashes."""
    sql = """
        SELECT u.id, u.username, u.role, u.group_id, g.name AS group_name,
               u.is_active, u.created_at, u.updated_at
        FROM users u LEFT JOIN user_groups g ON g.id = u.group_id
    """
    params = ()
    if group_id is not ALL_GROUPS:
        sql += " WHERE u.group_id = ?"
        params = (group_id,)
    with _connect_main() as conn:
        rows = conn.execute(sql + " ORDER BY u.username ASC", params).fetchall()
    return [dict(row) for row in rows]


def count_users():
    with _connect_main() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"])


def delete_user(user_id):
    """Delete a user together with their saves and grading results."""
    user = get_user_by_id(user_id)
    if not user:
        return
    scope = None if user["role"] == ROLE_ROOT else user["group_id"]
    with _connect_group(scope) as conn:
        conn.execute("DELETE FROM progress_saves WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM grading_results WHERE user_id = ?", (user_id,))
    with _connect_main() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def move_user_data(user_id, from_group_id, to_group_id):
    """Carry one user's saves, grading results and logs from one group's
    databases to another's (used when the root admin reassigns a user). Rows
    are copied first and removed from the source only afterwards."""
    if from_group_id == to_group_id:
        return
    for table, key_column in (("progress_saves", "save_name"), ("grading_results", "record_name")):
        with _connect_group(from_group_id) as src:
            rows = [dict(r) for r in src.execute(f"SELECT * FROM {table} WHERE user_id = ?", (user_id,))]
        if not rows:
            continue
        for row in rows:
            row.pop("id", None)
        columns = list(rows[0].keys())
        with _connect_group(to_group_id) as dst:
            dst.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) "
                f"ON CONFLICT(user_id, {key_column}) DO NOTHING",
                [[r[c] for c in columns] for r in rows],
            )
        with _connect_group(from_group_id) as src:
            src.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))

    log_dir = os.path.join(group_dir(from_group_id), "log_db")
    if not os.path.isdir(log_dir):
        return
    pattern = re.compile(re.escape(LOG_DB_PREFIX) + r"_(\d{6})\.db")
    for file_name in sorted(os.listdir(log_dir)):
        match = pattern.fullmatch(file_name)
        if not match:
            continue
        bucket = match.group(1)
        with _connect_log(from_group_id, bucket=bucket) as src:
            rows = src.execute(
                "SELECT sid, user_id, message, created_at, source_file, line_no, ingested_at "
                "FROM session_logs WHERE user_id = ? ORDER BY id",
                (user_id,),
            ).fetchall()
        if not rows:
            continue
        with _connect_log(to_group_id, bucket=bucket) as dst:
            dst.executemany(_LOG_INSERT_SQL, [tuple(r) for r in rows])
        with _connect_log(from_group_id, bucket=bucket) as src:
            src.execute("DELETE FROM session_logs WHERE user_id = ?", (user_id,))


# ---------------------------------------------------------------------------
# Progress saves (one group's database)
# ---------------------------------------------------------------------------

def upsert_progress_save(group_id, user_id, save_name, sid, patient_name, progress_label, progress_index,
                         payload):
    payload_json = json.dumps(payload, ensure_ascii=False)
    now = _now()
    with _connect_group(group_id) as conn:
        conn.execute(
            """
            INSERT INTO progress_saves (
                save_name, user_id, sid, patient_name, progress_label,
                progress_index, payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, save_name) DO UPDATE SET
                sid=excluded.sid,
                patient_name=excluded.patient_name,
                progress_label=excluded.progress_label,
                progress_index=excluded.progress_index,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (save_name, user_id, sid, patient_name, progress_label, progress_index, payload_json, now, now),
        )


def list_progress_saves(group_id, user_id=None):
    """Metadata of the saves in one group, optionally narrowed to one user."""
    sql = (
        "SELECT save_name, user_id, sid, patient_name, progress_label, progress_index, "
        "created_at, updated_at FROM progress_saves"
    )
    params = ()
    if user_id is not None:
        sql += " WHERE user_id = ?"
        params = (user_id,)
    with _connect_group(group_id) as conn:
        rows = conn.execute(sql + " ORDER BY updated_at DESC, id DESC", params).fetchall()
    return [dict(row) for row in rows]


def list_progress_save_names(group_id, user_id):
    return [row["save_name"] for row in list_progress_saves(group_id, user_id)]


def get_progress_payload(group_id, user_id, save_name):
    with _connect_group(group_id) as conn:
        row = conn.execute(
            "SELECT payload_json FROM progress_saves WHERE save_name = ? AND user_id = ?",
            (save_name, user_id),
        ).fetchone()
    if not row:
        return None
    return json.loads(row["payload_json"])


def delete_progress_save(group_id, user_id, save_name):
    with _connect_group(group_id) as conn:
        cursor = conn.execute(
            "DELETE FROM progress_saves WHERE save_name = ? AND user_id = ?", (save_name, user_id)
        )
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Grading results (one group's database)
# ---------------------------------------------------------------------------

def upsert_grading_result(group_id, user_id, record_name, sid, patient_name, disease, score_v2_percentage,
                          payload):
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)
    now = _now()
    with _connect_group(group_id) as conn:
        conn.execute(
            """
            INSERT INTO grading_results (
                record_name, user_id, sid, patient_name, disease,
                score_v2_percentage, payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, record_name) DO UPDATE SET
                sid=excluded.sid,
                patient_name=excluded.patient_name,
                disease=excluded.disease,
                score_v2_percentage=excluded.score_v2_percentage,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (record_name, user_id, sid, patient_name, disease, score_v2_percentage, payload_json, now, now),
        )


def list_grading_results(group_id, user_id=None):
    """Metadata of the grading results in one group, optionally for one user."""
    sql = (
        "SELECT record_name, user_id, sid, patient_name, disease, score_v2_percentage, "
        "created_at, updated_at FROM grading_results"
    )
    params = ()
    if user_id is not None:
        sql += " WHERE user_id = ?"
        params = (user_id,)
    with _connect_group(group_id) as conn:
        rows = conn.execute(sql + " ORDER BY updated_at DESC, id DESC", params).fetchall()
    return [dict(row) for row in rows]


def get_grading_payload(group_id, user_id, record_name):
    with _connect_group(group_id) as conn:
        row = conn.execute(
            "SELECT payload_json FROM grading_results WHERE record_name = ? AND user_id = ?",
            (record_name, user_id),
        ).fetchone()
    if not row:
        return None
    return json.loads(row["payload_json"])


def delete_grading_result(group_id, user_id, record_name):
    with _connect_group(group_id) as conn:
        cursor = conn.execute(
            "DELETE FROM grading_results WHERE record_name = ? AND user_id = ?", (record_name, user_id)
        )
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Session logs (monthly shards inside one group's directory)
# ---------------------------------------------------------------------------

_LOG_INSERT_SQL = """
    INSERT INTO session_logs (sid, user_id, message, created_at, source_file, line_no, ingested_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT DO NOTHING
"""


def group_id_for_sid(sid):
    """Group whose log database a session id belongs to, derived from the user
    id embedded in the SID. Legacy SIDs without one go to the default group."""
    owner = _user_id_from_sid(sid)
    user = get_user_by_id(owner) if owner is not None else None
    if user is None:
        return default_group_id()
    return None if user["role"] == ROLE_ROOT else user["group_id"]


def append_log(group_id, sid, message, user_id=None, created_at=None, source_file=None, line_no=None):
    created = created_at or _now()
    with _connect_log(group_id, sid) as conn:
        conn.execute(_LOG_INSERT_SQL, (sid, user_id, message, created, source_file, line_no, _now()))


def ingest_log_lines(group_id, sid, lines, source_file, user_id=None):
    """Bulk-ingest a SID log file into the group's monthly log shard.

    Idempotent when line numbers are stable for a given source file. Files are
    not modified or deleted. Returns the number of newly inserted rows.
    """
    now = _now()
    rows = []
    for idx, line in enumerate(lines, start=1):
        text = line.rstrip("\n")
        if text == "":
            continue
        rows.append((sid, user_id, text, now, source_file, idx, now))

    if not rows:
        return 0

    with _connect_log(group_id, sid) as conn:
        before = conn.total_changes
        conn.executemany(_LOG_INSERT_SQL, rows)
        inserted = conn.total_changes - before
    return inserted


def count_log_rows(group_id, user_id=None):
    """Number of log lines stored for a group (optionally for one user)."""
    log_dir = os.path.join(group_dir(group_id), "log_db")
    if not os.path.isdir(log_dir):
        return 0
    pattern = re.compile(re.escape(LOG_DB_PREFIX) + r"_(\d{6})\.db")
    total = 0
    for file_name in os.listdir(log_dir):
        match = pattern.fullmatch(file_name)
        if not match:
            continue
        with _connect_log(group_id, bucket=match.group(1)) as conn:
            if user_id is None:
                row = conn.execute("SELECT COUNT(*) AS n FROM session_logs").fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM session_logs WHERE user_id = ?", (user_id,)
                ).fetchone()
        total += int(row["n"])
    return total
