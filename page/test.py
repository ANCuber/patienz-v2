import streamlit as st
from model.patient import create_patient_model
from util.process import process_audio
import util.tools as util
import util.chat as chat
import time

# Configure instruction file paths
ss = st.session_state

util.init(1)
util.note()

CHAT_HEIGHT = 400


def send_to_patient(prompt: str, chat_area):
    """Shared send handler: record the doctor's message, get a hardened patient
    reply, and append it. Returns True if a rerun should follow."""
    prompt = prompt.rstrip("\n")
    if prompt == "":
        return False

    util.record(ss.log, f"Doctor: {prompt}")
    chat.append(ss.diagnostic_messages, "doctor", prompt)
    chat.update(chat_area, msgs=ss.diagnostic_messages, height=CHAT_HEIGHT, show_all=ss.show_all)

    # UX-3 / PERF-3: harden the patient generation call. A capped/blocked
    # candidate must never reach response.text (that raises and crashes the
    # page); instead inspect finish_reason / prompt_feedback and let the
    # student retry.
    try:
        response = ss.patient.send_message(f"醫學生：{prompt} （請作為病人回答）")
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


column = st.columns([1, 10, 1, 4])

with column[1]:
    st.header("對話區")
    output_container = st.container()
    chat_area = output_container.empty()

    chat.update(chat_area, msgs=ss.diagnostic_messages, height=CHAT_HEIGHT, show_all=ss.show_all)

    if "patient_model" not in ss and "problem" in ss:
        create_patient_model(ss.problem, prior_messages=ss.diagnostic_messages)

    # 語音輸入：轉成文字後走與文字輸入相同的送出流程。
    # st.audio_input 在 rerun 後仍會保留錄音，故以內容雜湊去重，避免重複送出。
    if audio := st.audio_input("語音輸入"):
        audio_bytes = audio.getvalue()
        audio_id = hash(audio_bytes)
        if ss.get("last_audio_id") != audio_id and util.check_progress():
            ss.last_audio_id = audio_id
            transcript = process_audio(audio)
            if transcript and send_to_patient(transcript, chat_area):
                st.rerun()

# Add a confirm answer button outside the input container
    button_container = st.container()
    with button_container:
        if st.button("完成問診", use_container_width=True) and util.check_progress():
            ss.diagnostic_ended = True

            util.next_page()

with column[3]:
    util.show_patient_profile()

    st.subheader("其他資訊")
    with st.container(border=True):
        util.peek_chat()
        util.show_time()

# st.chat_input must live at the app's top level (it cannot sit inside
# st.columns). Enter submits and the widget clears itself automatically.
if prompt := st.chat_input("請輸入您的對話內容"):
    if util.check_progress():
        if send_to_patient(prompt, chat_area):
            st.rerun()
