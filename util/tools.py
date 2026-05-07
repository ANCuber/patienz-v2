import shutil
import os
# import requests
# import concurrent.futures
# import pdfkit
from googlesearch import search 

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time
import datetime
import base64

import streamlit as st
import util.constants as const
import util.dialog as dialog
import util.save_load as save_load

ss = st.session_state 

def next_page():
    if ss.page_id == ss.current_progress:
        ss.current_progress = (ss.current_progress + 1) % len(const.section_name)
        target = ss.current_progress
    else:
        target = min(ss.page_id + 1, len(const.section_name) - 1)
    st.switch_page(f"page/{const.section_name[target]}.py")

def init_all():
    if "sid" not in ss:
        ss.sid = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        ss.log = f"data/log/{ss.sid}.txt"
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

        ss.start_time = [None for _ in range(len(const.section_name))]
        ss.cur_show_all, ss.show_all = False, False

def init(page_id: int):
    ss.page_id = page_id

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
        if ss.current_progress == i:
            elapsed_time = int(time.time() - ss.start_time[i])
        else:
            elapsed_time = int(ss.start_time[i + 1] - ss.start_time[i])
        st.write(f"{const.noun[i]}時間：{elapsed_time // 60}:{elapsed_time % 60:02d}")

    if ss.current_progress > 0:
        if ss.current_progress < grade_idx:
            elapsed_time = int(time.time() - ss.start_time[1])
        else:
            elapsed_time = int(ss.start_time[grade_idx] - ss.start_time[1])

        st.write(f"總時間：{elapsed_time // 60}:{elapsed_time % 60:02d}")

def peek_chat():
    ss.show_all = st.checkbox("偷看對話紀錄", ss.show_all)
    if ss.show_all != ss.cur_show_all:
        ss.cur_show_all = ss.show_all
        st.rerun()

def note():
    if "note" not in ss:
        ss.note = ""

    with st.sidebar:
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
                st.caption(f"🔒 {label}")

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
    if ss.page_id > ss.current_progress:
        dialog.page_error(ss.page_id, ss.current_progress)
        return False

    return True


def getPDF(query, output_pdf):
    """
    Takes a search query, finds the first website, and exports it as a PDF.

    :param query: The search query string.
    :param output_pdf: The name of the output PDF file.
    """

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")  # Use the updated headless mode syntax
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--remote-allow-origins=*")  # Required for Chrome 124+

# Disable Chrome's PDF Viewer to force download behavior (optional for CDP method)
    chrome_options.add_experimental_option('prefs', {
        "plugins.always_open_pdf_externally": True,
        "download.prompt_for_download": False
    })

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), 
        options=chrome_options
    )

    try:
        print(f"Searching for: {query}")
        search_results = list(search(query, num_results=1))
        print(f"Found {len(search_results)} search results")

        driver.get(search_results[0])
        time.sleep(5)

        # Configure PDF printing parameters
        print_options = {
            "landscape": False,
            "displayHeaderFooter": False,
            "printBackground": True,
            "preferCSSPageSize": True,
            "margin": {"top": "0.5in", "bottom": "0.5in", "left": "0.5in", "right": "0.5in"}
        }

        # Execute CDP command to generate PDF
        result = driver.execute_cdp_cmd("Page.printToPDF", print_options)

        # Decode and save the PDF
        pdf_data = base64.b64decode(result['data'])
        with open(output_pdf, "wb") as f:
            f.write(pdf_data)

        driver.quit()

    except Exception as e:
        print(f"An error occurred: {e}")

    # Check if the output PDF was created, if not, copy the error PDF
    if not os.path.exists(output_pdf):
        print(f"No PDF generated, copying error PDF to {output_pdf}")
        shutil.copy("tmp/error.pdf", output_pdf)


def record(file, text):
    """
    Records the text to a file.

    :param file: The file to write to.
    :param text: The text to write.
    """
    with open(file, "a", encoding="utf-8") as f:
        f.write(text + "\n")

    print(f"Recorded: {text}")

# Example usage (if running this file directly):
if __name__ == "__main__":
    query = "Uptodate diabetes"
    output = "output"
    getPDF(query, output)


"""
def getPDF2(query, output_pdf):
    '''
    Takes a search query, finds the first website, and exports it as a PDF.

    :param query: The search query string.
    :param output_pdf: The name of the output PDF file.
    '''

    if os.path.exists(output_pdf):
        print(f"File already exists: {output_pdf}")
        os.remove(output_pdf)
        print(f"Deleted {output_pdf}")

    try:
        print(f"Searching for: {query}")
        search_results = list(search(query, num_results=5))
        print(f"Found {len(search_results)} search results")

        for url in search_results:
            try:
                response = requests.get(url, timeout=5)

                if response.status_code != 200:
                    print(f"Failed to access URL: {url}")
                    continue

                print(f"Exporting {url} to {output_pdf}...")
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(pdfkit.from_url, url, output_pdf)
                    try:
                        future.result(timeout=20)
                        print(f"PDF saved as {output_pdf}")
                        break
                    except concurrent.futures.TimeoutError:
                        print(f"Timeout occurred during PDF generation for URL: {url}")
                        continue
                    except Exception as e:
                        print(f"An error occurred during PDF generation: {e}")
                        continue
            except requests.exceptions.Timeout:
                print(f"Timeout occurred for URL: {url}")
                continue
            except Exception as e:
                print(f"An error occurred: {e}")
                continue

    except Exception as e:
        print(f"An error occurred: {e}")
        return

    # Check if the output PDF was created, if not, copy the error PDF
    if not os.path.exists(output_pdf):
        print(f"No PDF generated, copying error PDF to {output_pdf}")
        shutil.copy("tmp/error.pdf", output_pdf)


"""
