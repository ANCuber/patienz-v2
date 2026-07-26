import datetime
import json
import os
import sqlite3
from contextlib import contextmanager

MAIN_DB_PATH = os.getenv("PATIENZ_DB_PATH", "data/app.db")
LOG_DB_DIR = os.getenv("PATIENZ_LOG_DB_DIR", "data/log_db")
LOG_DB_PREFIX = os.getenv("PATIENZ_LOG_DB_PREFIX", "session_logs")


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
def _connect_main():
    with _connect(MAIN_DB_PATH) as conn:
        yield conn


@contextmanager
def _connect_log(sid=None):
    with _connect(_log_db_path(sid)) as conn:
        yield conn


def _ensure_log_schema(conn):
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
        """
        CREATE INDEX IF NOT EXISTS idx_session_logs_sid_created
        ON session_logs (sid, created_at)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_session_logs_source_line
        ON session_logs (sid, source_file, line_no)
        WHERE source_file IS NOT NULL AND line_no IS NOT NULL
        """
    )


def _column_exists(conn, table_name, column_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def _ensure_column(conn, table_name, column_name, column_sql):
    if not _column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def init_db():
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
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_users_role_active
            ON users (role, is_active)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS progress_saves (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                save_name TEXT NOT NULL UNIQUE,
                sid TEXT,
                patient_name TEXT,
                progress_label TEXT,
                progress_index INTEGER,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _ensure_column(conn, "progress_saves", "user_id", "user_id INTEGER")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_progress_saves_sid
            ON progress_saves (sid)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_progress_saves_user_id
            ON progress_saves (user_id)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS grading_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_name TEXT NOT NULL UNIQUE,
                sid TEXT,
                patient_name TEXT,
                disease TEXT,
                score_v2_percentage REAL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _ensure_column(conn, "grading_results", "user_id", "user_id INTEGER")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_grading_results_sid
            ON grading_results (sid)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_grading_results_user_id
            ON grading_results (user_id)
            """
        )

    # Ensure current bucket exists for runtime log writes.
    with _connect_log() as conn:
        _ensure_log_schema(conn)


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


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
            ON CONFLICT(save_name) DO UPDATE SET
                user_id=excluded.user_id,
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
            rows = conn.execute(
                """
                SELECT save_name
                FROM progress_saves
                ORDER BY updated_at DESC, id DESC
                """
            ).fetchall()
        else:
            rows = conn.execute(
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
            row = conn.execute(
                """
                SELECT payload_json
                FROM progress_saves
                WHERE save_name = ?
                """,
                (save_name,),
            ).fetchone()
        else:
            row = conn.execute(
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
            conn.execute(
                """
                DELETE FROM progress_saves
                WHERE save_name = ?
                """,
                (save_name,),
            )
        else:
            conn.execute(
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
        conn.execute(
            """
            INSERT INTO grading_results (
                record_name, user_id, sid, patient_name, disease,
                score_v2_percentage, payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_name) DO UPDATE SET
                user_id=excluded.user_id,
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
        row = conn.execute(
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
        conn.execute(
            """
            INSERT INTO users (username, password_hash, role, is_active, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (username, password_hash, role, now, now),
        )


def upsert_user(username, password_hash, role="user", is_active=1):
    now = _now()
    with _connect_main() as conn:
        conn.execute(
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
        rows = conn.execute(
            """
            SELECT id, username, role, is_active, created_at, updated_at
            FROM users
            ORDER BY username ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def count_users():
    with _connect_main() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"])


def count_admin_users():
    with _connect_main() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM users
            WHERE role = 'admin' AND is_active = 1
            """
        ).fetchone()
    return int(row["n"])


def delete_user_by_username(username):
    with _connect_main() as conn:
        conn.execute(
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
        _ensure_log_schema(conn)
        if source_file is not None and line_no is not None:
            conn.execute(
                """
                INSERT OR IGNORE INTO session_logs (
                    sid, message, created_at, source_file, line_no, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (sid, message, created, source_file, line_no, ingested),
            )
            return

        conn.execute(
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
        _ensure_log_schema(conn)
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
