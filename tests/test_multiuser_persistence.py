import sqlite3

from util import db_store


def _reset_db(tmp_path):
    db_path = tmp_path / "app.db"
    log_dir = tmp_path / "log_db"
    db_store.MAIN_DB_PATH = str(db_path)
    db_store.LOG_DB_DIR = str(log_dir)
    db_store.LOG_DB_PREFIX = "session_logs"
    db_store.init_db()
    return db_path


def test_progress_save_is_user_scoped(tmp_path):
    _reset_db(tmp_path)

    db_store.upsert_progress_save(
        save_name="same-name.json",
        sid="sess-1",
        patient_name="Alice",
        progress_label="診斷",
        progress_index=0,
        payload={"user": "alice"},
        user_id=1,
    )
    db_store.upsert_progress_save(
        save_name="same-name.json",
        sid="sess-2",
        patient_name="Bob",
        progress_label="診斷",
        progress_index=0,
        payload={"user": "bob"},
        user_id=2,
    )

    assert db_store.get_progress_payload("same-name.json", user_id=1)["user"] == "alice"
    assert db_store.get_progress_payload("same-name.json", user_id=2)["user"] == "bob"
    assert sorted(db_store.list_progress_save_names(user_id=1)) == ["same-name.json"]
    assert sorted(db_store.list_progress_save_names(user_id=2)) == ["same-name.json"]


def test_grading_result_is_user_scoped(tmp_path):
    _reset_db(tmp_path)

    db_store.upsert_grading_result(
        record_name="same-record.json",
        sid="sess-1",
        patient_name="Alice",
        disease="Disease A",
        score_v2_percentage=80,
        payload={"score": 80},
        user_id=1,
    )
    db_store.upsert_grading_result(
        record_name="same-record.json",
        sid="sess-2",
        patient_name="Bob",
        disease="Disease B",
        score_v2_percentage=90,
        payload={"score": 90},
        user_id=2,
    )

    conn = sqlite3.connect(str(tmp_path / "app.db"))
    rows = conn.execute(
        "SELECT user_id, patient_name, disease, score_v2_percentage FROM grading_results ORDER BY user_id"
    ).fetchall()
    conn.close()

    assert rows == [
        (1, "Alice", "Disease A", 80.0),
        (2, "Bob", "Disease B", 90.0),
    ]
