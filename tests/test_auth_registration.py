import time

import pytest

from util import auth, db_store


def test_register_user_creates_active_user_in_default_group(env):
    ok, message = auth.register_user("student_1", "password123", "password123")

    assert ok is True
    assert message == "已新增使用者"
    user = db_store.get_user_by_username("student_1")
    assert user["role"] == "user"
    assert user["group_id"] == env.default_group
    assert user["is_active"] == 1
    assert auth._verify_password("password123", user["password_hash"])


@pytest.mark.parametrize(
    "username, password, confirmation, expected",
    [
        ("ab", "password123", "password123", "帳號需為 3-32 碼，且只能包含英數字、底線、句點或連字號"),
        ("bad name", "password123", "password123", "帳號需為 3-32 碼，且只能包含英數字、底線、句點或連字號"),
        ("student_1", "password123", "different", "兩次輸入的密碼不一致"),
        ("student_1", "short", "short", "密碼至少需要 8 個字元"),
    ],
)
def test_register_user_validates_input(env, username, password, confirmation, expected):
    ok, message = auth.register_user(username, password, confirmation)

    assert ok is False
    assert message == expected


def test_register_user_rejects_duplicate_username(env):
    auth.register_user("student_1", "password123", "password123")

    ok, message = auth.register_user("student_1", "password456", "password456")

    assert ok is False
    assert message == "帳號已存在"


def test_bootstrap_creates_exactly_one_root_without_a_group(env):
    root = db_store.get_root_user()
    assert root["username"] == env.root
    assert root["group_id"] is None
    assert [u["role"] for u in db_store.list_users()] == ["root"]


def test_root_updates_profile_password_and_role(env):
    env.act_as(env.root)
    auth.create_user("student_1", "password123", role="user")

    ok, message = auth.update_user("student_1", "student_2", "newpassword", "group_admin", True)

    assert ok is True
    assert message == "使用者資料已更新"
    assert db_store.get_user_by_username("student_1") is None
    user = db_store.get_user_by_username("student_2")
    assert user["role"] == "group_admin"
    assert user["group_id"] == env.default_group
    assert auth._verify_password("newpassword", user["password_hash"])


def test_root_account_cannot_be_demoted_disabled_deleted_or_duplicated(env):
    env.act_as(env.root)
    auth.create_user("student_1", "password123")

    assert auth.update_user(env.root, env.root, "", "user", True) == (False, "Root 管理員不可降級或停用")
    assert auth.update_user(env.root, env.root, "", "root", False) == (False, "Root 管理員不可降級或停用")
    assert auth.delete_user(env.root) == (False, "不可刪除目前登入中的帳號")
    assert auth.update_user("student_1", "student_1", "", "root", True) == (False, "系統只能有一位 Root 管理員")
    assert auth.create_user("root_2", "password123", role="root")[0] is False

    # Root may still rename itself and change its own password.
    assert auth.update_user(env.root, "boss", "newpassword1", "root", True)[0]
    assert db_store.get_root_user()["username"] == "boss"
    assert auth.current_username() == "boss"


def test_auth_token_round_trip_and_expiry(env, monkeypatch):
    env.act_as(env.root)
    auth.create_user("student_1", "password123", role="user")
    user = db_store.get_user_by_username("student_1")
    time_now = time.time()
    token = auth._auth_token(user["id"])

    auth.st.query_params[auth.AUTH_TOKEN_PARAM] = token
    auth.ss.auth_user = None
    auth._restore_auth_from_query()
    assert auth.current_username() == "student_1"
    assert auth.current_group_id() == env.default_group

    monkeypatch.setattr(auth.time, "time", lambda: time_now + auth.AUTH_TOKEN_TTL + 1)
    auth.st.query_params[auth.AUTH_TOKEN_PARAM] = token
    auth.ss.auth_user = None
    auth._restore_auth_from_query()
    assert auth.is_authenticated() is False
