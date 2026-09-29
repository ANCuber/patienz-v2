"""Token revocation, login throttling, session re-check and user deletion."""
import threading

import pytest

from util import auth, db_store


def _reset(tmp_path, monkeypatch):
    db_store.MAIN_DB_PATH = str(tmp_path / "app.db")
    db_store.LOG_DB_DIR = str(tmp_path / "log_db")
    db_store.init_db()
    auth._failed_logins.clear()
    auth.ss.auth_user = None
    auth.ss.auth_checked_at = 0
    monkeypatch.setattr(auth.st, "query_params", {}, raising=False)


def _restore_from(token):
    auth.st.query_params[auth.AUTH_TOKEN_PARAM] = token
    auth.ss.auth_user = None
    auth._restore_auth_from_query()
    return auth.is_authenticated()


def test_logout_revokes_url_token(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    auth.create_user("student_1", "password123")
    ok, _ = auth.login("student_1", "password123")
    assert ok
    token = auth.st.query_params[auth.AUTH_TOKEN_PARAM]
    assert _restore_from(token)

    auth.logout()
    assert not _restore_from(token)


def test_password_change_revokes_url_token(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    auth.create_user("student_1", "password123")
    user = db_store.get_user_by_username("student_1")
    token = auth._auth_token(user["id"])
    assert _restore_from(token)

    auth.ss.auth_user = None
    auth.update_user("student_1", "student_1", "newpassword1", "user", True)
    assert not _restore_from(token)
    assert auth.login("student_1", "newpassword1")[0]


def test_deactivated_user_session_is_dropped_on_recheck(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    auth.create_user("admin_1", "password123", role="admin")
    auth.create_user("student_1", "password123")
    assert auth.login("student_1", "password123")[0]

    auth.ss.auth_user = {"id": db_store.get_user_by_username("admin_1")["id"],
                         "username": "admin_1", "role": "admin", "token_version": 0}
    auth.update_user("student_1", "student_1", "", "user", False)

    student = db_store.get_user_by_username("student_1")
    auth.ss.auth_user = {"id": student["id"], "username": "student_1", "role": "user", "token_version": 0}
    auth.ss.auth_checked_at = 0
    auth.init_auth()
    assert not auth.is_authenticated()


def test_login_lockout_after_repeated_failures(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    auth.create_user("student_1", "password123")
    for _ in range(auth.MAX_FAILED_LOGINS):
        ok, msg = auth.login("student_1", "wrong-password")
        assert not ok
        assert msg == "帳號或密碼錯誤"
    ok, msg = auth.login("student_1", "password123")
    assert not ok
    assert "登入失敗次數過多" in msg


def test_unknown_and_wrong_password_share_one_message(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    auth.create_user("student_1", "password123")
    assert auth.login("nobody", "password123")[1] == auth.login("student_1", "nope")[1]


def test_delete_user_removes_their_saves_and_results(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    auth.create_user("admin_1", "password123", role="admin")
    auth.create_user("student_1", "password123")
    uid = db_store.get_user_by_username("student_1")["id"]
    db_store.upsert_progress_save("s.json", "sid", "P", "問診", 1, {"a": 1}, user_id=uid)
    db_store.upsert_grading_result("g.json", "sid", "P", "D", 50.0, {"b": 2}, user_id=uid)

    auth.ss.auth_user = {"id": 999, "username": "admin_1", "role": "admin", "token_version": 0}
    ok, _ = auth.delete_user("student_1")
    assert ok
    assert db_store.get_user_by_username("student_1") is None
    assert db_store.list_progress_save_names(user_id=uid) == []


def test_concurrent_sessions_write_without_lock_errors(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    errors = []

    def session(i):
        try:
            sid = f"{i}-20260921000000-{i:04x}"
            for t in range(30):
                db_store.append_log(sid, f"turn {t}")
                if t % 10 == 0:
                    db_store.upsert_progress_save(f"s{i}-{t}.json", sid, "P", "問診", 1, {"t": t}, user_id=i)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=session, args=(i,)) for i in range(40)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    assert len(db_store.list_progress_save_names(user_id=3)) == 3


@pytest.mark.parametrize("sid, expected", [
    ("20250212110342", "202502"),
    ("3-20260909234520-9e53b6f6c5a9", "202609"),
    ("123456-20260101000000-ab", "202601"),
])
def test_log_bucket_from_sid(sid, expected):
    assert db_store._log_bucket_from_sid(sid) == expected
