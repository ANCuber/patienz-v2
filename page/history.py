import streamlit as st
from model.patient import create_patient_model
import util.tools as util
import util.chat as chat

ss = st.session_state

util.init(1)
util.note()

CHAT_HEIGHT = 400
PATIENT_SUFFIX = " （請作為病人回答）"

column = st.columns([1, 10, 1, 4])

with column[1]:
    st.header("對話區")
    output_container = st.container()
    chat_area = output_container.empty()

    chat.update(chat_area, msgs=ss.diagnostic_messages, height=CHAT_HEIGHT, show_all=ss.show_all)

    if "patient_model" not in ss and "problem" in ss:
        create_patient_model(ss.problem, prior_messages=ss.diagnostic_messages)

    # 語音輸入：轉成文字後走與文字輸入相同的送出流程。
    transcript = chat.new_voice_transcript("audio_input_history")
    if transcript and chat.send_to_patient(transcript, chat_area, CHAT_HEIGHT, PATIENT_SUFFIX):
        st.rerun()

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
    if util.check_progress() and chat.send_to_patient(prompt, chat_area, CHAT_HEIGHT, PATIENT_SUFFIX):
        st.rerun()
