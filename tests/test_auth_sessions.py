"""Token revocation, login throttling, session re-check and user deletion."""
import threading

import pytest

from util import auth, db_store


def _restore_from(token):
    auth.st.query_params[auth.AUTH_TOKEN_PARAM] = token
    auth.ss.auth_user = None
    auth._restore_auth_from_query()
    return auth.is_authenticated()


def _add_student(env, name="student_1"):
    env.act_as(env.root)
    assert auth.create_user(name, "password123")[0]
    env.act_as(None)
    return db_store.get_user_by_username(name)


def test_logout_revokes_url_token(env):
    _add_student(env)
    ok, _ = auth.login("student_1", "password123")
    assert ok
    token = auth.st.query_params[auth.AUTH_TOKEN_PARAM]
    assert _restore_from(token)

    auth.logout()
    assert not _restore_from(token)


def test_password_change_revokes_url_token(env):
    user = _add_student(env)
    token = auth._auth_token(user["id"])
    assert _restore_from(token)

    env.act_as(env.root)
    assert auth.update_user("student_1", "student_1", "newpassword1", "user", True)[0]
    assert not _restore_from(token)
    assert auth.login("student_1", "newpassword1")[0]


def test_deactivated_user_session_is_dropped_on_recheck(env):
    _add_student(env)
    assert auth.login("student_1", "password123")[0]

    env.act_as(env.root)
    assert auth.update_user("student_1", "student_1", "", "user", False)[0]

    env.act_as("student_1")
    auth.ss.auth_checked_at = 0
    auth.init_auth()
    assert not auth.is_authenticated()


def test_session_picks_up_role_and_group_changes_on_recheck(env):
    gid = env.make_group("cardio")
    _add_student(env)
    env.act_as(env.root)
    assert auth.update_user("student_1", "student_1", "", "group_admin", True, group_id=gid)[0]

    student = db_store.get_user_by_username("student_1")
    auth.ss.auth_user = {"id": student["id"], "username": "student_1", "role": "user",
                         "group_id": env.default_group, "token_version": 0}
    auth.ss.auth_checked_at = 0
    auth.init_auth()
    assert auth.is_group_admin()
    assert auth.current_group_id() == gid


def test_login_lockout_after_repeated_failures(env):
    _add_student(env)
    for _ in range(auth.MAX_FAILED_LOGINS):
        ok, msg = auth.login("student_1", "wrong-password")
        assert not ok
        assert msg == "帳號或密碼錯誤"
    ok, msg = auth.login("student_1", "password123")
    assert not ok
    assert "登入失敗次數過多" in msg


def test_unknown_and_wrong_password_share_one_message(env):
    _add_student(env)
    assert auth.login("nobody", "password123")[1] == auth.login("student_1", "nope")[1]


def test_delete_user_removes_their_saves_and_results(env):
    user = _add_student(env)
    uid, gid = user["id"], user["group_id"]
    db_store.upsert_progress_save(gid, uid, "s.json", "sid", "P", "問診", 1, {"a": 1})
    db_store.upsert_grading_result(gid, uid, "g.json", "sid", "P", "D", 50.0, {"b": 2})

    env.act_as(env.root)
    ok, _ = auth.delete_user("student_1")
    assert ok
    assert db_store.get_user_by_username("student_1") is None
    assert db_store.list_progress_save_names(gid, uid) == []
    assert db_store.list_grading_results(gid, uid) == []


def test_concurrent_sessions_write_without_lock_errors(env):
    groups = [env.default_group, env.make_group("g_a"), env.make_group("g_b")]
    errors = []

    def session(i):
        try:
            gid = groups[i % len(groups)]
            sid = f"{i}-20260921000000-{i:04x}"
            for t in range(30):
                db_store.append_log(gid, sid, f"turn {t}", user_id=i)
                if t % 10 == 0:
                    db_store.upsert_progress_save(gid, i, f"s{i}-{t}.json", sid, "P", "問診", 1, {"t": t})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=session, args=(i,)) for i in range(40)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    assert len(db_store.list_progress_save_names(groups[0], 3)) == 3
    assert sum(db_store.count_log_rows(g) for g in groups) == 40 * 30


@pytest.mark.parametrize("sid, expected", [
    ("20250212110342", "202502"),
    ("3-20260909234520-9e53b6f6c5a9", "202609"),
    ("123456-20260101000000-ab", "202601"),
])
def test_log_bucket_from_sid(sid, expected):
    assert db_store._log_bucket_from_sid(sid) == expected
