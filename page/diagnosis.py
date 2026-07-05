import streamlit as st
from model.patient import create_patient_model
from util.process import process_audio
import util.dialog as dialog
import util.tools as util
import util.chat as chat

# Configure instruction file paths
ss = st.session_state

util.init(5)
util.note()

CHAT_HEIGHT = 400


def send_to_patient(prompt: str, chat_area):
    """Shared send handler for the病情解釋 chat: record the doctor's message,
    get a hardened patient reply, and append it. Returns True on success."""
    prompt = prompt.rstrip("\n")
    if prompt == "":
        return False

    util.record(ss.log, f"Doctor: {prompt}")
    chat.append(ss.diagnostic_messages, "doctor", prompt)
    chat.update(chat_area, msgs=ss.diagnostic_messages, height=CHAT_HEIGHT, show_all=ss.show_all)

    # UX-3 / PERF-3: harden the patient generation call so a capped/blocked
    # candidate never reaches response.text and crashes the page.
    try:
        response = ss.patient.send_message(f"醫學生：{prompt}")
    except Exception as e:
        util.record(ss.log, f"[PATIENT] send_message error: {e}")
        st.warning("病人沒聽清楚，請再說一次")
        return False

    finish_reason = None
    candidate = None
    try:
        if response.candidates:
            candidate = response.candidates[0]
            finish_reason = candidate.finish_reason
    except Exception:
        candidate = None

    blocked = False
    try:
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            blocked = True
    except Exception:
        blocked = False

    reply_text = ""
    if candidate is not None and not blocked:
        try:
            reply_text = response.text
        except Exception as e:
            util.record(ss.log, f"[PATIENT] response.text unavailable (finish_reason={finish_reason}): {e}")
            reply_text = ""

    formatted_response = reply_text.replace("(", "（").replace(")", "）").strip()

    if blocked or candidate is None or formatted_response == "":
        util.record(ss.log, f"[PATIENT] empty/blocked response (finish_reason={finish_reason}, blocked={blocked})")
        st.warning("病人沒聽清楚，請再說一次")
        return False

    util.record(ss.log, f"Patient: {reply_text}")
    chat.append(ss.diagnostic_messages, "patient", formatted_response)
    return True


def list_input(state_key, label, help=None, placeholder=None, multiline=False, height=90):
    """Render a dynamic list of inputs with add/delete-row buttons.

    Returns the list of trimmed, non-empty values.
    """
    if state_key not in ss:
        ss[state_key] = [""]

    st.markdown(f"**{label}**")
    if help:
        st.caption(help)

    n = len(ss[state_key])
    delete_idx = None
    for i in range(n):
        cols = st.columns([20, 1], vertical_alignment="center")
        with cols[0]:
            widget_key = f"{state_key}__row_{i}_of_{n}"
            if multiline:
                ss[state_key][i] = st.text_area(
                    f"{state_key}_{i}",
                    value=ss[state_key][i],
                    key=widget_key,
                    label_visibility="collapsed",
                    placeholder=placeholder,
                    height=height,
                )
            else:
                ss[state_key][i] = st.text_input(
                    f"{state_key}_{i}",
                    value=ss[state_key][i],
                    key=widget_key,
                    label_visibility="collapsed",
                    placeholder=placeholder,
                )
        with cols[1]:
            if st.button(
                "✕",
                key=f"{state_key}__del_{i}_of_{n}",
                help="刪除此列",
                use_container_width=True,
            ):
                delete_idx = i

    if delete_idx is not None:
        ss[state_key].pop(delete_idx)
        if not ss[state_key]:
            ss[state_key] = [""]
        st.rerun()

    if st.button("➕ 新增一列", key=f"{state_key}__add", use_container_width=True):
        ss[state_key].append("")
        st.rerun()

    return [s.strip() for s in ss[state_key] if s and s.strip()]

column = st.columns([1, 10, 1, 4])

with column[1]:
    st.header("對話區（病情解釋）")
    output_container = st.container()
    chat_area = output_container.empty()

    if "patient_model" not in ss and "problem" in ss:
        create_patient_model(ss.problem, prior_messages=ss.diagnostic_messages)

    chat.update(chat_area, msgs=ss.diagnostic_messages, height=CHAT_HEIGHT, show_all=ss.show_all)

    # 語音輸入：轉成文字後走與文字輸入相同的送出流程。
    # st.audio_input 在 rerun 後仍會保留錄音，故以內容雜湊去重，避免重複送出。
    if audio := st.audio_input("語音輸入", key="audio_input2"):
        audio_bytes = audio.getvalue()
        audio_id = hash(audio_bytes)
        if ss.get("last_audio_id2") != audio_id and util.check_progress():
            ss.last_audio_id2 = audio_id
            transcript = process_audio(audio)
            if transcript and send_to_patient(transcript, chat_area):
                st.rerun()

    ss.diagnosis = st.text_input("主診斷")

    if ss.preliminary_ddx:
        st.subheader("初步鑑別診斷處理")
        st.caption("請對先前提出的每個鑑別診斷標註目前狀態，作為臨床推理之依據。")
        for i, item in enumerate(ss.preliminary_ddx):
            cols = st.columns([3, 5])
            with cols[0]:
                st.markdown(f"**{item['name']}**")
                st.caption(f"原可能性：{item.get('likelihood', '中')}")
            with cols[1]:
                ss.final_ddx_status[item["name"]] = st.radio(
                    " ",
                    options=["保留為鑑別", "已被檢驗排除", "確診（即主診斷）"],
                    key=f"final_ddx_{i}",
                    horizontal=True,
                    label_visibility="collapsed",
                )

    extra = list_input(
        "additional_ddx_list",
        "其他新增鑑別診斷",
        help="不在初步清單中的鑑別診斷，每列輸入一項",
        placeholder="例如：Pneumonia",
    )

    retained = [name for name, status in ss.final_ddx_status.items() if status == "保留為鑑別"]
    ss.ddx = "、".join(retained + extra) if (retained or extra) else ""

    comorbidities_list = list_input(
        "comorbidities_list",
        "已存在共病",
        help="每列輸入一項，例如 HTN、DM、CKD",
        placeholder="例如：HTN",
    )
    ss.comorbidities = "、".join(comorbidities_list)

    treatment_list = list_input(
        "treatment_list",
        "處置",
        help="包含進行之檢查與治療方式，每列輸入一項；若內容較長會自動換行顯示",
        placeholder="例如：Order CXR、Start IV NS 1L over 1 hour",
        multiline=True,
    )
    ss.treatment = "、".join(treatment_list)

# Add a confirm answer button outside the input container
    button_container = st.container()
    with button_container:
        if st.button("開始評分", use_container_width=True) and util.check_progress():
            if ss.diagnosis != "" and ss.treatment != "":
                print(ss.diagnosis)
                print(ss.treatment)
                ss.diagnostic_ended = True

                util.next_page()
            else:
                st.warning("請先完成診斷和處置")

with column[3]:
    util.show_patient_profile()

    st.subheader("其他資訊")
    with st.container(border=True):
        util.peek_chat()
        # util.show_time()

# st.chat_input must live at the app's top level (it cannot sit inside
# st.columns). Enter submits and the widget clears itself automatically.
if prompt := st.chat_input("請輸入您的對話內容"):
    if util.check_progress():
        if send_to_patient(prompt, chat_area):
            st.rerun()

