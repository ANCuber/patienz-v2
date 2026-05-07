import streamlit as st 
import util.constants as const

# with emoji titles
@st.dialog("歡迎 👋")
def welcome():
    st.write("歡迎使用本系統")
    st.write("您可以在左邊的選單選取不同的功能")
    st.write("請先完成病患設定並開始看診模擬")
    if st.button("開始"):
        st.switch_page("page/config.py")

@st.dialog("進入新區域！")
def intro(page_id: int):
    for text in const.intro[page_id]:
        st.write(text)

@st.dialog("頁面錯誤 ❌")
def page_error(page_id: int, current_progress: int):
    st.write(f"您尚未完成{const.noun[current_progress]}")
    st.write(f"請先完成{const.noun[current_progress]}才能進入{const.noun[page_id]}")

    if st.button(f"返回{const.noun[current_progress]}區"):
        st.switch_page(f"page/{const.section_name[current_progress]}.py")

@st.dialog("錯誤 ❌")
def error(e, dest=None):
    st.write(e)
    if dest:
        if st.button("確認"):
            st.switch_page(f"page/{dest}.py")

@st.dialog("存檔成功 ✅")
def config_saved(file_name: str):
    st.write(f"本次病患之設定已儲存為：")
    st.write(file_name)
    if st.button("確認"):
        st.switch_page("page/grade.py")

@st.dialog("完成問診 ✅")
def refresh():
    st.write("恭喜您完成了本次的問診")
    st.write("請點擊「確認」以重新開始")

    if st.button("確認"):
        for key in st.session_state:
            print(f"Deleting {key}")
            del st.session_state[key]
        st.switch_page("page/config.py")
