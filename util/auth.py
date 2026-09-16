import binascii
import base64
import hashlib
import hmac
import os
import re
import secrets
import time

import streamlit as st

import util.db_store as db_store

ss = st.session_state

DEFAULT_ADMIN_USERNAME = os.getenv("PATIENZ_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("PATIENZ_ADMIN_PASSWORD", "admin123")
AUTH_TOKEN_PARAM = "patienz_auth"
AUTH_TOKEN_TTL = int(os.getenv("PATIENZ_AUTH_TOKEN_TTL", str(30 * 24 * 60 * 60)))
AUTH_SECRET = os.getenv("PATIENZ_AUTH_SECRET") or os.getenv("GEMINI_API_KEY") or "change-this-auth-secret"


def _hash_password(password, salt=None):
    salt_bytes = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 200000)
    return f"{salt_bytes.hex()}${digest.hex()}"


def _verify_password(password, stored):
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt_bytes = bytes.fromhex(salt_hex)
    except Exception:
        return False

    candidate = _hash_password(password, salt=salt_bytes).split("$", 1)[1]
    return hmac.compare_digest(candidate, digest_hex)


def _auth_token(user_id):
    expires_at = int(time.time()) + AUTH_TOKEN_TTL
    payload = f"{user_id}:{expires_at}".encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(AUTH_SECRET.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


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


def _restore_auth_from_query():
    token = _query_token()
    if not token or "." not in token:
        return
    encoded, signature = token.rsplit(".", 1)
    expected = hmac.new(
        AUTH_SECRET.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        _set_query_token(None)
        return
    try:
        padding = "=" * (-len(encoded) % 4)
        user_id_text, expires_text = base64.urlsafe_b64decode(
            (encoded + padding).encode("ascii")
        ).decode("utf-8").split(":", 1)
        if int(expires_text) <= int(time.time()):
            raise ValueError("expired token")
        user = db_store.get_user_by_id(int(user_id_text))
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
        user = None

    if not user or int(user.get("is_active", 0)) != 1:
        _set_query_token(None)
        return
    ss.auth_user = {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
    }


def _bootstrap_default_admin():
    if db_store.count_users() > 0:
        return
    db_store.create_user(
        username=DEFAULT_ADMIN_USERNAME,
        password_hash=_hash_password(DEFAULT_ADMIN_PASSWORD),
        role="admin",
    )


def init_auth():
    db_store.init_db()
    _bootstrap_default_admin()
    if "auth_user" not in ss:
        ss.auth_user = None
    if not is_authenticated():
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


def current_role():
    user = ss.get("auth_user") or {}
    return user.get("role")


def is_admin():
    return current_role() == "admin"


def login(username, password):
    username = (username or "").strip()
    if not username or not password:
        return False, "請輸入帳號與密碼"

    user = db_store.get_user_by_username(username)
    if not user or int(user.get("is_active", 0)) != 1:
        return False, "帳號不存在或已停用"

    if not _verify_password(password, user["password_hash"]):
        return False, "帳號或密碼錯誤"

    ss.auth_user = {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
    }
    _set_query_token(_auth_token(user["id"]))
    return True, "登入成功"


def logout():
    ss.auth_user = None
    _set_query_token(None)


def render_login_form():
    st.title("PaTiENZ 登入")
    st.caption("請先登入再使用系統。")

    login_tab, register_tab = st.tabs(["登入", "註冊帳號"])

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

    with register_tab:
        st.caption("註冊後即可直接登入，新增帳號預設為一般使用者。")
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


def create_user(username, password, role="user"):
    username = (username or "").strip()
    if not username:
        return False, "帳號不可為空"
    if len(password or "") < 8:
        return False, "密碼至少需要 8 個字元"
    if role not in {"admin", "user"}:
        return False, "角色不合法"

    if db_store.get_user_by_username(username):
        return False, "帳號已存在"

    db_store.create_user(username, _hash_password(password), role=role)
    return True, "已新增使用者"


def register_user(username, password, password_confirmation):
    username = (username or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{2,31}", username):
        return False, "帳號需為 3-32 碼，且只能包含英數字、底線、句點或連字號"
    if password != password_confirmation:
        return False, "兩次輸入的密碼不一致"
    return create_user(username, password, role="user")


def list_users():
    return db_store.list_users()


def update_user(old_username, username, password, role, is_active):
    old_username = (old_username or "").strip()
    username = (username or "").strip()
    if not old_username:
        return False, "請選擇使用者"
    if not username or len(username) > 64 or any(char.isspace() for char in username):
        return False, "帳號不可為空，且不可包含空白"
    if role not in {"admin", "user"}:
        return False, "角色不合法"

    user = db_store.get_user_by_username(old_username)
    if not user:
        return False, "找不到使用者"
    existing = db_store.get_user_by_username(username)
    if existing and existing["id"] != user["id"]:
        return False, "帳號已存在"
    if old_username == current_username() and not is_active:
        return False, "不可停用目前登入中的帳號"
    if user["role"] == "admin" and db_store.count_admin_users() <= 1:
        if role != "admin" or not is_active:
            return False, "系統至少需要保留一位啟用中的 admin"

    password_hash = _hash_password(password) if password else None
    updated = db_store.update_user_by_username(
        old_username=old_username,
        username=username,
        password_hash=password_hash,
        role=role,
        is_active=1 if is_active else 0,
    )
    if not updated:
        return False, "使用者更新失敗"

    if old_username == current_username():
        ss.auth_user["username"] = username
        ss.auth_user["role"] = role
    return True, "使用者資料已更新"


def delete_user(username):
    username = (username or "").strip()
    if not username:
        return False, "請選擇使用者"

    user = db_store.get_user_by_username(username)
    if not user:
        return False, "找不到使用者"

    if username == current_username():
        return False, "不可刪除目前登入中的帳號"

    if user["role"] == "admin" and db_store.count_admin_users() <= 1:
        return False, "系統至少需要保留一位 admin"

    db_store.delete_user_by_username(username)
    return True, "已刪除使用者"
