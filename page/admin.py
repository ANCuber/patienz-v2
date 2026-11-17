import streamlit as st

import util.access as access
import util.admin_data as admin_data
import util.auth as auth


st.set_page_config(layout="wide")
auth.init_auth()
auth.require_login()

actor = auth.current_actor()
if not access.can_administer(actor):
    st.error("僅限 Root 管理員或群組管理員使用")
    st.stop()

IS_ROOT = access.is_root(actor)
groups = auth.list_groups()
group_names = {g["id"]: g["name"] for g in groups}
group_ids = list(group_names)


def group_label(group_id):
    return group_names.get(group_id, "（無群組）")


def user_label(user):
    where = "Root" if user["role"] == access.ROLE_ROOT else (user.get("group_name") or "—")
    return f"{user['username']}（{where}／{auth.role_label(user['role'])}）"


def show_result(ok, msg):
    if ok:
        st.success(msg)
        st.rerun()
    else:
        st.error(msg)


with st.sidebar:
    st.caption(f"目前登入：{auth.current_username()}（{auth.role_label()}）")
    if not IS_ROOT:
        st.caption(f"管理群組：{group_label(actor['group_id'])}")
    if st.button("返回看診", use_container_width=True, key="admin_back_btn"):
        st.switch_page("page/config.py")
    if st.button("登出", use_container_width=True, key="admin_logout_btn"):
        auth.logout()
        st.rerun()

st.title("管理後台")
if IS_ROOT:
    st.caption("Root 管理員：可管理所有群組、群組管理員、使用者與各群組的資料庫。")
else:
    st.caption(f"群組管理員：可管理「{group_label(actor['group_id'])}」群組內的一般使用者與資料，無法存取其他群組。")

tab_names = ["使用者", "群組", "資料"] if IS_ROOT else ["使用者", "資料"]
tabs = dict(zip(tab_names, st.tabs(tab_names)))

# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
with tabs["使用者"]:
    users = auth.list_users()
    roles = access.assignable_roles(actor)
    left, middle, right = st.columns([1, 1.2, 1])

    with left:
        st.subheader("新增使用者")
        with st.form("create_user_form", clear_on_submit=True):
            username = st.text_input("帳號")
            password = st.text_input("密碼（至少 8 碼）", type="password")
            role = st.selectbox("角色", roles, format_func=auth.role_label)
            if IS_ROOT:
                new_group = st.selectbox("群組", group_ids, format_func=group_label, key="create_user_group")
            else:
                new_group = actor["group_id"]
            submitted = st.form_submit_button("新增", use_container_width=True)

        if submitted:
            show_result(*auth.create_user(username=username, password=password, role=role, group_id=new_group))

    with middle:
        st.subheader("修改使用者")
        # A group admin edits its ordinary users and its own account; root edits anyone.
        editable = [u for u in users if u["id"] == actor["id"] or access.can_manage_user(actor, u)]
        if editable:
            selected = st.selectbox("選擇帳號", editable, format_func=user_label, key="edit_user")
            target_is_root = selected["role"] == access.ROLE_ROOT
            is_self = selected["id"] == actor["id"]
            with st.form(f"edit_user_form_{selected['id']}", clear_on_submit=False):
                edited_username = st.text_input("帳號", value=selected["username"])
                edited_password = st.text_input("新密碼（留空則不修改）", type="password")
                if IS_ROOT and not target_is_root:
                    edited_role = st.selectbox(
                        "角色", roles, index=roles.index(selected["role"]), format_func=auth.role_label
                    )
                    edited_group = st.selectbox(
                        "群組（變更時會一併搬移該使用者的資料）",
                        group_ids,
                        index=group_ids.index(selected["group_id"]),
                        format_func=group_label,
                    )
                else:
                    edited_role, edited_group = selected["role"], selected["group_id"]
                edited_active = st.checkbox(
                    "啟用帳號", value=int(selected["is_active"]) == 1, disabled=target_is_root or is_self
                )
                edit_submitted = st.form_submit_button("儲存修改", use_container_width=True)

            if edit_submitted:
                show_result(*auth.update_user(
                    old_username=selected["username"],
                    username=edited_username,
                    password=edited_password,
                    role=edited_role,
                    is_active=edited_active,
                    group_id=edited_group,
                ))
        else:
            st.info("目前沒有可修改的使用者")

    with right:
        st.subheader("刪除使用者")
        deletable = [u for u in users if u["id"] != actor["id"] and access.can_manage_user(actor, u)]
        if not deletable:
            st.info("目前沒有可刪除的使用者")
        else:
            to_delete = st.selectbox("選擇帳號", deletable, format_func=user_label, key="delete_user")
            st.caption("會一併刪除該使用者的存檔與評分結果，無法復原。")
            if st.button("刪除使用者", type="primary", use_container_width=True):
                show_result(*auth.delete_user(to_delete["username"]))

    st.divider()
    st.subheader("使用者清單")
    if users:
        st.dataframe(
            [
                {
                    "帳號": u["username"],
                    "群組": "—" if u["role"] == access.ROLE_ROOT else (u.get("group_name") or "—"),
                    "角色": auth.role_label(u["role"]),
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

# ---------------------------------------------------------------------------
# Groups (root only)
# ---------------------------------------------------------------------------
if IS_ROOT:
    with tabs["群組"]:
        st.caption(
            "每個群組有獨立的資料庫，最多一位群組管理員。群組與群組管理員可分開建立："
            "先在此新增群組，再到「使用者」分頁新增或指定該群組的群組管理員。"
        )
        left, middle, right = st.columns(3)

        with left:
            st.subheader("新增群組")
            with st.form("create_group_form", clear_on_submit=True):
                group_name = st.text_input("群組名稱")
                group_submitted = st.form_submit_button("新增", use_container_width=True)
            if group_submitted:
                show_result(*auth.create_group(group_name))

        with middle:
            st.subheader("重新命名")
            rename_id = st.selectbox("選擇群組", group_ids, format_func=group_label, key="rename_group")
            with st.form(f"rename_group_form_{rename_id}", clear_on_submit=False):
                renamed = st.text_input("新名稱", value=group_label(rename_id))
                rename_submitted = st.form_submit_button("儲存", use_container_width=True)
            if rename_submitted:
                show_result(*auth.rename_group(rename_id, renamed))

        with right:
            st.subheader("刪除群組")
            delete_id = st.selectbox("選擇群組", group_ids, format_func=group_label, key="delete_group")
            st.caption("只能刪除沒有使用者的群組；預設群組不可刪除。資料庫檔案會保留並改名封存。")
            if st.button("刪除群組", type="primary", use_container_width=True):
                show_result(*auth.delete_group(delete_id))

        st.divider()
        st.subheader("群組清單")
        st.dataframe(
            [
                {
                    "群組": g["name"],
                    "群組管理員": g["admin_username"] or "（未指定）",
                    "人數": g["member_count"],
                    "建立時間": g["created_at"],
                }
                for g in groups
            ],
            use_container_width=True,
            hide_index=True,
        )

# ---------------------------------------------------------------------------
# Data (one group's database at a time)
# ---------------------------------------------------------------------------
with tabs["資料"]:
    scopes = admin_data.accessible_scopes()
    scope_labels = dict(scopes)
    if not scopes:
        st.info("沒有可檢視的資料")
        st.stop()
    scope = st.selectbox(
        "資料庫", [gid for gid, _ in scopes], format_func=lambda gid: scope_labels[gid], key="data_scope"
    )

    try:
        saves = admin_data.list_progress_saves(scope)
        results = admin_data.list_grading_results(scope)
        log_rows = admin_data.count_log_rows(scope)
    except admin_data.PermissionDenied as e:
        st.error(str(e))
        st.stop()

    st.caption(f"進度存檔 {len(saves)} 筆｜評分結果 {len(results)} 筆｜對話紀錄 {log_rows} 行")

    def record_section(title, rows, name_key, columns, load, delete, key):
        st.subheader(title)
        if not rows:
            st.info("尚無資料")
            return
        st.dataframe(
            [{label: row.get(field) for label, field in columns} for row in rows],
            use_container_width=True,
            hide_index=True,
        )
        picked = st.selectbox(
            "選擇一筆",
            rows,
            format_func=lambda r: f"{r['username']}｜{r[name_key]}",
            key=f"{key}_pick_{scope}",
        )
        view_col, delete_col = st.columns(2)
        with view_col:
            if st.button("檢視內容", use_container_width=True, key=f"{key}_view_{scope}"):
                payload = load(scope, picked["user_id"], picked[name_key])
                if payload is None:
                    st.warning("找不到這筆資料")
                else:
                    st.json(payload, expanded=False)
        with delete_col:
            if st.button("刪除這筆", type="primary", use_container_width=True, key=f"{key}_delete_{scope}"):
                delete(scope, picked["user_id"], picked[name_key])
                st.success("已刪除")
                st.rerun()

    try:
        record_section(
            "評分結果", results, "record_name",
            [("使用者", "username"), ("病人", "patient_name"), ("疾病", "disease"),
             ("分數 (%)", "score_v2_percentage"), ("更新時間", "updated_at"), ("名稱", "record_name")],
            admin_data.get_grading_payload, admin_data.delete_grading_result, "grading",
        )
        st.divider()
        record_section(
            "進度存檔", saves, "save_name",
            [("使用者", "username"), ("病人", "patient_name"), ("進度", "progress_label"),
             ("更新時間", "updated_at"), ("名稱", "save_name")],
            admin_data.get_progress_payload, admin_data.delete_progress_save, "saves",
        )
    except admin_data.PermissionDenied as e:
        st.error(str(e))
