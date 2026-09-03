import datetime
import json
import os
import sqlite3
from contextlib import contextmanager

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:
    psycopg = None
    dict_row = None

MAIN_DB_PATH = os.getenv("PATIENZ_DB_PATH", "data/app.db")
LOG_DB_DIR = os.getenv("PATIENZ_LOG_DB_DIR", "data/log_db")
LOG_DB_PREFIX = os.getenv("PATIENZ_LOG_DB_PREFIX", "session_logs")
DB_URL = os.getenv("PATIENZ_DB_URL", "").strip()


def _is_postgres():
    return DB_URL.startswith("postgres://") or DB_URL.startswith("postgresql://")


def _ensure_parent_dir(path):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def _log_bucket_from_sid(sid):
    # SID format is usually YYYYMMDDHHMMSS; fallback to current month if unknown.
    if isinstance(sid, str) and len(sid) >= 6 and sid[:6].isdigit():
        return sid[:6]
    return datetime.datetime.now().strftime("%Y%m")


def _log_db_path(sid=None):
    bucket = _log_bucket_from_sid(sid)
    _ensure_parent_dir(f"{LOG_DB_DIR}/x")
    return os.path.join(LOG_DB_DIR, f"{LOG_DB_PREFIX}_{bucket}.db")


def _log_table_name(sid=None):
    if not _is_postgres():
        return "session_logs"
    bucket = _log_bucket_from_sid(sid)
    return f"session_logs_{bucket}"


@contextmanager
def _connect(path):
    _ensure_parent_dir(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def _connect_postgres():
    if psycopg is None:
        raise RuntimeError(
            "PATIENZ_DB_URL points to PostgreSQL but psycopg is not installed. "
            "Install dependency: psycopg[binary]."
        )
    conn = psycopg.connect(DB_URL, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def _connect_main():
    if _is_postgres():
        with _connect_postgres() as conn:
            yield conn
    else:
        with _connect(MAIN_DB_PATH) as conn:
            yield conn


@contextmanager
def _connect_log(sid=None):
    if _is_postgres():
        with _connect_main() as conn:
            yield conn
    else:
        with _connect(_log_db_path(sid)) as conn:
            yield conn


def _adapt_sql(sql):
    if _is_postgres():
        return sql.replace("?", "%s")
    return sql


def _execute(conn, sql, params=None):
    if params is None:
        return conn.execute(_adapt_sql(sql))
    return conn.execute(_adapt_sql(sql), params)


def _ensure_log_schema(conn, sid=None):
    table_name = _log_table_name(sid)
    if _is_postgres():
        _execute(
            conn,
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id BIGSERIAL PRIMARY KEY,
                sid TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                source_file TEXT,
                line_no INTEGER,
                ingested_at TEXT NOT NULL
            )
            """,
        )
        _execute(
            conn,
            f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_sid_created
            ON {table_name} (sid, created_at)
            """,
        )
        _execute(
            conn,
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_{table_name}_source_line
            ON {table_name} (sid, source_file, line_no)
            WHERE source_file IS NOT NULL AND line_no IS NOT NULL
            """,
        )
        return

    _execute(
        conn,
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
        """,
    )
    _execute(
        conn,
        """
        CREATE INDEX IF NOT EXISTS idx_session_logs_sid_created
        ON session_logs (sid, created_at)
        """
    )
    _execute(
        conn,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_session_logs_source_line
        ON session_logs (sid, source_file, line_no)
        WHERE source_file IS NOT NULL AND line_no IS NOT NULL
        """
    )


def _column_exists(conn, table_name, column_name):
    if _is_postgres():
        row = _execute(
            conn,
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = ?
              AND column_name = ?
            LIMIT 1
            """,
            (table_name, column_name),
        ).fetchone()
        return row is not None

    rows = _execute(conn, f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def _ensure_column(conn, table_name, column_name, column_sql):
    if _is_postgres():
        _execute(conn, f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column_sql}")
        return
    if not _column_exists(conn, table_name, column_name):
        _execute(conn, f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def _sqlite_unique_sets(conn, table_name):
    rows = _execute(conn, f"PRAGMA index_list('{table_name}')").fetchall()
    result = []
    for row in rows:
        if row["unique"] != 1:
            continue
        info_rows = _execute(conn, f"PRAGMA index_info('{row['name']}')").fetchall()
        result.append({col["name"] for col in info_rows})
    return result


def _migrate_user_scoped_table(conn, table_name, key_column):
    if _is_postgres():
        return

    unique_sets = _sqlite_unique_sets(conn, table_name)
    if not any(set({key_column}) == cols for cols in unique_sets):
        return

    legacy_table = f"{table_name}_legacy"
    if table_name == "progress_saves":
        col_names = [
            "id", "save_name", "sid", "patient_name", "progress_label",
            "progress_index", "payload_json", "created_at", "updated_at", "user_id",
        ]
        create_sql = """
            CREATE TABLE progress_saves (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                save_name TEXT NOT NULL,
                sid TEXT,
                patient_name TEXT,
                progress_label TEXT,
                progress_index INTEGER,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                user_id BIGINT
            )
        """
    else:
        col_names = [
            "id", "record_name", "sid", "patient_name", "disease",
            "score_v2_percentage", "payload_json", "created_at", "updated_at", "user_id",
        ]
        create_sql = """
            CREATE TABLE grading_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_name TEXT NOT NULL,
                sid TEXT,
                patient_name TEXT,
                disease TEXT,
                score_v2_percentage REAL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                user_id BIGINT
            )
        """

    _execute(conn, f"ALTER TABLE {table_name} RENAME TO {legacy_table}")
    _execute(conn, create_sql)
    columns_sql = ", ".join(col_names)
    _execute(
        conn,
        f"INSERT INTO {table_name} ({columns_sql}) SELECT {columns_sql} FROM {legacy_table}",
    )
    _execute(conn, f"DROP TABLE {legacy_table}")


def _ensure_user_scoped_unique_index(conn, table_name, columns):
    columns_sql = ", ".join(columns)
    index_name = f"idx_{table_name}_{'_'.join(columns)}_u"
    if _is_postgres():
        _execute(
            conn,
            f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table_name} ({columns_sql})",
        )
        return

    unique_sets = _sqlite_unique_sets(conn, table_name)
    if any(set(columns) == cols for cols in unique_sets):
        return

    _execute(
        conn,
        f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table_name} ({columns_sql})",
    )


def init_db():
    with _connect_main() as conn:
        if _is_postgres():
            _execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS users (
                    id BIGSERIAL PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """,
            )
        else:
            _execute(
                conn,
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
                """,
            )
        _execute(
            conn,
            """
            CREATE INDEX IF NOT EXISTS idx_users_role_active
            ON users (role, is_active)
            """
        )

        if _is_postgres():
            _execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS progress_saves (
                    id BIGSERIAL PRIMARY KEY,
                    save_name TEXT NOT NULL,
                    sid TEXT,
                    patient_name TEXT,
                    progress_label TEXT,
                    progress_index INTEGER,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """,
            )
        else:
            _execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS progress_saves (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    save_name TEXT NOT NULL,
                    sid TEXT,
                    patient_name TEXT,
                    progress_label TEXT,
                    progress_index INTEGER,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """,
            )
        _ensure_column(conn, "progress_saves", "user_id", "user_id BIGINT")
        _migrate_user_scoped_table(conn, "progress_saves", "save_name")
        _execute(
            conn,
            """
            CREATE INDEX IF NOT EXISTS idx_progress_saves_sid
            ON progress_saves (sid)
            """
        )
        _execute(
            conn,
            """
            CREATE INDEX IF NOT EXISTS idx_progress_saves_user_id
            ON progress_saves (user_id)
            """
        )
        _ensure_user_scoped_unique_index(conn, "progress_saves", ("user_id", "save_name"))

        if _is_postgres():
            _execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS grading_results (
                    id BIGSERIAL PRIMARY KEY,
                    record_name TEXT NOT NULL,
                    sid TEXT,
                    patient_name TEXT,
                    disease TEXT,
                    score_v2_percentage DOUBLE PRECISION,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """,
            )
        else:
            _execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS grading_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_name TEXT NOT NULL,
                    sid TEXT,
                    patient_name TEXT,
                    disease TEXT,
                    score_v2_percentage REAL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """,
            )
        _ensure_column(conn, "grading_results", "user_id", "user_id BIGINT")
        _migrate_user_scoped_table(conn, "grading_results", "record_name")
        _execute(
            conn,
            """
            CREATE INDEX IF NOT EXISTS idx_grading_results_sid
            ON grading_results (sid)
            """
        )
        _execute(
            conn,
            """
            CREATE INDEX IF NOT EXISTS idx_grading_results_user_id
            ON grading_results (user_id)
            """
        )
        _ensure_user_scoped_unique_index(conn, "grading_results", ("user_id", "record_name"))

        if _is_postgres():
            # Create current-month log shard table on startup.
            _ensure_log_schema(conn)

    if not _is_postgres():
        # Ensure current bucket exists for runtime log writes.
        with _connect_log() as conn:
            _ensure_log_schema(conn)


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def upsert_progress_save(save_name, sid, patient_name, progress_label, progress_index, payload, user_id=None):
    payload_json = json.dumps(payload, ensure_ascii=False)
    now = _now()
    with _connect_main() as conn:
        _execute(
            conn,
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
            (
                save_name,
                user_id,
                sid,
                patient_name,
                progress_label,
                progress_index,
                payload_json,
                now,
                now,
            ),
        )


def list_progress_save_names(user_id=None):
    with _connect_main() as conn:
        if user_id is None:
            rows = _execute(
                conn,
                """
                SELECT save_name
                FROM progress_saves
                ORDER BY updated_at DESC, id DESC
                """
            ).fetchall()
        else:
            rows = _execute(
                conn,
                """
                SELECT save_name
                FROM progress_saves
                WHERE user_id = ?
                ORDER BY updated_at DESC, id DESC
                """,
                (user_id,),
            ).fetchall()
    return [row["save_name"] for row in rows]


def get_progress_payload(save_name, user_id=None):
    with _connect_main() as conn:
        if user_id is None:
            row = _execute(
                conn,
                """
                SELECT payload_json
                FROM progress_saves
                WHERE save_name = ?
                """,
                (save_name,),
            ).fetchone()
        else:
            row = _execute(
                conn,
                """
                SELECT payload_json
                FROM progress_saves
                WHERE save_name = ? AND user_id = ?
                """,
                (save_name, user_id),
            ).fetchone()
    if not row:
        return None
    return json.loads(row["payload_json"])


def delete_progress_save(save_name, user_id=None):
    with _connect_main() as conn:
        if user_id is None:
            _execute(
                conn,
                """
                DELETE FROM progress_saves
                WHERE save_name = ?
                """,
                (save_name,),
            )
        else:
            _execute(
                conn,
                """
                DELETE FROM progress_saves
                WHERE save_name = ? AND user_id = ?
                """,
                (save_name, user_id),
            )


def upsert_grading_result(record_name, sid, patient_name, disease, score_v2_percentage, payload, user_id=None):
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)
    now = _now()
    with _connect_main() as conn:
        _execute(
            conn,
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
            (
                record_name,
                user_id,
                sid,
                patient_name,
                disease,
                score_v2_percentage,
                payload_json,
                now,
                now,
            ),
        )


def get_user_by_username(username):
    with _connect_main() as conn:
        row = _execute(
            conn,
            """
            SELECT id, username, password_hash, role, is_active, created_at, updated_at
            FROM users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def create_user(username, password_hash, role="user"):
    now = _now()
    with _connect_main() as conn:
        _execute(
            conn,
            """
            INSERT INTO users (username, password_hash, role, is_active, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (username, password_hash, role, now, now),
        )


def upsert_user(username, password_hash, role="user", is_active=1):
    now = _now()
    with _connect_main() as conn:
        _execute(
            conn,
            """
            INSERT INTO users (username, password_hash, role, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                password_hash=excluded.password_hash,
                role=excluded.role,
                is_active=excluded.is_active,
                updated_at=excluded.updated_at
            """,
            (username, password_hash, role, int(is_active), now, now),
        )


def list_users():
    with _connect_main() as conn:
        rows = _execute(
            conn,
            """
            SELECT id, username, role, is_active, created_at, updated_at
            FROM users
            ORDER BY username ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def count_users():
    with _connect_main() as conn:
        row = _execute(conn, "SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"])


def count_admin_users():
    with _connect_main() as conn:
        row = _execute(
            conn,
            """
            SELECT COUNT(*) AS n
            FROM users
            WHERE role = 'admin' AND is_active = 1
            """
        ).fetchone()
    return int(row["n"])


def delete_user_by_username(username):
    with _connect_main() as conn:
        _execute(
            conn,
            """
            DELETE FROM users
            WHERE username = ?
            """,
            (username,),
        )


def append_log(sid, message, created_at=None, source_file=None, line_no=None):
    created = created_at or _now()
    ingested = _now()
    with _connect_log(sid) as conn:
        _ensure_log_schema(conn, sid=sid)
        table_name = _log_table_name(sid)
        if source_file is not None and line_no is not None:
            if _is_postgres():
                _execute(
                    conn,
                    f"""
                    INSERT INTO {table_name} (
                        sid, message, created_at, source_file, line_no, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (sid, source_file, line_no)
                    DO NOTHING
                    """,
                    (sid, message, created, source_file, line_no, ingested),
                )
            else:
                _execute(
                    conn,
                    """
                    INSERT OR IGNORE INTO session_logs (
                        sid, message, created_at, source_file, line_no, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (sid, message, created, source_file, line_no, ingested),
                )
            return

        if _is_postgres():
            _execute(
                conn,
                f"""
                INSERT INTO {table_name} (
                    sid, message, created_at, source_file, line_no, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (sid, source_file, line_no)
                DO NOTHING
                """,
                (sid, message, created, source_file, line_no, ingested),
            )
            return

        _execute(
            conn,
            """
            INSERT INTO session_logs (
                sid, message, created_at, source_file, line_no, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (sid, message, created, source_file, line_no, ingested),
        )


def ingest_log_lines(sid, lines, source_file):
    """Bulk-ingest a SID log file into the monthly log shard.

    This is idempotent when line numbers are stable for a given source file.
    Files are not modified or deleted.
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
        _ensure_log_schema(conn, sid=sid)
        table_name = _log_table_name(sid)
        if _is_postgres():
            conn.executemany(
                _adapt_sql(
                    f"""
                    INSERT INTO {table_name} (
                        sid, message, created_at, source_file, line_no, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (sid, source_file, line_no)
                    DO NOTHING
                    """
                ),
                rows,
            )
            inserted = len(rows)
        else:
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
