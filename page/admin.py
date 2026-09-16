import streamlit as st

import util.auth as auth


st.set_page_config(layout="wide")
auth.init_auth()
auth.require_login()

if not auth.is_admin():
    st.error("僅限 admin 使用")
    st.stop()

with st.sidebar:
    st.caption(f"目前登入：{auth.current_username()} (admin)")
    if st.button("返回看診", use_container_width=True, key="admin_back_btn"):
        st.switch_page("page/config.py")
    if st.button("登出", use_container_width=True, key="admin_logout_btn"):
        auth.logout()
        st.rerun()

st.title("使用者管理")
st.caption("新增、修改或刪除使用者帳號。")
st.info(
    "操作說明：\n"
    "1. 左側輸入帳號、密碼與角色後按「新增」。\n"
    "2. 中間選擇帳號後可修改帳號、密碼、角色與啟用狀態。\n"
    "3. 右側選擇帳號後按「刪除使用者」。\n"
    "4. 安全限制：不可刪除或停用目前登入帳號，且系統至少保留一位啟用中的 admin。\n"
    "5. 使用者資料以資料庫為準；新增或修改後不需要編輯其他檔案。"
)

left, middle, right = st.columns([1, 1.2, 1])

with left:
    st.subheader("新增使用者")
    with st.form("create_user_form", clear_on_submit=True):
        username = st.text_input("帳號")
        password = st.text_input("密碼（至少 8 碼）", type="password")
        role = st.selectbox("角色", ["user", "admin"])
        submitted = st.form_submit_button("新增", use_container_width=True)

    if submitted:
        ok, msg = auth.create_user(username=username, password=password, role=role)
        if ok:
            st.success(msg)
            st.rerun()
        else:
            st.error(msg)

with middle:
    st.subheader("修改使用者")
    users = auth.list_users()
    user_labels = [u["username"] for u in users]

    if user_labels:
        to_edit = st.selectbox("選擇帳號", user_labels, key="edit_user")
        selected = next(user for user in users if user["username"] == to_edit)
        with st.form("edit_user_form", clear_on_submit=False):
            edited_username = st.text_input("帳號", value=selected["username"])
            edited_password = st.text_input("新密碼（留空則不修改）", type="password")
            edited_role = st.selectbox(
                "角色",
                ["user", "admin"],
                index=0 if selected["role"] == "user" else 1,
            )
            edited_active = st.checkbox("啟用帳號", value=int(selected["is_active"]) == 1)
            edit_submitted = st.form_submit_button("儲存修改", use_container_width=True)

        if edit_submitted:
            ok, msg = auth.update_user(
                old_username=to_edit,
                username=edited_username,
                password=edited_password,
                role=edited_role,
                is_active=edited_active,
            )
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)
    else:
        st.info("目前沒有可修改的使用者")

with right:
    st.subheader("刪除使用者")
    users = auth.list_users()
    user_labels = [u["username"] for u in users]

    if not user_labels:
        st.info("目前沒有可管理的使用者")
    else:
        to_delete = st.selectbox("選擇帳號", user_labels)
        if st.button("刪除使用者", type="primary", use_container_width=True):
            ok, msg = auth.delete_user(to_delete)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

st.divider()
st.subheader("使用者清單")
users = auth.list_users()
if users:
    st.dataframe(
        [
            {
                "帳號": u["username"],
                "角色": u["role"],
                "啟用": "是" if int(u.get("is_active", 0)) == 1 else "否",
                "建立時間": u["created_at"],
            }
            for u in users
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("尚無使用者資料")
