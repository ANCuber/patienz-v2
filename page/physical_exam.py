import streamlit as st
from model.examiner import create_pe_examiner_model
import util.dialog as dialog
import util.tools as util
import util.constants as const
import json

ss = st.session_state

util.init(2)
util.note()

column = st.columns([1, 10, 1, 4])

with column[1]:
    selection_container = st.container()
    button_container = st.container()
    result_container = st.container()

    with selection_container:
        st.header("理學檢查")

        with open("examination_file/pe_choice.json", "r", encoding="utf-8") as f:
            pe_choice = json.load(f)

        category = st.radio("檢查部位", pe_choice.keys(), horizontal=True)

        if category != None:
            subcategory = st.radio("檢查項目", pe_choice[category].keys(), horizontal=True)

            if subcategory != None:
                items = pe_choice[category][subcategory]
                selected_items = st.multiselect("檢查細項", options=items, default=items)

    def render_result():
        with result_container:
            if ss.pe_result != []: st.header("理學檢查結果")

            with st.container(border=True):
                for name, res in ss.pe_result:
                    st.subheader(name)
                    st.markdown(res, unsafe_allow_html=True)

    with button_container:
        st.container(height=50, border=False)
        if st.button("開始理學檢查", use_container_width=True) and util.check_progress():
            if not selected_items:
                st.warning("請至少選擇一個檢查細項")
            else:
                items_str = f"{category} - {subcategory}: {', '.join(selected_items)}"
                create_pe_examiner_model(ss.problem, items_str)
                with st.spinner("進行理學檢查中..."):
                    strict_prompt = (
                        f"使用者僅請求進行子類別「{subcategory}」（屬於「{category}」），"
                        f"請僅回報此子類別對應手法的發現，不可主動列出其他手法的所見。"
                        f"具體細項：{', '.join(selected_items)}。"
                        f"請依系統指令的「各子類別對應的手法」嚴格遵守。"
                    )
                    result = ss.pe_examiner.send_message(strict_prompt).text
                    ss.pe_result.append((f"{category} - {subcategory}", result))

                st.rerun()

        if st.button("完成理學檢查", use_container_width=True) and util.check_progress():
            util.next_page()

    render_result()

with column[3]:
    util.show_patient_profile()
