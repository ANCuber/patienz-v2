"""Root / group admin / user permissions, per-group databases and the
migration from the pre-group layout."""
import os
import sqlite3

import pytest

from util import access, admin_data, auth, db_store


@pytest.fixture
def two_groups(env):
    """Groups A and B, each with an admin and one ordinary user."""
    env.a = env.make_group("A", admin="admin_a", members=["alice"])
    env.b = env.make_group("B", admin="admin_b", members=["bob"])
    env.act_as(None)
    return env


# --------------------------------------------------------------------------
# User management
# --------------------------------------------------------------------------

def test_group_admin_creates_only_ordinary_users_in_own_group(two_groups):
    env = two_groups
    env.act_as("admin_a")

    assert auth.create_user("carol", "password123")[0]
    assert env.user("carol")["group_id"] == env.a
    assert env.user("carol")["role"] == "user"

    assert auth.create_user("dave", "password123", group_id=env.b) == (False, "權限不足")
    assert auth.create_user("dave", "password123", role="group_admin") == (False, "權限不足")
    assert auth.create_user("dave", "password123", role="root") == (False, "權限不足")
    assert env.user("dave") is None


def test_group_admin_manages_own_users_but_nothing_outside(two_groups):
    env = two_groups
    env.act_as("admin_a")

    assert [u["username"] for u in auth.list_users()] == ["admin_a", "alice"]
    assert [g["name"] for g in auth.list_groups()] == ["A"]

    assert auth.update_user("alice", "alice2", "newpassword1", "user", True)[0]
    assert env.user("alice2") is not None

    for target in ("bob", "admin_b", env.root, "no_such_user"):
        assert auth.update_user(target, target, "hackedpass1", "user", True) == (False, "權限不足")
        assert auth.delete_user(target) == (False, "權限不足")
    assert auth.login("bob", "password123")[0]  # password untouched

    # Cannot promote, or push a user into another group.
    env.act_as("admin_a")
    assert auth.update_user("alice2", "alice2", "", "group_admin", True) == (False, "權限不足")
    assert auth.update_user("alice2", "alice2", "", "user", True, group_id=env.b) == (False, "權限不足")
    assert env.user("alice2")["group_id"] == env.a

    assert auth.delete_user("alice2") == (True, "已刪除使用者")


def test_group_admin_may_edit_own_account_but_not_escalate(two_groups):
    env = two_groups
    env.act_as("admin_a")

    assert auth.update_user("admin_a", "admin_a", "newpassword1", "group_admin", True)[0]
    assert auth.update_user("admin_a", "admin_a", "", "root", True)[0] is False
    assert auth.update_user("admin_a", "admin_a", "", "group_admin", True, group_id=env.b) == (False, "權限不足")
    assert auth.update_user("admin_a", "admin_a", "", "group_admin", False) == (False, "不可停用目前登入中的帳號")
    assert auth.delete_user("admin_a") == (False, "不可刪除目前登入中的帳號")


def test_ordinary_user_and_anonymous_have_no_admin_rights(two_groups):
    env = two_groups
    for who in ("alice", None):
        env.act_as(who)
        assert auth.list_users() == []
        assert auth.list_groups() == []
        assert auth.create_user("eve", "password123") == (False, "權限不足")
        assert auth.update_user("alice", "alice", "hackedpass1", "user", True) == (False, "權限不足")
        assert auth.delete_user("bob") == (False, "權限不足")
        assert auth.create_group("C") == (False, "權限不足")
        assert auth.delete_group(env.b) == (False, "權限不足")
        assert not auth.is_admin()


def test_one_group_admin_per_group(two_groups):
    env = two_groups
    env.act_as(env.root)

    assert auth.create_user("admin_a2", "password123", role="group_admin", group_id=env.a) == (
        False, "該群組已有群組管理員")
    assert auth.update_user("alice", "alice", "", "group_admin", True) == (False, "該群組已有群組管理員")
    # The database refuses it too, independent of the checks in auth.
    with pytest.raises(sqlite3.IntegrityError):
        db_store.create_user("sneaky", "x", role="group_admin", group_id=env.a)
    with pytest.raises(sqlite3.IntegrityError):
        db_store.create_user("root2", "x", role="root", group_id=None)

    # Swap: demote the current admin, then promote another member.
    assert auth.update_user("admin_a", "admin_a", "", "user", True)[0]
    assert auth.update_user("alice", "alice", "", "group_admin", True)[0]
    assert db_store.get_group_admin(env.a)["username"] == "alice"


def test_root_manages_groups(two_groups):
    env = two_groups
    env.act_as(env.root)

    assert auth.create_group("A") == (False, "群組名稱已存在")
    assert auth.create_group("  ") == (False, "群組名稱不可為空")
    assert auth.rename_group(env.a, "B") == (False, "群組名稱已存在")
    assert auth.rename_group(env.a, "Alpha")[0]

    assert auth.delete_group(env.default_group) == (False, "不可刪除預設群組")
    assert auth.delete_group(env.a) == (False, "群組內仍有使用者，請先移出或刪除")

    # A group may exist without an admin; an emptied group can be deleted and
    # its database directory is kept under a new name.
    assert auth.create_group("empty")[0]
    empty = db_store.get_group_by_name("empty")
    assert empty["admin_username"] is None
    db_store.upsert_progress_save(empty["id"], 0, "x.json", "s", "P", "問診", 1, {})
    assert auth.delete_group(empty["id"]) == (True, "已刪除群組")
    assert not os.path.exists(db_store.group_dir(empty["id"]))
    assert any(".deleted-" in name for name in os.listdir(db_store._group_root()))

    names = {g["name"]: g for g in auth.list_groups()}
    assert set(names) == {"default", "Alpha", "B"}
    assert names["Alpha"]["member_count"] == 2
    assert names["Alpha"]["admin_username"] == "admin_a"


# --------------------------------------------------------------------------
# Data isolation
# --------------------------------------------------------------------------

def test_each_group_has_its_own_database_files(two_groups):
    env = two_groups
    alice, bob = env.user("alice"), env.user("bob")
    db_store.upsert_progress_save(env.a, alice["id"], "s.json", "sid-a", "PA", "問診", 1, {"g": "a"})
    db_store.upsert_progress_save(env.b, bob["id"], "s.json", "sid-b", "PB", "問診", 1, {"g": "b"})
    db_store.append_log(env.a, f"{alice['id']}-20260101000000-ab", "hello from a", user_id=alice["id"])

    path_a, path_b = db_store._group_db_path(env.a), db_store._group_db_path(env.b)
    assert path_a != path_b and os.path.isfile(path_a) and os.path.isfile(path_b)
    assert os.path.dirname(path_a) != os.path.dirname(path_b)

    # Nothing of group A is reachable through group B's id, and vice versa.
    assert db_store.get_progress_payload(env.b, alice["id"], "s.json") is None
    assert [r["sid"] for r in db_store.list_progress_saves(env.a)] == ["sid-a"]
    assert [r["sid"] for r in db_store.list_progress_saves(env.b)] == ["sid-b"]
    assert db_store.count_log_rows(env.a) == 1
    assert db_store.count_log_rows(env.b) == 0

    # The system database holds no user data at all.
    conn = sqlite3.connect(db_store.MAIN_DB_PATH)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"users", "user_groups", "app_meta"} <= tables
    assert not {"progress_saves", "grading_results", "session_logs"} & tables


def test_admin_data_enforces_group_boundaries(two_groups):
    env = two_groups
    alice, bob = env.user("alice"), env.user("bob")
    db_store.upsert_grading_result(env.a, alice["id"], "g.json", "s", "PA", "D", 70.0, {"g": "a"})
    db_store.upsert_grading_result(env.b, bob["id"], "g.json", "s", "PB", "D", 80.0, {"g": "b"})

    env.act_as("admin_a")
    assert [s[1] for s in admin_data.accessible_scopes()] == ["A"]
    assert [r["username"] for r in admin_data.list_grading_results(env.a)] == ["alice"]
    assert admin_data.get_grading_payload(env.a, alice["id"], "g.json") == {"g": "a"}
    for forbidden in (env.b, env.default_group, None):
        with pytest.raises(admin_data.PermissionDenied):
            admin_data.list_grading_results(forbidden)
        with pytest.raises(admin_data.PermissionDenied):
            admin_data.get_grading_payload(forbidden, bob["id"], "g.json")
        with pytest.raises(admin_data.PermissionDenied):
            admin_data.delete_grading_result(forbidden, bob["id"], "g.json")
        with pytest.raises(admin_data.PermissionDenied):
            admin_data.count_log_rows(forbidden)
    assert admin_data.delete_grading_result(env.a, alice["id"], "g.json") is True

    for who in ("alice", None):  # ordinary users get nothing, not even their own group
        env.act_as(who)
        assert admin_data.accessible_scopes() == []
        with pytest.raises(admin_data.PermissionDenied):
            admin_data.list_grading_results(env.a)

    env.act_as(env.root)
    assert [s[0] for s in admin_data.accessible_scopes()] == [env.default_group, env.a, env.b, None]
    assert admin_data.get_grading_payload(env.b, bob["id"], "g.json") == {"g": "b"}
    assert admin_data.list_grading_results(None) == []


def test_access_policy_table():
    root = {"id": 1, "role": "root", "group_id": None}
    admin_a = {"id": 2, "role": "group_admin", "group_id": 10}
    user_a = {"id": 3, "role": "user", "group_id": 10}
    admin_b = {"id": 4, "role": "group_admin", "group_id": 20}
    user_b = {"id": 5, "role": "user", "group_id": 20}

    assert all(access.can_access_group(root, g) for g in (10, 20, None))
    assert access.can_access_group(admin_a, 10)
    assert not access.can_access_group(admin_a, 20)
    assert not access.can_access_group(admin_a, None)
    assert not access.can_access_group(user_a, 10)
    assert not access.can_access_group(None, 10)

    assert access.can_manage_user(admin_a, user_a)
    for target in (user_b, admin_b, root, admin_a):
        assert not access.can_manage_user(admin_a, target)
    assert not access.can_manage_user(user_a, user_a)
    assert all(access.can_manage_user(root, t) for t in (admin_a, user_a, admin_b, user_b))

    assert access.can_access_user_data(user_a, 3, 10)           # own data
    assert not access.can_access_user_data(user_a, 2, 10)       # a peer's data
    assert access.can_access_user_data(admin_a, 3, 10)          # own group
    assert not access.can_access_user_data(admin_a, 5, 20)      # other group
    assert access.can_access_user_data(root, 5, 20)


def test_moving_a_user_carries_their_data_to_the_new_group(two_groups):
    env = two_groups
    alice = env.user("alice")
    sid = f"{alice['id']}-20260101000000-ab"
    db_store.upsert_progress_save(env.a, alice["id"], "s.json", sid, "P", "問診", 1, {"k": 1})
    db_store.upsert_grading_result(env.a, alice["id"], "g.json", sid, "P", "D", 60.0, {"k": 2})
    db_store.append_log(env.a, sid, "line 1", user_id=alice["id"])
    db_store.append_log(env.a, "999-20260101000000-ff", "someone else", user_id=999)

    env.act_as(env.root)
    assert auth.update_user("alice", "alice", "", "user", True, group_id=env.b) == (True, "使用者資料已更新")

    assert env.user("alice")["group_id"] == env.b
    assert db_store.get_progress_payload(env.b, alice["id"], "s.json") == {"k": 1}
    assert db_store.get_grading_payload(env.b, alice["id"], "g.json") == {"k": 2}
    assert db_store.list_progress_saves(env.a) == []
    assert db_store.list_grading_results(env.a) == []
    assert db_store.count_log_rows(env.b, alice["id"]) == 1
    assert db_store.count_log_rows(env.a) == 1  # the other user's line stays


# --------------------------------------------------------------------------
# Migration from the pre-group layout
# --------------------------------------------------------------------------

def _build_legacy_layout(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "app.db"))
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user',
            is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            token_version INTEGER NOT NULL DEFAULT 0);
        INSERT INTO users (username, password_hash, role, created_at, updated_at) VALUES
            ('old_admin', 'x', 'admin', 't', 't'),
            ('admin', 'x', 'admin', 't', 't'),
            ('student', 'x', 'user', 't', 't');
        CREATE TABLE progress_saves (
            id INTEGER PRIMARY KEY AUTOINCREMENT, save_name TEXT NOT NULL, sid TEXT, patient_name TEXT,
            progress_label TEXT, progress_index INTEGER, payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, user_id INTEGER);
        INSERT INTO progress_saves (save_name, payload_json, created_at, updated_at, user_id) VALUES
            ('root.json', '{"o": "root"}', 't', 't', 2),
            ('student.json', '{"o": "student"}', 't', 't', 3),
            ('orphan.json', '{"o": "nobody"}', 't', 't', NULL);
        CREATE TABLE grading_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT, record_name TEXT NOT NULL, sid TEXT, patient_name TEXT,
            disease TEXT, score_v2_percentage REAL, payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, user_id INTEGER);
        INSERT INTO grading_results (record_name, payload_json, created_at, updated_at, user_id) VALUES
            ('g.json', '{"s": 1}', 't', 't', 3);
        """
    )
    conn.commit()
    conn.close()

    os.makedirs(tmp_path / "log_db")
    conn = sqlite3.connect(str(tmp_path / "log_db" / "session_logs_202609.db"))
    conn.executescript(
        """
        CREATE TABLE session_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT NOT NULL, message TEXT NOT NULL,
            created_at TEXT NOT NULL, source_file TEXT, line_no INTEGER, ingested_at TEXT NOT NULL);
        INSERT INTO session_logs (sid, message, created_at, ingested_at) VALUES
            ('2-20260909233609-aa', 'root line', 't', 't'),
            ('3-20260909233609-bb', 'student line', 't', 't'),
            ('20250212110342', 'legacy line', 't', 't');
        """
    )
    conn.commit()
    conn.close()


def test_legacy_layout_is_migrated_into_root_and_default_group(tmp_path, monkeypatch):
    _build_legacy_layout(tmp_path)
    monkeypatch.setattr(db_store, "MAIN_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setattr(db_store, "GROUP_DB_DIR", None)
    monkeypatch.setattr(db_store, "LOG_DB_DIR", str(tmp_path / "log_db"))
    monkeypatch.setattr(db_store, "LOG_DB_PREFIX", "session_logs")
    monkeypatch.delenv("PATIENZ_ADMIN_USERNAME", raising=False)

    db_store.init_db()
    default = db_store.default_group_id()

    # The admin named like PATIENZ_ADMIN_USERNAME wins; every other account,
    # including the second admin, is an ordinary user of the default group.
    users = {u["username"]: u for u in db_store.list_users()}
    assert users["admin"]["role"] == "root" and users["admin"]["group_id"] is None
    assert users["old_admin"]["role"] == "user" and users["old_admin"]["group_id"] == default
    assert users["student"]["role"] == "user" and users["student"]["group_name"] == "default"

    assert db_store.get_progress_payload(None, 2, "root.json") == {"o": "root"}
    assert db_store.get_progress_payload(default, 3, "student.json") == {"o": "student"}
    assert db_store.get_progress_payload(default, db_store.UNOWNED_USER_ID, "orphan.json") == {"o": "nobody"}
    assert db_store.get_grading_payload(default, 3, "g.json") == {"s": 1}
    assert db_store.count_log_rows(None) == 1
    assert db_store.count_log_rows(default) == 2
    assert db_store.count_log_rows(default, 3) == 1

    # Old tables are kept as backups; a second start does not copy twice.
    conn = sqlite3.connect(str(tmp_path / "app.db"))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert conn.execute("SELECT COUNT(*) FROM progress_saves_pre_groups").fetchone()[0] == 3
    conn.close()
    assert "progress_saves" not in tables and "grading_results_pre_groups" in tables

    monkeypatch.setattr(db_store, "_INITIALIZED_FOR", None)
    db_store.init_db()
    assert db_store.count_log_rows(default) == 2
    assert len(db_store.list_progress_saves(default)) == 2
    assert [u["role"] for u in db_store.list_users()].count("root") == 1
