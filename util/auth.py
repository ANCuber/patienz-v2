"""Authentication and user management.

Sessions live in Streamlit session state. Because a browser refresh clears that
state, a signed token is also placed in the URL query string so the user is
restored on reload. Tokens carry ``user_id:expires_at:token_version`` and are
verified against the database, so they stop working when the user logs out,
changes password, is deactivated or is deleted.

Signing secret: ``PATIENZ_AUTH_SECRET``; if unset, a random secret is generated
once and persisted next to the database file.
"""
import base64
import binascii
import hashlib
import hmac
import os
import re
import secrets
import threading
import time

import streamlit as st

import util.access as access
import util.db_store as db_store

ss = st.session_state

DEFAULT_ADMIN_USERNAME = os.getenv("PATIENZ_ADMIN_USERNAME", "admin")
# No built-in default: if unset, a random password is generated on first
# bootstrap and printed once to the server log.
DEFAULT_ADMIN_PASSWORD = os.getenv("PATIENZ_ADMIN_PASSWORD") or None
ALLOW_REGISTRATION = os.getenv("PATIENZ_ALLOW_REGISTRATION", "1") not in ("0", "false", "no")
AUTH_TOKEN_PARAM = "patienz_auth"
AUTH_TOKEN_TTL = int(os.getenv("PATIENZ_AUTH_TOKEN_TTL", str(30 * 24 * 60 * 60)))
# How often (seconds) a live session re-checks its user row (deactivated? revoked?).
SESSION_RECHECK_SECONDS = int(os.getenv("PATIENZ_SESSION_RECHECK", "60"))
# Login throttling: after MAX_FAILED_LOGINS failures the account is locked for LOCKOUT_SECONDS.
MAX_FAILED_LOGINS = int(os.getenv("PATIENZ_MAX_FAILED_LOGINS", "5"))
LOCKOUT_SECONDS = int(os.getenv("PATIENZ_LOGIN_LOCKOUT", str(15 * 60)))
PBKDF2_ITERATIONS = 200_000

_secret_cache = {}
_secret_lock = threading.Lock()
_failed_logins = {}          # username -> [failure_count, first_failure_ts, locked_until_ts]
_failed_logins_lock = threading.Lock()
_bootstrapped_for = None
_bootstrap_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Secrets & hashing
# ---------------------------------------------------------------------------

def _secret_file():
    return os.path.join(os.path.dirname(os.path.abspath(db_store.MAIN_DB_PATH)), ".auth_secret")


def _auth_secret():
    env = os.getenv("PATIENZ_AUTH_SECRET")
    if env:
        return env
    path = _secret_file()
    with _secret_lock:
        cached = _secret_cache.get(path)
        if cached:
            return cached
        try:
            with open(path, "r", encoding="utf-8") as fh:
                secret = fh.read().strip()
        except FileNotFoundError:
            secret = ""
        if not secret:
            secret = secrets.token_urlsafe(48)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(secret)
        _secret_cache[path] = secret
        return secret


def _hash_password(password, salt=None):
    salt_bytes = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, PBKDF2_ITERATIONS)
    return f"{salt_bytes.hex()}${digest.hex()}"


def _verify_password(password, stored):
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt_bytes = bytes.fromhex(salt_hex)
    except Exception:
        return False
    candidate = _hash_password(password, salt=salt_bytes).split("$", 1)[1]
    return hmac.compare_digest(candidate, digest_hex)


# ---------------------------------------------------------------------------
# URL login token
# ---------------------------------------------------------------------------

def _sign(encoded):
    return hmac.new(_auth_secret().encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()


def _auth_token(user_id, token_version=None):
    if token_version is None:
        user = db_store.get_user_by_id(user_id) or {}
        token_version = int(user.get("token_version", 0))
    expires_at = int(time.time()) + AUTH_TOKEN_TTL
    payload = f"{user_id}:{expires_at}:{token_version}".encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{encoded}.{_sign(encoded)}"


def _query_token():
    query_params = getattr(st, "query_params", None)
    if query_params is None:
        return None
    token = query_params.get(AUTH_TOKEN_PARAM)
    if isinstance(token, list):
        token = token[0] if token else None
    return token if isinstance(token, str) else None


def _set_query_token(token):
    query_params = getattr(st, "query_params", None)
    if query_params is None:
        return
    if token:
        query_params[AUTH_TOKEN_PARAM] = token
    else:
        try:
            del query_params[AUTH_TOKEN_PARAM]
        except KeyError:
            pass


def _session_user_dict(user):
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "group_id": user.get("group_id"),
        "token_version": int(user.get("token_version", 0)),
    }


def _restore_auth_from_query():
    token = _query_token()
    if not token or "." not in token:
        return
    encoded, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(signature, _sign(encoded)):
        _set_query_token(None)
        return
    user = None
    try:
        padding = "=" * (-len(encoded) % 4)
        parts = base64.urlsafe_b64decode((encoded + padding).encode("ascii")).decode("utf-8").split(":")
        if len(parts) != 3:
            raise ValueError("malformed token")
        user_id_text, expires_text, version_text = parts
        if int(expires_text) <= int(time.time()):
            raise ValueError("expired token")
        user = db_store.get_user_by_id(int(user_id_text))
        if user and int(user.get("token_version", 0)) != int(version_text):
            user = None  # revoked by logout / password change
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
        user = None

    if not user or int(user.get("is_active", 0)) != 1:
        _set_query_token(None)
        return
    ss.auth_user = _session_user_dict(user)
    ss.auth_checked_at = time.time()


def _recheck_session_user():
    """Drop a live session whose user was deactivated, deleted or revoked since
    the last check. Runs at most once per SESSION_RECHECK_SECONDS."""
    now = time.time()
    if now - ss.get("auth_checked_at", 0) < SESSION_RECHECK_SECONDS:
        return
    user = db_store.get_user_by_id(current_user_id())
    if (
        not user
        or int(user.get("is_active", 0)) != 1
        or int(user.get("token_version", 0)) != int(ss.auth_user.get("token_version", 0))
    ):
        ss.auth_user = None
        _set_query_token(None)
        return
    ss.auth_user = _session_user_dict(user)
    ss.auth_checked_at = now


# ---------------------------------------------------------------------------
# Bootstrap / session helpers
# ---------------------------------------------------------------------------

def _bootstrap_root_admin():
    """Make sure the single root admin exists (new database, or an old one
    that had no admin to promote)."""
    global _bootstrapped_for
    target = os.path.abspath(db_store.MAIN_DB_PATH)
    if _bootstrapped_for == target:
        return
    with _bootstrap_lock:
        if _bootstrapped_for == target:
            return
        if db_store.get_root_user() is None:
            username = DEFAULT_ADMIN_USERNAME
            suffix = 0
            while db_store.get_user_by_username(username):
                suffix += 1
                username = f"{DEFAULT_ADMIN_USERNAME}_root{suffix if suffix > 1 else ''}"
            password = DEFAULT_ADMIN_PASSWORD
            generated = password is None
            if generated:
                password = secrets.token_urlsafe(12)
            db_store.create_user(
                username=username,
                password_hash=_hash_password(password),
                role=access.ROLE_ROOT,
                group_id=None,
            )
            if generated:
                print(
                    f"[AUTH] Created root admin '{username}' with a generated password: {password}\n"
                    "[AUTH] This is printed only once. Log in and change it from the admin page."
                )
            else:
                print(f"[AUTH] Created root admin '{username}' from PATIENZ_ADMIN_PASSWORD.")
        _bootstrapped_for = target


def init_auth():
    db_store.init_db()
    _bootstrap_root_admin()
    if "auth_user" not in ss:
        ss.auth_user = None
    if is_authenticated():
        _recheck_session_user()
    else:
        _restore_auth_from_query()


def is_authenticated():
    user = ss.get("auth_user")
    return bool(user and user.get("id"))


def current_user_id():
    user = ss.get("auth_user") or {}
    return user.get("id")


def current_username():
    user = ss.get("auth_user") or {}
    return user.get("username")


def safe_user_segment(username):
    """Filesystem-safe directory name for a user. Single source of truth for the
    per-user folders under data/log, data/save and data/grading_results."""
    allowed = []
    for ch in (username or "anonymous"):
        if ch.isalnum() or ch in ("-", "_"):
            allowed.append(ch)
    text = "".join(allowed).strip("_")
    return text or "anonymous"


def current_role():
    user = ss.get("auth_user") or {}
    return user.get("role")


def current_group_id():
    """Group whose databases hold the current user's data (None for root)."""
    user = ss.get("auth_user") or {}
    return user.get("group_id")


def is_root():
    return access.is_root(ss.get("auth_user"))


def is_group_admin():
    return access.is_group_admin(ss.get("auth_user"))


def is_admin():
    """Root or group admin: may open the admin page."""
    return access.can_administer(ss.get("auth_user"))


def role_label(role=None):
    role = role or current_role()
    return access.ROLE_LABELS.get(role, role or "")


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------

def _lockout_remaining(username):
    now = time.time()
    with _failed_logins_lock:
        entry = _failed_logins.get(username)
        if not entry:
            return 0
        count, first_ts, locked_until = entry
        if locked_until and locked_until > now:
            return int(locked_until - now)
        if locked_until and locked_until <= now:
            del _failed_logins[username]
        return 0


def _note_failed_login(username):
    now = time.time()
    with _failed_logins_lock:
        count, first_ts, locked_until = _failed_logins.get(username, (0, now, 0))
        if now - first_ts > LOCKOUT_SECONDS:
            count, first_ts = 0, now
        count += 1
        if count >= MAX_FAILED_LOGINS:
            locked_until = now + LOCKOUT_SECONDS
        _failed_logins[username] = [count, first_ts, locked_until]


def _clear_failed_logins(username):
    with _failed_logins_lock:
        _failed_logins.pop(username, None)


def login(username, password):
    username = (username or "").strip()
    if not username or not password:
        return False, "請輸入帳號與密碼"

    remaining = _lockout_remaining(username)
    if remaining:
        return False, f"登入失敗次數過多，請於 {max(1, remaining // 60)} 分鐘後再試"

    user = db_store.get_user_by_username(username)
    # Same message for unknown user, disabled user and wrong password so the
    # login form does not reveal which accounts exist.
    if not user or int(user.get("is_active", 0)) != 1 or not _verify_password(password, user["password_hash"]):
        _note_failed_login(username)
        return False, "帳號或密碼錯誤"

    _clear_failed_logins(username)
    ss.auth_user = _session_user_dict(user)
    ss.auth_checked_at = time.time()
    _set_query_token(_auth_token(user["id"], ss.auth_user["token_version"]))
    return True, "登入成功"


def logout():
    user_id = current_user_id()
    ss.auth_user = None
    _set_query_token(None)
    if user_id is not None:
        try:
            db_store.bump_token_version(user_id)  # revoke URL tokens on every device
        except Exception as e:
            print(f"[AUTH] token revoke failed: {e}")


def render_login_form():
    st.title("PaTiENZ 登入")
    st.caption("請先登入再使用系統。")

    if ALLOW_REGISTRATION:
        login_tab, register_tab = st.tabs(["登入", "註冊帳號"])
    else:
        login_tab, register_tab = st.container(), None

    with login_tab:
        with st.form("login_form", clear_on_submit=False):
            username = st.text_input("帳號")
            password = st.text_input("密碼", type="password")
            submitted = st.form_submit_button("登入", use_container_width=True)

        if submitted:
            ok, msg = login(username, password)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

    if register_tab is None:
        return

    with register_tab:
        st.caption("註冊後即可直接登入，新增帳號預設為一般使用者（預設群組）。")
        with st.form("register_form", clear_on_submit=True):
            register_username = st.text_input("新帳號")
            register_password = st.text_input("密碼（至少 8 碼）", type="password")
            register_confirmation = st.text_input("再次輸入密碼", type="password")
            register_submitted = st.form_submit_button("註冊", use_container_width=True)

        if register_submitted:
            ok, msg = register_user(
                username=register_username,
                password=register_password,
                password_confirmation=register_confirmation,
            )
            if ok:
                st.success(msg)
            else:
                st.error(msg)


def require_login():
    if is_authenticated():
        return
    st.warning("請先登入")
    st.rerun()
    st.stop()


# ---------------------------------------------------------------------------
# User and group management (admin page + self-registration)
#
# Every function below resolves the acting user from the live session, reloads
# it from the database and checks util/access.py before touching anything, so
# the admin page cannot be used to reach outside the actor's own group.
# ---------------------------------------------------------------------------

USERNAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{2,31}")
USERNAME_RULE = "帳號需為 3-32 碼，且只能包含英數字、底線、句點或連字號"
GROUP_NAME_MAX = 64
NO_PERMISSION = "權限不足"


def current_actor():
    """The logged-in user, freshly loaded from the database (None if the
    session is gone or the account was deactivated)."""
    user_id = current_user_id()
    if user_id is None:
        return None
    user = db_store.get_user_by_id(user_id)
    if not user or int(user.get("is_active", 0)) != 1:
        return None
    return user


def _check_credentials(username, password):
    if not username:
        return "帳號不可為空"
    if not USERNAME_RE.fullmatch(username):
        return USERNAME_RULE
    if len(password or "") < 8:
        return "密碼至少需要 8 個字元"
    return None


def _insert_user(username, password, role, group_id):
    if db_store.get_user_by_username(username):
        return False, "帳號已存在"
    try:
        db_store.create_user(username, _hash_password(password), role=role, group_id=group_id)
    except Exception as e:  # UNIQUE race between two concurrent requests
        text = str(e).lower()
        if "unique" in text and "group_id" in text:
            return False, "該群組已有群組管理員"
        if "unique" in text:
            return False, "帳號已存在"
        raise
    return True, "已新增使用者"


def create_user(username, password, role="user", group_id=None):
    """Create an account. Root may create ordinary users and group admins in
    any group (default group when ``group_id`` is omitted); a group admin may
    only create ordinary users in its own group."""
    actor = current_actor()
    if not access.can_administer(actor):
        return False, NO_PERMISSION
    username = (username or "").strip()
    error = _check_credentials(username, password)
    if error:
        return False, error
    if role not in access.assignable_roles(actor):
        return False, "角色不合法" if role not in db_store.ROLES else NO_PERMISSION

    if access.is_root(actor):
        if group_id is None:
            group_id = db_store.default_group_id()
    else:
        if group_id is not None and group_id != actor["group_id"]:
            return False, NO_PERMISSION
        group_id = actor["group_id"]

    if not db_store.get_group(group_id):
        return False, "找不到群組"
    if role == access.ROLE_GROUP_ADMIN and db_store.get_group_admin(group_id):
        return False, "該群組已有群組管理員"
    return _insert_user(username, password, role, group_id)


def register_user(username, password, password_confirmation):
    """Self-registration: always an ordinary user in the default group."""
    if not ALLOW_REGISTRATION:
        return False, "目前不開放自行註冊，請聯絡管理員"
    username = (username or "").strip()
    if not USERNAME_RE.fullmatch(username):
        return False, USERNAME_RULE
    if password != password_confirmation:
        return False, "兩次輸入的密碼不一致"
    error = _check_credentials(username, password)
    if error:
        return False, error
    return _insert_user(username, password, access.ROLE_USER, db_store.default_group_id())


def list_users():
    """Accounts the current user may see: all (root) or own group (group admin)."""
    actor = current_actor()
    if access.is_root(actor):
        return db_store.list_users()
    if access.is_group_admin(actor):
        return db_store.list_users(group_id=actor["group_id"])
    return []


def list_groups():
    """Groups the current user may administer."""
    actor = current_actor()
    if access.is_root(actor):
        return db_store.list_groups()
    if access.is_group_admin(actor):
        group = db_store.get_group(actor["group_id"])
        return [group] if group else []
    return []


def update_user(old_username, username, password, role, is_active, group_id=None):
    """Edit an account. ``group_id`` None keeps the account's current group;
    only root may change role or group (moving a user carries its data along)."""
    actor = current_actor()
    if not access.can_administer(actor):
        return False, NO_PERMISSION
    old_username = (old_username or "").strip()
    username = (username or "").strip()
    if not old_username:
        return False, "請選擇使用者"

    user = db_store.get_user_by_username(old_username)
    # A group admin gets the same answer for "does not exist" and "exists in
    # another group", so it cannot probe for accounts outside its group.
    is_self = bool(user) and user["id"] == actor["id"]
    if not user or not (is_self or access.can_manage_user(actor, user)):
        return False, "找不到使用者" if access.is_root(actor) else NO_PERMISSION

    if not USERNAME_RE.fullmatch(username):
        return False, USERNAME_RULE
    if password and len(password) < 8:
        return False, "密碼至少需要 8 個字元"
    if role not in db_store.ROLES:
        return False, "角色不合法"
    if group_id is None:
        group_id = user["group_id"]

    if user["role"] == access.ROLE_ROOT:
        if role != access.ROLE_ROOT or not is_active:
            return False, "Root 管理員不可降級或停用"
        group_id = None
    else:
        if role == access.ROLE_ROOT:
            return False, "系統只能有一位 Root 管理員"
        if is_self and not is_active:
            return False, "不可停用目前登入中的帳號"
        if not access.is_root(actor) and (role != user["role"] or group_id != user["group_id"]):
            return False, NO_PERMISSION
        if not db_store.get_group(group_id):
            return False, "找不到群組"
        if role == access.ROLE_GROUP_ADMIN:
            holder = db_store.get_group_admin(group_id)
            if holder and holder["id"] != user["id"]:
                return False, "該群組已有群組管理員"

    existing = db_store.get_user_by_username(username)
    if existing and existing["id"] != user["id"]:
        return False, "帳號已存在"

    password_hash = _hash_password(password) if password else None
    try:
        updated = db_store.update_user(
            user_id=user["id"],
            username=username,
            password_hash=password_hash,
            role=role,
            is_active=1 if is_active else 0,
            group_id=group_id,
        )
    except Exception as e:  # UNIQUE race
        text = str(e).lower()
        if "unique" in text and "group_id" in text:
            return False, "該群組已有群組管理員"
        if "unique" in text:
            return False, "帳號已存在"
        raise
    if not updated:
        return False, "使用者更新失敗"

    message = "使用者資料已更新"
    if group_id != user["group_id"]:
        try:
            db_store.move_user_data(user["id"], user["group_id"], group_id)
        except Exception as e:
            print(f"[AUTH] moving data of user {user['id']} failed: {e}")
            message = "使用者已移至新群組，但資料搬移失敗，請查看伺服器紀錄"

    if is_self:
        refreshed = db_store.get_user_by_id(user["id"])
        ss.auth_user = _session_user_dict(refreshed)
        ss.auth_checked_at = time.time()
        if password_hash is not None:
            _set_query_token(_auth_token(user["id"], ss.auth_user["token_version"]))
    return True, message


def delete_user(username):
    actor = current_actor()
    if not access.can_administer(actor):
        return False, NO_PERMISSION
    username = (username or "").strip()
    if not username:
        return False, "請選擇使用者"

    user = db_store.get_user_by_username(username)
    if user and user["id"] == actor["id"]:
        return False, "不可刪除目前登入中的帳號"
    if not user or not access.can_manage_user(actor, user):
        return False, "找不到使用者" if access.is_root(actor) else NO_PERMISSION
    if user["role"] == access.ROLE_ROOT:
        return False, "不可刪除 Root 管理員"

    db_store.delete_user(user["id"])
    return True, "已刪除使用者"


def _check_group_name(name):
    if not name:
        return "群組名稱不可為空"
    if len(name) > GROUP_NAME_MAX:
        return f"群組名稱不可超過 {GROUP_NAME_MAX} 個字元"
    return None


def create_group(name):
    """Root only. The group starts empty; appoint its admin separately."""
    if not access.can_manage_groups(current_actor()):
        return False, NO_PERMISSION
    name = (name or "").strip()
    error = _check_group_name(name)
    if error:
        return False, error
    if db_store.get_group_by_name(name):
        return False, "群組名稱已存在"
    try:
        db_store.create_group(name)
    except Exception as e:
        if "unique" in str(e).lower():
            return False, "群組名稱已存在"
        raise
    return True, "已新增群組"


def rename_group(group_id, name):
    if not access.can_manage_groups(current_actor()):
        return False, NO_PERMISSION
    name = (name or "").strip()
    error = _check_group_name(name)
    if error:
        return False, error
    existing = db_store.get_group_by_name(name)
    if existing and existing["id"] != group_id:
        return False, "群組名稱已存在"
    if not db_store.rename_group(group_id, name):
        return False, "找不到群組"
    return True, "群組名稱已更新"


def delete_group(group_id):
    """Root only, and only for an empty, non-default group."""
    if not access.can_manage_groups(current_actor()):
        return False, NO_PERMISSION
    group = db_store.get_group(group_id)
    if not group:
        return False, "找不到群組"
    if group_id == db_store.default_group_id():
        return False, "不可刪除預設群組"
    if group["member_count"]:
        return False, "群組內仍有使用者，請先移出或刪除"
    if not db_store.delete_group(group_id):
        return False, "群組內仍有使用者，請先移出或刪除"
    return True, "已刪除群組"
