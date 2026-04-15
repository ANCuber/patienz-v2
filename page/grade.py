from concurrent.futures import ThreadPoolExecutor
import streamlit as st
import pandas as pd
from model.grader import create_grader_model
from model.advisor import create_advisor_model
from model.mark_scheme_setter import create_mark_scheme_setter_model
from model.grader_v2 import create_grader_v2_model
import util.dialog as dialog
import util.tools as util
import util.chat as chat
import datetime
import json

INSTRUCTION_FOLDER = "instruction_file/"

ss = st.session_state

util.init(5)
util.note()

# Helper function to run grading models
def get_grading_result_sync(current_model, messages_for_grading):
    grader = current_model.start_chat()
    response = grader.send_message(messages_for_grading)
    return response.text


# Process grading results into a DataFrame
def process_grading_result(input_json):
    grading_result = json.loads(input_json)
    sorted_result = sorted(grading_result, key=lambda x: x['id'])

    rows = []
    for data in sorted_result:
        rows.append({
            "項目": data['item'],
            "回饋": data['feedback'],
            "得分": int(data['real_score']),
            "配分": int(data['full_score']),
        })

    df = pd.DataFrame(rows)
    return df, df["配分"].sum(), df["得分"].sum()


def render_html_table(df):
    left_align = lambda x: f"<div style='text-align: left;'>{x}</div>"
    cent_align = lambda x: f"<div style='text-align: center;'>{x}</div>"

    html_table = df.to_html(
        index=False,
        escape=False,
        classes="dataframe table",
        table_id="grading-results",
        col_space="4em",
        formatters=[left_align, left_align, cent_align, cent_align],
        justify="center",
    )

    return html_table


# === Collect all student data for v2 grading ===
def collect_student_data():
    """Collect all student performance data for mark scheme generation and grading."""
    data_parts = []

    # 1. Patient setup
    data_parts.append(f"## 虛擬病人設定\n{json.dumps(ss.data, ensure_ascii=False, indent=2)}")

    # 2. Interview records
    data_parts.append("## 問診紀錄")
    for msg in ss.diagnostic_messages:
        data_parts.append(f"{msg['role']}：{msg['content']}")

    # 3. Physical examination records
    if ss.pe_result:
        data_parts.append("## 理學檢查紀錄")
        for name, result in ss.pe_result:
            data_parts.append(f"### {name}\n{result}")

    # 4. Examination records (enhanced with ordering metadata)
    if ss.examination_history:
        data_parts.append("## 檢查紀錄")
        data_parts.append(f"共開立 {len(ss.examination_history)} 次檢查")

        for entry in ss.examination_history:
            data_parts.append(f"### 第{entry['order_number']}次檢查 — {entry['subcategory']}")
            data_parts.append(f"- 類別：{entry['category']}")
            data_parts.append(f"- 項目：{'、'.join(entry.get('items_chinese', entry['items']))}")
            if entry.get('interpretation'):
                data_parts.append(f"- 學生判讀：{entry['interpretation']}")
            data_parts.append(f"- 結果：\n{entry['result_html']}")

        # 檢查開立順序摘要
        sequence = " → ".join([f"第{e['order_number']}次:{e['subcategory']}" for e in ss.examination_history])
        data_parts.append(f"\n### 檢查開立順序\n{sequence}")

        # 已開立檢查項目總覽
        all_items = []
        for e in ss.examination_history:
            all_items.extend(e.get('items_chinese', e['items']))
        data_parts.append(f"\n### 已開立檢查項目總覽\n{'、'.join(all_items)}")

    elif ss.examination_result:
        # 向後相容：使用舊版資料結構
        data_parts.append("## 檢查紀錄")
        for name, result in ss.examination_result:
            data_parts.append(f"### {name}\n{result}")

    # 5. Diagnosis records
    data_parts.append(f"## 診斷紀錄")
    data_parts.append(f"主診斷：{ss.diagnosis}")
    data_parts.append(f"鑑別診斷：{ss.ddx}")
    data_parts.append(f"處置：{ss.treatment}")

    return "\n\n".join(data_parts)


STANDARD_CATEGORIES = [
    '病史詢問', '溝通技巧', '身體檢查與檢驗判讀',
    '臨床推理與鑑別診斷', '疾病處置與治療計畫', '整體專業表現', '全域評級',
]


def _normalize_category(cat):
    """Normalize AI-generated category name to standard name.

    Handles token corruption (e.g. '病史詢_詢問' → '病史詢問') by matching
    the standard category with the highest character overlap.
    """
    if cat in STANDARD_CATEGORIES:
        return cat
    # Strip underscores/spaces that may be inserted by the LLM
    cleaned = cat.replace('_', '').replace(' ', '')
    if cleaned in STANDARD_CATEGORIES:
        return cleaned
    # Fuzzy match: pick the standard category sharing the most characters
    best, best_score = cat, 0
    for std in STANDARD_CATEGORIES:
        score = sum(1 for ch in cleaned if ch in std)
        if score > best_score:
            best, best_score = std, score
    return best


def process_v2_grading_result(input_json):
    """Process grader_v2 JSON result into categorized DataFrames."""
    grading_result = json.loads(input_json)
    sorted_result = sorted(grading_result, key=lambda x: x['item_id'])

    # Normalize category names to handle AI token corruption
    for item in sorted_result:
        item['category'] = _normalize_category(item['category'])

    # Separate global rating from regular items
    regular_items = [item for item in sorted_result if item['category'] != '全域評級']
    global_rating = [item for item in sorted_result if item['category'] == '全域評級']

    # Group by category
    categories = {}
    for item in regular_items:
        cat = item['category']
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(item)

    return categories, global_rating


def render_v2_html_table(items):
    """Render a category's items as an HTML table."""
    rows = []
    for item in items:
        rows.append({
            "項目": item['description'],
            "回饋": item['feedback'],
            "得分": int(item['score']),
            "配分": int(item['max_score']),
        })

    df = pd.DataFrame(rows)

    left_align = lambda x: f"<div style='text-align: left;'>{x}</div>"
    cent_align = lambda x: f"<div style='text-align: center;'>{x}</div>"

    html_table = df.to_html(
        index=False,
        escape=False,
        classes="dataframe table",
        table_id="grading-v2-results",
        col_space="4em",
        formatters=[left_align, left_align, cent_align, cent_align],
        justify="center",
    )

    return html_table, df["配分"].sum(), df["得分"].sum()


# ========================================
# Layout - Original Grading Section
# ========================================
st.header("基準評分")
st.caption("依據固定評分表對五大面向進行標準化評分，作為基準參考分數。")

tabs = st.tabs(["病況詢問", "病史詢問", "溝通技巧與感情支持", "鑑別診斷", "疾病處置"])

# Run grading models in parallel using ThreadPoolExecutor
if ss.current_progress == 5 and "advisor" not in ss:
    grader_models = [create_grader_model(f"{INSTRUCTION_FOLDER}grader_inst_{chr(65+i)}.txt") for i in range(5)]
    chat_history = f"***醫學生與病人的對話紀錄如下：***\n"
    chat_history += "\n".join([f"{msg['role']}：{msg['content']}" for msg in ss.diagnostic_messages])
    if ss.pe_result:
        chat_history += f"\n***理學檢查結果：***\n"
        for name, res in ss.pe_result:
            chat_history += f"{name}：{res}\n"
    chat_history += f"\n特別注意：**以下是實習醫師的主診斷：{ss.diagnosis}**"
    chat_history += f"\n特別注意：**以下是實習醫師的鑑別診斷：{ss.ddx}**"
    chat_history += f"\n特別注意：**以下是實習醫師的處置：{ss.treatment}**"
    chat_history += f"***醫學生與病人的對話紀錄結束***\n"

    answer_for_grader = f"以下JSON記錄的為正確診斷與病人資訊：\n{ss.data}\n"
    messages = [chat_history if i <= 2 else answer_for_grader + chat_history for i in range(5)]

    def run_models_sync():
        with ThreadPoolExecutor(max_workers=5) as executor:
            tasks = [executor.submit(get_grading_result_sync, model, msg) for model, msg in zip(grader_models, messages)]
            return [task.result() for task in tasks]

    with st.spinner("評分中..."):
        ss.grading_responses = run_models_sync()

    total = 0

    for i, response in enumerate(ss.grading_responses):
        util.record(ss.log, response)

        df, full_score, real_score = process_grading_result(response)
        total += real_score / full_score

    ss.score_percentage = round(total * 20, 1)
    ss.advice_messages = [{"role": "advisor", "content": f"你的平均得分率是：{ss.score_percentage}%"}, {"role": "advisor", "content": f"此病人的疾病是：{ss.data['Problem']['疾病']}"}, {"role": "advisor", "content": f"您應該進行的處置是：{ss.data['Problem']['處置方式']}"}]

    create_advisor_model(f"{INSTRUCTION_FOLDER}advisor_instruction.txt")

if "advisor" in ss:
    for i, response in enumerate(ss.grading_responses):
        df, full_score, real_score = process_grading_result(response)

        with tabs[i]:
            st.subheader(f"細項評分")
            with st.expander(f"本領域獲得分數：（{real_score}/{full_score}）", expanded=True):
                with st.container(height=350):
                    st.markdown(render_html_table(df), unsafe_allow_html=True)

st.subheader("建議詢問")

output_container = st.container()
chat_area = output_container.empty()

chat.update(chat_area, ss.advice_messages, height=350, show_all=True)


# ========================================
# V2 Grading Section - 二代評分區
# ========================================
st.divider()
st.header("📋 二代評分區（OSCE標準評分）")
st.caption("根據OSCE國際標準，動態生成專屬於本次案例的評分表，並由AI考官逐項評分。")

if "advisor" in ss:
    # Run v2 grading if not yet done
    if "grader_v2_response" not in ss:
        # Mark scheme setter only sees patient setup (no student performance data)
        patient_setup = f"## 虛擬病人設定\n{json.dumps(ss.data, ensure_ascii=False, indent=2)}"
        # Grader v2 sees all student data
        student_data = collect_student_data()

        with st.spinner("正在生成OSCE評分表..."):
            # Step 1: Generate mark scheme (based on patient setup only)
            mark_scheme_model = create_mark_scheme_setter_model()
            mark_scheme_chat = mark_scheme_model.start_chat()
            mark_scheme_response = mark_scheme_chat.send_message(
                f"請根據以下虛擬病人設定，設計一份OSCE評分表：\n\n{patient_setup}"
            )
            ss.mark_scheme_raw = mark_scheme_response.text
            util.record(ss.log, f"[V2] Mark Scheme: {ss.mark_scheme_raw}")

        with st.spinner("AI考官評分中..."):
            # Step 2: Grade using the mark scheme + full student data
            grader_v2_model = create_grader_v2_model(ss.mark_scheme_raw)
            grader_v2_chat = grader_v2_model.start_chat()
            grader_v2_response = grader_v2_chat.send_message(
                f"請根據評分表，對以下學生的臨床表現進行逐項評分：\n\n{student_data}"
            )
            ss.grader_v2_response = grader_v2_response.text
            util.record(ss.log, f"[V2] Grading Result: {ss.grader_v2_response}")

    # Display v2 grading results
    if "grader_v2_response" in ss:
        categories, global_rating = process_v2_grading_result(ss.grader_v2_response)

        # Show mark scheme in expander
        with st.expander("查看本次生成的OSCE評分表", expanded=False):
            mark_scheme_items = json.loads(ss.mark_scheme_raw)
            for item in sorted(mark_scheme_items, key=lambda x: x['item_id']):
                st.markdown(
                    f"**{item['item_id']}. [{item['category']}]** {item['description']}（配分：{item['max_score']}）\n"
                    f"> {item['scoring_guide']}"
                )

        # Create tabs for each category
        category_names = list(categories.keys())
        if category_names:
            v2_tabs = st.tabs(category_names)

            total_score = 0
            total_max = 0

            for i, cat_name in enumerate(category_names):
                items = categories[cat_name]
                with v2_tabs[i]:
                    html_table, cat_max, cat_score = render_v2_html_table(items)
                    total_score += cat_score
                    total_max += cat_max
                    st.subheader("細項評分")
                    with st.expander(f"本類別獲得分數：（{cat_score}/{cat_max}）", expanded=True):
                        with st.container(height=350):
                            st.markdown(html_table, unsafe_allow_html=True)

            # Score summary
            if total_max > 0:
                score_pct = round(total_score / total_max * 100, 1)
                ss.v2_score_percentage = score_pct

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("總得分", f"{total_score}/{total_max}")
                with col2:
                    st.metric("得分率", f"{score_pct}%")
                with col3:
                    if global_rating:
                        gr = global_rating[0]
                        rating_labels = {1: "不及格", 2: "邊緣", 3: "通過", 4: "優良", 5: "傑出"}
                        rating_label = rating_labels.get(int(gr['score']), "")
                        st.metric("全域評級", f"{gr['score']}/5（{rating_label}）")

            # Global rating feedback
            if global_rating:
                gr = global_rating[0]
                st.info(f"**考官綜合評語：**{gr['feedback']}")


# ========================================
# Advisor Q&A Section
# ========================================
st.divider()
st.subheader("💬 顧問問答")

if "advisor_qa_messages" not in ss:
    ss.advisor_qa_messages = []

qa_container = st.container()
qa_chat_area = qa_container.empty()

if ss.advisor_qa_messages:
    chat.update(qa_chat_area, ss.advisor_qa_messages, height=400, show_all=True)

if "advisor" in ss:
    if (prompt := st.chat_input("輸入您對評分的問題")) and util.check_progress():
        chat.append(ss.advisor_qa_messages, "student", prompt)
        chat.update(qa_chat_area, msgs=ss.advisor_qa_messages, height=400, show_all=True)

        response = ss.advisor.send_message(f"學生：{prompt}")
        chat.append(ss.advisor_qa_messages, "advisor", response.text)
        chat.update(qa_chat_area, msgs=ss.advisor_qa_messages, height=400, show_all=True)

# ========================================
# Bottom Buttons
# ========================================
subcolumns = st.columns(2)

with subcolumns[0]:
    # End grading button
    if st.button("結束評分", use_container_width=True) and util.check_progress():
        dialog.refresh()

with subcolumns[1]:
    # Save grading data
    if st.button("儲存本次病患設定", use_container_width=True) and util.check_progress():
        data = ss.data
        file_name = f"{datetime.datetime.now().strftime('%Y%m%d')} - {data['基本資訊']['姓名']} - {data['Problem']['疾病']} - {ss.score_percentage}%.json"
        with open(f"data/problem_set/{file_name}", "w") as f:
            f.write(ss.problem)

        dialog.config_saved(file_name)
