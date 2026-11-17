import sqlite3

from util import db_store


def test_progress_save_is_user_scoped(env):
    gid = env.default_group
    db_store.upsert_progress_save(gid, 1, "same-name.json", "sess-1", "Alice", "診斷", 0, {"user": "alice"})
    db_store.upsert_progress_save(gid, 2, "same-name.json", "sess-2", "Bob", "診斷", 0, {"user": "bob"})

    assert db_store.get_progress_payload(gid, 1, "same-name.json")["user"] == "alice"
    assert db_store.get_progress_payload(gid, 2, "same-name.json")["user"] == "bob"
    assert db_store.list_progress_save_names(gid, 1) == ["same-name.json"]
    assert db_store.list_progress_save_names(gid, 2) == ["same-name.json"]
    assert len(db_store.list_progress_saves(gid)) == 2


def test_grading_result_is_user_scoped(env):
    gid = env.default_group
    db_store.upsert_grading_result(gid, 1, "same-record.json", "sess-1", "Alice", "Disease A", 80, {"score": 80})
    db_store.upsert_grading_result(gid, 2, "same-record.json", "sess-2", "Bob", "Disease B", 90, {"score": 90})

    conn = sqlite3.connect(db_store._group_db_path(gid))
    rows = conn.execute(
        "SELECT user_id, patient_name, disease, score_v2_percentage FROM grading_results ORDER BY user_id"
    ).fetchall()
    conn.close()

    assert rows == [
        (1, "Alice", "Disease A", 80.0),
        (2, "Bob", "Disease B", 90.0),
    ]


def test_upsert_overwrites_the_same_users_row(env):
    gid = env.default_group
    db_store.upsert_progress_save(gid, 1, "a.json", "s", "P", "問診", 1, {"v": 1})
    db_store.upsert_progress_save(gid, 1, "a.json", "s", "P", "問診", 1, {"v": 2})

    assert db_store.get_progress_payload(gid, 1, "a.json") == {"v": 2}
    assert len(db_store.list_progress_saves(gid)) == 1
