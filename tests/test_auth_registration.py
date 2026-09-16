import pytest
import time

from util import auth, db_store


def _reset_db(tmp_path):
    db_store.MAIN_DB_PATH = str(tmp_path / "app.db")
    db_store.LOG_DB_DIR = str(tmp_path / "log_db")
    db_store.init_db()


def test_register_user_creates_active_user_with_user_role(tmp_path):
    _reset_db(tmp_path)

    ok, message = auth.register_user("student_1", "password123", "password123")

    assert ok is True
    assert message == "已新增使用者"
    user = db_store.get_user_by_username("student_1")
    assert user["role"] == "user"
    assert user["is_active"] == 1
    assert auth._verify_password("password123", user["password_hash"])


@pytest.mark.parametrize(
    "username, password, confirmation, expected",
    [
        ("ab", "password123", "password123", "帳號需為 3-32 碼，且只能包含英數字、底線、句點或連字號"),
        ("bad name", "password123", "password123", "帳號需為 3-32 碼，且只能包含英數字、底線、句點或連字號"),
        ("student_1", "password123", "different", "兩次輸入的密碼不一致"),
    ],
)
def test_register_user_validates_input(tmp_path, username, password, confirmation, expected):
    _reset_db(tmp_path)

    ok, message = auth.register_user(username, password, confirmation)

    assert ok is False
    assert message == expected


def test_register_user_rejects_duplicate_username(tmp_path):
    _reset_db(tmp_path)
    auth.register_user("student_1", "password123", "password123")

    ok, message = auth.register_user("student_1", "password456", "password456")

    assert ok is False
    assert message == "帳號已存在"


def test_update_user_changes_profile_and_password(tmp_path):
    _reset_db(tmp_path)
    auth.create_user("student_1", "password123", role="user")

    ok, message = auth.update_user(
        "student_1", "student_2", "newpassword", "admin", True
    )

    assert ok is True
    assert message == "使用者資料已更新"
    assert db_store.get_user_by_username("student_1") is None
    user = db_store.get_user_by_username("student_2")
    assert user["role"] == "admin"
    assert auth._verify_password("newpassword", user["password_hash"])


def test_update_user_protects_last_admin_and_current_account(tmp_path):
    _reset_db(tmp_path)
    auth.create_user("admin_1", "password123", role="admin")
    auth.create_user("student_1", "password123", role="user")
    auth.ss.auth_user = {
        "id": db_store.get_user_by_username("admin_1")["id"],
        "username": "admin_1",
        "role": "admin",
    }

    ok, message = auth.update_user("admin_1", "admin_1", "", "user", True)
    assert ok is False
    assert message == "系統至少需要保留一位啟用中的 admin"

    ok, message = auth.update_user("admin_1", "admin_1", "", "admin", False)
    assert ok is False
    assert message == "不可停用目前登入中的帳號"


def test_auth_token_round_trip_and_expiry(tmp_path, monkeypatch):
    _reset_db(tmp_path)
    auth.create_user("student_1", "password123", role="user")
    user = db_store.get_user_by_username("student_1")
    time_now = time.time()
    token = auth._auth_token(user["id"])

    class QueryParams(dict):
        pass

    query_params = QueryParams({auth.AUTH_TOKEN_PARAM: token})
    monkeypatch.setattr(auth.st, "query_params", query_params, raising=False)
    auth.ss.auth_user = None
    auth._restore_auth_from_query()
    assert auth.current_username() == "student_1"

    monkeypatch.setattr(auth.time, "time", lambda: time_now + auth.AUTH_TOKEN_TTL + 1)
    auth.ss.auth_user = None
    auth._restore_auth_from_query()
    assert auth.is_authenticated() is False