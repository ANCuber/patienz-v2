import streamlit as st
from model.examiner import create_text_examiner_model
from model.examiner import create_value_examiner_model
import util.dialog as dialog
import util.tools as util
import util.constants as const
from util.reference_parser import parse_reference, is_abnormal, is_critical
import csv
import pandas as pd
import json
import time

ss = st.session_state

util.init(3)
util.note()

# 文字型檢查（使用 text examiner 生成敘述性結果）
TEXT_TYPE_EXAMS = {"X光", "超音波", "CT", "MRI", "其他影像", "心電圖", "功能檢查", "內視鏡"}

# 自訂 CSS：異常值標記樣式
st.markdown("""
<style>
.abnormal-high { color: #dc3545; font-weight: bold; }
.abnormal-low { color: #0d6efd; font-weight: bold; }
.critical { color: #dc3545; font-weight: bold; background-color: #fff3cd; padding: 2px 4px; border-radius: 3px; }
.normal { color: #212529; }
</style>
""", unsafe_allow_html=True)


def process_examination_result(full_items, result_json):
    """處理數值型檢查結果，加入異常值標記。"""
    examination_result = json.loads(result_json)

    full_items_dict = {item[0]: {
        "chinese_name": item[1],
        "reference_value": item[2],
        "unit": item[3],
    } for item in full_items}

    # 取得病人性別
    gender = ss.data["基本資訊"]["性別"] if "data" in ss else None

    rows = []
    has_abnormal = False

    for data in examination_result['value_type_item']:
        try:
            item_info = full_items_dict[data['englishName']]
            ref_str = item_info['reference_value']
            value_str = data['value']

            # 解析參考值並判斷異常
            ref_parsed = parse_reference(ref_str)
            abnormal, direction = is_abnormal(value_str, ref_parsed, gender)
            critical = is_critical(value_str, data['englishName'])

            # 格式化檢測值顯示
            if critical:
                has_abnormal = True
                display_value = f'<span class="critical">⚠ {value_str} {direction}</span>'
            elif abnormal:
                has_abnormal = True
                css_class = "abnormal-high" if direction == "↑" else "abnormal-low"
                display_value = f'<span class="{css_class}">{value_str} {direction}</span>'
            else:
                display_value = f'<span class="normal">{value_str}</span>'

            rows.append({
                "檢驗項目": data['englishName'],
                "中文名稱": item_info['chinese_name'],
                "參考值": ref_str,
                "檢測值": display_value,
                "單位": item_info['unit'],
            })
        except Exception as e:
            print(f"Error processing {data.get('englishName', 'unknown')}: {e}")

    df = pd.DataFrame(rows)
    left_align = lambda x: f"<div style='text-align: left;'>{x}</div>"
    cent_align = lambda x: f"<div style='text-align: center;'>{x}</div>"

    if df.empty:
        return "發生錯誤，請重新檢查。", False

    html_table = df.to_html(
        index=False,
        escape=False,
        classes="dataframe table",
        table_id="examination-results",
        col_space="5em",
        formatters=[left_align, left_align, cent_align, cent_align, cent_align],
        justify="center",
    )

    return html_table, has_abnormal


column = st.columns([1, 10, 1, 4])

with column[1]:
    selection_container = st.container()
    button_container = st.container()
    result_container = st.container()
    interpretation_container = st.container()

    with selection_container:
        st.header("檢查選擇")

        with open("examination_file/examination_choice.json", "r", encoding="utf-8") as f:
            examination_choice = json.load(f)

        category = st.radio("檢查領域", examination_choice.keys(), horizontal=True)

        if category != None:

            examination = st.radio("檢查項目", examination_choice[category].keys(), horizontal=True)

            if examination != None:
                l, r = int(examination_choice[category][examination]['l']-1), int(examination_choice[category][examination]['r']-1)

                with open("examination_file/examination.csv", "r", encoding="utf-8") as f:
                    sheet = list(csv.reader(f))
                    display_options = [f"{row[1]} {row[0]}" for row in sheet[l:r] if len(row) >= 2 and row[0].strip()]
                    full_options = {f"{row[1]} {row[0]}": row for row in sheet if len(row) >= 2 and row[0].strip()}

                # 標記已開立的檢查
                marked_options = []
                for opt in display_options:
                    eng_name = full_options[opt][0]
                    if eng_name in ss.ordered_exam_set:
                        marked_options.append(f"{opt}（已檢查）")
                    else:
                        marked_options.append(opt)

                # 建立標記名稱到原始名稱的對應
                marked_to_original = dict(zip(marked_options, display_options))

                if examination in const.default_all:
                    item_names_marked = st.multiselect("檢查細項", options=marked_options, default=marked_options)
                else:
                    item_names_marked = st.multiselect("檢查細項", marked_options)

                # 轉換回原始名稱
                item_names = [marked_to_original[m] for m in item_names_marked]

    def render_result():
        with result_container:
            if ss.examination_result != []: st.header("檢查結果")

            with st.container(border=True):
                for name, res in ss.examination_result:
                    st.subheader(name)
                    st.markdown(res, unsafe_allow_html=True)

    # 結果判讀區
    def render_interpretation():
        with interpretation_container:
            if ss.examination_history:
                latest = ss.examination_history[-1]
                if not latest.get("interpretation"):
                    st.subheader("結果判讀")
                    interp = st.text_area(
                        "請簡述您對上述檢查結果的判讀（選填，將納入評分參考）",
                        key=f"interp_{latest['order_number']}",
                        height=100,
                        placeholder="例如：血紅素偏低，白血球升高，懷疑感染合併貧血..."
                    )
                    if st.button("儲存判讀", key=f"save_interp_{latest['order_number']}"):
                        latest["interpretation"] = interp
                        st.success("判讀已儲存")
                        st.rerun()

    with button_container:
        st.container(height=50, border=False)

        if st.button("開始檢查", use_container_width=True) and util.check_progress():
            # 檢查重複開立
            duplicate_items = []
            new_items = []
            for item in item_names:
                eng_name = full_options[item][0]
                if eng_name in ss.ordered_exam_set:
                    duplicate_items.append(item)
                else:
                    new_items.append(item)

            if duplicate_items and not ss.get("confirm_duplicate", False):
                st.warning(f"以下檢查已做過：{'、'.join(duplicate_items)}。如需重複開立，請再次點擊「開始檢查」。")
                ss.confirm_duplicate = True
            else:
                ss.confirm_duplicate = False

                # 記錄已開立項目
                for item in item_names:
                    ss.ordered_exam_set.add(full_options[item][0])

                full_items = [sheet[0]]
                full_items += [full_options[item] for item in item_names]

                items_english = [full_options[item][0] for item in item_names]
                items_chinese = [full_options[item][1] for item in item_names]

                if examination in TEXT_TYPE_EXAMS:
                    create_text_examiner_model(ss.problem, ", ".join([item[0] for item in full_items]))
                    with st.spinner("進行檢查中..."):
                        result_text = ss.examiner.send_message(
                            f"Please provide the examination findings for the following: {full_items}"
                        ).text
                        result_html = result_text
                        has_abnormal = True  # 文字型預設標記

                    ss.examination_result.append(("、".join(items_chinese), result_html))

                else:
                    create_value_examiner_model(ss.problem, ", ".join([item[0] for item in full_items]))
                    with st.spinner("進行檢查中..."):
                        raw_result = ss.examiner.send_message(f"{full_items}").text
                        result_html, has_abnormal = process_examination_result(full_items, raw_result)

                    ss.examination_result.append((examination, result_html))

                # 記錄到 examination_history
                ss.examination_history.append({
                    "order_number": len(ss.examination_history) + 1,
                    "category": category,
                    "subcategory": examination,
                    "items": items_english,
                    "items_chinese": items_chinese,
                    "result_type": "text" if examination in TEXT_TYPE_EXAMS else "value",
                    "result_html": result_html,
                    "has_abnormal": has_abnormal,
                    "interpretation": "",
                })

                st.rerun()

        if st.button("完成檢查", use_container_width=True) and util.check_progress():
            util.next_page()

    render_result()
    render_interpretation()

with column[3]:
    util.show_patient_profile()

    # 檢查歷史摘要
    if ss.examination_history:
        st.header("已開立檢查")
        with st.container(border=True):
            for entry in ss.examination_history:
                indicator = "🔴" if entry.get("has_abnormal") else "🟢"
                interp_icon = "📝" if entry.get("interpretation") else ""
                with st.expander(
                    f"{indicator} 第{entry['order_number']}次：{entry['subcategory']} {interp_icon}",
                    expanded=False
                ):
                    st.caption(f"類別：{entry['category']}")
                    st.caption(f"項目數：{len(entry['items'])}")
                    items_display = "、".join(entry['items_chinese'][:5])
                    if len(entry['items_chinese']) > 5:
                        items_display += f"...等{len(entry['items_chinese'])}項"
                    st.caption(f"項目：{items_display}")
                    if entry.get("interpretation"):
                        st.info(f"判讀：{entry['interpretation']}")
