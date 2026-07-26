import hashlib
import hmac
import json
import os
import secrets

import streamlit as st

import util.db_store as db_store

ss = st.session_state

DEFAULT_ADMIN_USERNAME = os.getenv("PATIENZ_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("PATIENZ_ADMIN_PASSWORD", "admin123")
USERS_CONFIG_PATH = os.getenv("PATIENZ_USERS_CONFIG", "config/users.json")


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


def _bootstrap_default_admin():
    if db_store.count_users() > 0:
        return
    db_store.create_user(
        username=DEFAULT_ADMIN_USERNAME,
        password_hash=_hash_password(DEFAULT_ADMIN_PASSWORD),
        role="admin",
    )


def _read_users_config():
    if not os.path.exists(USERS_CONFIG_PATH):
        return None
    try:
        with open(USERS_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _bootstrap_users_from_config():
    cfg = _read_users_config()
    if not isinstance(cfg, dict):
        return False

    users = cfg.get("users")
    if not isinstance(users, list):
        return False

    synced = 0
    for item in users:
        if not isinstance(item, dict):
            continue

        username = str(item.get("username") or "").strip()
        role = str(item.get("role") or "user").strip().lower()
        is_active = bool(item.get("is_active", True))
        password = item.get("password")
        password_hash = item.get("password_hash")

        if not username:
            continue
        if role not in {"admin", "user"}:
            role = "user"

        if isinstance(password_hash, str) and "$" in password_hash:
            final_hash = password_hash
        elif isinstance(password, str) and password:
            final_hash = _hash_password(password)
        else:
            existing = db_store.get_user_by_username(username)
            if not existing:
                continue
            final_hash = existing["password_hash"]

        db_store.upsert_user(
            username=username,
            password_hash=final_hash,
            role=role,
            is_active=1 if is_active else 0,
        )
        synced += 1

    return synced > 0


def init_auth():
    db_store.init_db()
    loaded_from_json = _bootstrap_users_from_config()
    if not loaded_from_json:
        _bootstrap_default_admin()
    if "auth_user" not in ss:
        ss.auth_user = None


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
    return True, "登入成功"


def logout():
    ss.auth_user = None


def render_login_form():
    st.title("PaTiENZ 登入")
    st.caption("請先登入再使用系統。")

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


def list_users():
    return db_store.list_users()


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
