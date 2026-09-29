import os
import time
import datetime
import uuid

import streamlit as st
import util.constants as const
import util.dialog as dialog
import util.navigation as navigation
import util.stages as stages
import util.save_load as save_load
import util.db_store as db_store
import util.auth as auth

ss = st.session_state 

def next_page():
    if ss.page_id == ss.current_progress:
        ss.current_progress = (ss.current_progress + 1) % len(const.section_name)
        target = ss.current_progress
    else:
        target = min(ss.page_id + 1, len(const.section_name) - 1)
    st.switch_page(f"page/{const.section_name[target]}.py")

def init_all():
    try:
        auth.init_auth()
    except Exception as e:
        print(f"[DB] init failed: {e}")

    if not auth.is_authenticated():
        return

    if "sid" not in ss:
        safe_user = auth.safe_user_segment(auth.current_username())
        user_key = auth.current_user_id() if auth.current_user_id() is not None else "anon"
        ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        ss.sid = f"{user_key}-{ts}-{uuid.uuid4().hex[:12]}"
        ss.log = f"data/log/{safe_user}/{ss.sid}.txt"
        print(f"Session ID: {ss.sid}")
        print(f"Log file: {ss.log}")

        ss.page_id = 0
        ss.current_progress = 0

        ss.first_entry = [True for _ in range(len(const.section_name))]

        ss.diagnostic_messages = []
        ss.pe_result = []
        ss.examination_result = []
        ss.examination_history = []
        ss.ordered_exam_set = set()
        ss.advice_messages = []
        ss.preliminary_ddx = []
        ss.preliminary_ddx_locked = False
        ss.comorbidities = ""
        ss.final_ddx_status = {}
        # Defaults so grading degrades gracefully if a free-exploration student
        # reaches 評分 without visiting 診斷 (diagnosis.py normally assigns these).
        ss.diagnosis = ""
        ss.ddx = ""
        ss.treatment = ""

        ss.start_time = [None for _ in range(len(const.section_name))]
        # Full conversation history is now shown by default (UX-2). show_all=True
        # means the whole transcript is rendered in the scrollable container.
        ss.cur_show_all, ss.show_all = True, True

def init(page_id: int):
    auth.init_auth()
    if not auth.is_authenticated():
        st.warning("請先登入")
        st.rerun()
        st.stop()

    ss.page_id = page_id

    # Free-exploration mode (§7): visiting a phase ahead of the frontier unlocks
    # it. Any phases skipped over keep start_time=None (tolerated by show_time).
    if ss.get("free_navigation") and ss.page_id > ss.current_progress and not ss.first_entry[0]:
        ss.current_progress = ss.page_id

    if ss.start_time[ss.page_id] is None and ss.current_progress == ss.page_id:
        ss.start_time[ss.page_id] = time.time()
        print(f"Start time for page {ss.page_id}: {ss.start_time[ss.page_id]}")

    if ss.first_entry[0] == True and ss.page_id != 0:
        st.switch_page(f"page/{const.section_name[0]}.py")

    if ss.current_progress == ss.page_id and ss.first_entry[ss.page_id]:
        ss.first_entry[ss.page_id] = False
        dialog.intro(ss.page_id)

@st.fragment(run_every=1)
def show_time():
    grade_idx = len(const.section_name) - 1
    last_active = grade_idx - 1
    for i in range(1, min(last_active, max(ss.page_id, ss.current_progress)) + 1):
        if ss.current_progress < i:
            continue
        # In free-exploration mode a phase may have been skipped (start_time None);
        # skip its timer rather than crash on arithmetic with None.
        if ss.start_time[i] is None:
            continue
        if ss.current_progress == i:
            elapsed_time = int(time.time() - ss.start_time[i])
        else:
            if ss.start_time[i + 1] is None:
                continue
            elapsed_time = int(ss.start_time[i + 1] - ss.start_time[i])
        st.write(f"{const.noun[i]}時間：{elapsed_time // 60}:{elapsed_time % 60:02d}")

    if ss.current_progress > 0 and ss.start_time[1] is not None:
        if ss.current_progress < grade_idx or ss.start_time[grade_idx] is None:
            elapsed_time = int(time.time() - ss.start_time[1])
        else:
            elapsed_time = int(ss.start_time[grade_idx] - ss.start_time[1])

        st.write(f"總時間：{elapsed_time // 60}:{elapsed_time % 60:02d}")

def peek_chat():
    # Full history is shown by default now; this checkbox lets the student
    # collapse the view down to just the latest exchange if they prefer.
    compact = st.checkbox("只顯示最新對話", not ss.show_all)
    ss.show_all = not compact
    if ss.show_all != ss.cur_show_all:
        ss.cur_show_all = ss.show_all
        st.rerun()

def _render_quest_tracker():
    """闖關模式側欄進度（§7-B）。呈現臆斷→篩檢→再鑑別→確診各關卡的完成狀態。
    必須在 `with st.sidebar:` 區塊內呼叫，st.* 才會渲染到側欄。"""
    snapshot = {
        "history_turns": sum(1 for m in ss.get("diagnostic_messages", []) if m.get("role") == "doctor"),
        "has_tentative": bool(ss.get("preliminary_ddx")),
        "exams_ordered": len(ss.get("examination_history", [])),
        "has_diagnosis": bool(ss.get("diagnosis")),
    }
    st.divider()
    st.header("闖關進度")
    icons = {"done": "✅", "current": "▶", "locked": "🔒"}
    for s in stages.stage_status(snapshot):
        st.markdown(f"{icons[s['state']]} {s['title']}")
        if s["state"] == "current":
            st.caption(f"🎯 {s['goal']}")
    if stages.all_cleared(snapshot):
        st.success("🏆 已完成所有關卡，可前往評分區")


def note():
    auth.init_auth()
    auth.require_login()

    if "note" not in ss:
        ss.note = ""

    with st.sidebar:
        st.caption(f"使用者：{auth.current_username()} ({auth.current_role()})")
        if st.button("登出", use_container_width=True, key="logout_btn"):
            auth.logout()
            st.rerun()
            st.stop()

        if auth.is_admin() and st.button("🛠️ 使用者管理", use_container_width=True, key="admin_users_btn"):
            st.switch_page("page/admin.py")

        st.header("看診進度")
        for i, n in enumerate(const.noun):
            label = f"{const.icon[i]} {n}區"
            if i < ss.current_progress:
                if st.button(f"↩ {label}", key=f"navback_{i}", use_container_width=True):
                    st.switch_page(f"page/{const.section_name[i]}.py")
            elif i == ss.current_progress:
                if i == ss.page_id:
                    st.markdown(f"**▶ {label}**（進行中）")
                else:
                    if st.button(f"▶ {label}（進行中）", key=f"navcur_{i}", use_container_width=True):
                        st.switch_page(f"page/{const.section_name[i]}.py")
            else:
                if ss.get("free_navigation"):
                    if st.button(f"→ {label}", key=f"navfwd_{i}", use_container_width=True):
                        st.switch_page(f"page/{const.section_name[i]}.py")
                else:
                    st.caption(f"🔒 {label}")

        if ss.get("free_navigation"):
            st.caption("🧭 自由探索模式：可任意切換各階段")

        if ss.get("flow_mode") == "quest":
            _render_quest_tracker()

        st.divider()
        st.header("筆記區")
        ss.note = st.text_area("在此輸入您看診時的記錄，不計分", height=250, value=ss.note)

        st.divider()
        st.header("進度存檔")
        if st.button("💾 立即存檔", use_container_width=True, key="save_progress_btn"):
            file_name = save_load.save_progress()
            ss.last_save_file = file_name
            st.success(f"已存檔：{file_name}")
        if ss.get("last_save_file"):
            st.caption(f"最近存檔：{ss.last_save_file}")

def show_patient_profile():
    st.header("病人資料")
    data_container = st.container(border=True)
    if "data" in ss:
        data = ss.data
        with data_container:
            st.write(f"姓名：{data['基本資訊']['姓名']}")
            st.write(f"生日：{data['基本資訊']['生日']}")
            st.write(f"性別：{data['基本資訊']['性別']}")
            st.write(f"身高：{data['基本資訊']['身高']} cm")
            st.write(f"體重：{data['基本資訊']['體重']} kg")
            if "MH" in data and "主訴" in data["MH"]:
                st.write(f"**主訴：{data['MH']['主訴']}**")


def check_progress():
    if navigation.can_visit(ss.page_id, ss.current_progress, ss.get("free_navigation")):
        return True
    dialog.page_error(ss.page_id, ss.current_progress)
    return False


def record(file, text):
    """
    Records the text to a file.

    :param file: The file to write to.
    :param text: The text to write.
    """
    os.makedirs(os.path.dirname(file), exist_ok=True)
    with open(file, "a", encoding="utf-8") as f:
        f.write(text + "\n")

    try:
        if ss.get("sid"):
            db_store.append_log(ss.sid, text)
    except Exception as e:
        print(f"[DB] append_log failed: {e}")

    print(f"Recorded: {text}")
