"""SQLite persistence for users, progress saves, grading results and session logs.

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

Schema DDL runs once per process per database file (see ``init_db`` and
``_ensure_log_schema``), not on every call.

Session logs are sharded into one SQLite file per month under ``LOG_DB_DIR``.
"""
import datetime
import json
import os
import sqlite3
import threading
from contextlib import contextmanager

MAIN_DB_PATH = os.getenv("PATIENZ_DB_PATH", "data/app.db")
LOG_DB_DIR = os.getenv("PATIENZ_LOG_DB_DIR", "data/log_db")
LOG_DB_PREFIX = os.getenv("PATIENZ_LOG_DB_PREFIX", "session_logs")
BUSY_TIMEOUT_SECONDS = float(os.getenv("PATIENZ_DB_BUSY_TIMEOUT", "30"))

# Re-entrant: init_db holds it while creating the log shard, which locks it again.
_schema_lock = threading.RLock()
_INITIALIZED_FOR = None          # abspath of the main DB whose schema is ready
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


def _log_db_path(sid=None):
    bucket = _log_bucket_from_sid(sid)
    return os.path.join(LOG_DB_DIR, f"{LOG_DB_PREFIX}_{bucket}.db")


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
def _connect_log(sid=None):
    path = _log_db_path(sid)
    with _connect(path) as conn:
        _ensure_log_schema(conn, path)
        yield conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

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
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_session_logs_source_line
            ON session_logs (sid, source_file, line_no)
            WHERE source_file IS NOT NULL AND line_no IS NOT NULL
            """
        )
        conn.commit()
        _LOG_SCHEMA_READY.add(key)


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


def _migrate_user_scoped_table(conn, table_name, key_column, create_sql, col_names):
    """Rebuild a table that still carries the pre-multi-user UNIQUE(key_column)
    constraint so the (user_id, key_column) index can take over."""
    if not any({key_column} == cols for cols in _unique_column_sets(conn, table_name)):
        return
    legacy_table = f"{table_name}_legacy"
    columns_sql = ", ".join(col_names)
    conn.execute(f"ALTER TABLE {table_name} RENAME TO {legacy_table}")
    conn.execute(create_sql)
    conn.execute(f"INSERT INTO {table_name} ({columns_sql}) SELECT {columns_sql} FROM {legacy_table}")
    conn.execute(f"DROP TABLE {legacy_table}")


def _ensure_unique_index(conn, table_name, columns):
    if any(set(columns) == cols for cols in _unique_column_sets(conn, table_name)):
        return
    index_name = f"idx_{table_name}_{'_'.join(columns)}_u"
    conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table_name} ({', '.join(columns)})")


_PROGRESS_SAVES_SQL = """
    CREATE TABLE IF NOT EXISTS progress_saves (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        save_name TEXT NOT NULL,
        sid TEXT,
        patient_name TEXT,
        progress_label TEXT,
        progress_index INTEGER,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        user_id INTEGER
    )
"""
_PROGRESS_SAVES_COLS = [
    "id", "save_name", "sid", "patient_name", "progress_label",
    "progress_index", "payload_json", "created_at", "updated_at", "user_id",
]

_GRADING_RESULTS_SQL = """
    CREATE TABLE IF NOT EXISTS grading_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        record_name TEXT NOT NULL,
        sid TEXT,
        patient_name TEXT,
        disease TEXT,
        score_v2_percentage REAL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        user_id INTEGER
    )
"""
_GRADING_RESULTS_COLS = [
    "id", "record_name", "sid", "patient_name", "disease",
    "score_v2_percentage", "payload_json", "created_at", "updated_at", "user_id",
]


def init_db():
    """Create/migrate the main schema. Idempotent and cheap to call repeatedly:
    the DDL only runs once per process per database file (tests that repoint
    MAIN_DB_PATH get a fresh run automatically)."""
    global _INITIALIZED_FOR
    target = os.path.abspath(MAIN_DB_PATH)
    if _INITIALIZED_FOR == target:
        return
    with _schema_lock:
        if _INITIALIZED_FOR == target:
            return
        _init_main_schema()
        _INITIALIZED_FOR = target


def _init_main_schema():
    with _connect_main() as conn:
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_role_active ON users (role, is_active)")
        # Bumped on logout / password change to revoke previously issued login tokens.
        _ensure_column(conn, "users", "token_version", "token_version INTEGER NOT NULL DEFAULT 0")

        conn.execute(_PROGRESS_SAVES_SQL)
        _ensure_column(conn, "progress_saves", "user_id", "user_id INTEGER")
        _migrate_user_scoped_table(conn, "progress_saves", "save_name", _PROGRESS_SAVES_SQL, _PROGRESS_SAVES_COLS)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_progress_saves_sid ON progress_saves (sid)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_progress_saves_user_id ON progress_saves (user_id)")
        _ensure_unique_index(conn, "progress_saves", ("user_id", "save_name"))

        conn.execute(_GRADING_RESULTS_SQL)
        _ensure_column(conn, "grading_results", "user_id", "user_id INTEGER")
        _migrate_user_scoped_table(conn, "grading_results", "record_name", _GRADING_RESULTS_SQL, _GRADING_RESULTS_COLS)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_grading_results_sid ON grading_results (sid)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_grading_results_user_id ON grading_results (user_id)")
        _ensure_unique_index(conn, "grading_results", ("user_id", "record_name"))

    # Make sure the current month's log shard exists for runtime writes.
    with _connect_log():
        pass


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Progress saves
# ---------------------------------------------------------------------------

def upsert_progress_save(save_name, sid, patient_name, progress_label, progress_index, payload, user_id=None):
    payload_json = json.dumps(payload, ensure_ascii=False)
    now = _now()
    with _connect_main() as conn:
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


def list_progress_save_names(user_id=None):
    with _connect_main() as conn:
        if user_id is None:
            rows = conn.execute(
                "SELECT save_name FROM progress_saves ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT save_name FROM progress_saves WHERE user_id = ? ORDER BY updated_at DESC, id DESC",
                (user_id,),
            ).fetchall()
    return [row["save_name"] for row in rows]


def get_progress_payload(save_name, user_id=None):
    with _connect_main() as conn:
        if user_id is None:
            row = conn.execute(
                "SELECT payload_json FROM progress_saves WHERE save_name = ?", (save_name,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT payload_json FROM progress_saves WHERE save_name = ? AND user_id = ?",
                (save_name, user_id),
            ).fetchone()
    if not row:
        return None
    return json.loads(row["payload_json"])


def delete_progress_save(save_name, user_id=None):
    with _connect_main() as conn:
        if user_id is None:
            conn.execute("DELETE FROM progress_saves WHERE save_name = ?", (save_name,))
        else:
            conn.execute(
                "DELETE FROM progress_saves WHERE save_name = ? AND user_id = ?", (save_name, user_id)
            )


# ---------------------------------------------------------------------------
# Grading results
# ---------------------------------------------------------------------------

def upsert_grading_result(record_name, sid, patient_name, disease, score_v2_percentage, payload, user_id=None):
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)
    now = _now()
    with _connect_main() as conn:
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


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

_USER_COLUMNS = "id, username, password_hash, role, is_active, token_version, created_at, updated_at"


def get_user_by_username(username):
    with _connect_main() as conn:
        row = conn.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id):
    with _connect_main() as conn:
        row = conn.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def create_user(username, password_hash, role="user"):
    now = _now()
    with _connect_main() as conn:
        conn.execute(
            """
            INSERT INTO users (username, password_hash, role, is_active, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (username, password_hash, role, now, now),
        )


def update_user_by_username(old_username, username, password_hash=None, role="user", is_active=1):
    """Update profile fields. A password change also bumps token_version so
    every previously issued login token for that user stops working."""
    now = _now()
    if password_hash is None:
        sql = """
            UPDATE users
            SET username = ?, role = ?, is_active = ?, updated_at = ?
            WHERE username = ?
        """
        params = (username, role, int(is_active), now, old_username)
    else:
        sql = """
            UPDATE users
            SET username = ?, password_hash = ?, role = ?, is_active = ?, updated_at = ?,
                token_version = token_version + 1
            WHERE username = ?
        """
        params = (username, password_hash, role, int(is_active), now, old_username)

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


def list_users():
    with _connect_main() as conn:
        rows = conn.execute(
            "SELECT id, username, role, is_active, created_at, updated_at FROM users ORDER BY username ASC"
        ).fetchall()
    return [dict(row) for row in rows]


def count_users():
    with _connect_main() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"])


def count_admin_users():
    with _connect_main() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND is_active = 1"
        ).fetchone()
    return int(row["n"])


def delete_user_by_username(username):
    """Delete a user together with their DB-side saves and grading results."""
    with _connect_main() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            return
        user_id = row["id"]
        conn.execute("DELETE FROM progress_saves WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM grading_results WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


# ---------------------------------------------------------------------------
# Session logs (monthly shards)
# ---------------------------------------------------------------------------

def append_log(sid, message, created_at=None, source_file=None, line_no=None):
    created = created_at or _now()
    ingested = _now()
    with _connect_log(sid) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO session_logs (
                sid, message, created_at, source_file, line_no, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (sid, message, created, source_file, line_no, ingested),
        )


def ingest_log_lines(sid, lines, source_file):
    """Bulk-ingest a SID log file into the monthly log shard.

    Idempotent when line numbers are stable for a given source file. Files are
    not modified or deleted. Returns the number of newly inserted rows.
    """
    now = _now()
    rows = []
    for idx, line in enumerate(lines, start=1):
        text = line.rstrip("\n")
        if text == "":
            continue
        rows.append((sid, text, now, source_file, idx, now))

    if not rows:
        return 0

    with _connect_log(sid) as conn:
        before = conn.total_changes
        conn.executemany(
            """
            INSERT OR IGNORE INTO session_logs (
                sid, message, created_at, source_file, line_no, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        inserted = conn.total_changes - before
    return inserted
