import streamlit as st

import util.auth as auth


st.set_page_config(layout="wide")
auth.init_auth()
auth.require_login()

if not auth.is_admin():
    st.error("僅限 admin 使用")
    st.stop()

st.title("使用者管理")
st.caption("新增或刪除使用者帳號。")
st.info(
    "操作說明：\n"
    "1. 左側輸入帳號、密碼與角色後按「新增」。\n"
    "2. 右側選擇帳號後按「刪除使用者」。\n"
    "3. 安全限制：不可刪除目前登入帳號，且系統至少保留一位 admin。\n"
    "4. 你也可直接編輯 config/users.json，系統啟動時會自動同步。"
)

left, right = st.columns([1, 1])

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
