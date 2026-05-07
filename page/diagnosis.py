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

    if "chat_input_counter" not in ss:
        ss.chat_input_counter = 0
    input_key = f"chat_input_{ss.chat_input_counter}"
    audio_key = f"audio_input2_{ss.chat_input_counter}"

    if audio := st.audio_input("語音輸入", key=audio_key):
        ss.audio2 = audio
        ss.prompt = process_audio(audio)
        ss.prompt = st.text_area("請輸入您的對話內容", value=ss.prompt, key=input_key)

    chat.update(chat_area, msgs=ss.diagnostic_messages, height=200, show_all=ss.show_all)

    if "audio2" not in ss:
        ss.prompt = st.text_area("請輸入您的對話內容", key=input_key)

    if st.button("送出對話", use_container_width=True) and util.check_progress():
        if ss.prompt != "":
            ss.prompt = ss.prompt.rstrip("\n")
            util.record(ss.log, f"Doctor: {ss.prompt}")

            chat.append(ss.diagnostic_messages, "doctor", ss.prompt)
            chat.update(chat_area, msgs=ss.diagnostic_messages, height=200, show_all=ss.show_all)

            response = ss.patient.send_message(f"醫學生：{ss.prompt}")
            formatted_response = response.text.replace("(", "（").replace(")", "）")
            util.record(ss.log, f"Patient: {response.text}")
            chat.append(ss.diagnostic_messages, "patient", formatted_response)

            ss.chat_input_counter += 1
            ss.prompt = ""
            if "audio2" in ss:
                del ss.audio2
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

