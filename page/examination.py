import streamlit as st
from model.examiner import create_text_examiner_model
from model.examiner import create_value_examiner_model
from model.lab_advisor import request_lab_feedback
import util.dialog as dialog
import util.tools as util
import util.constants as const
import util.exam_panels as exam_panels
import util.image_bank as image_bank
from util.reference_parser import parse_reference, is_abnormal, is_critical, is_implausible
import csv
import pandas as pd
import json
import time


def _request_lab_feedback(entry):
    try:
        return request_lab_feedback(ss.problem, entry)
    except Exception as e:
        print(f"Lab advisor error: {e}")
        return ""

ss = st.session_state

util.init(4)
util.note()

# 文字型檢查（使用 text examiner 生成敘述性結果）
TEXT_TYPE_EXAMS = {"X光", "超音波", "CT", "MRI", "其他影像", "心電圖", "功能檢查", "內視鏡"}

# 檢查單（exam cart）：累積跨子類別的待開立檢查細項
if "exam_cart" not in ss:
    ss.exam_cart = []

# 自訂 CSS：異常值標記樣式
st.markdown("""
<style>
.abnormal-high { color: #ff5c5c; font-weight: bold; }
.abnormal-low { color: #5c9eff; font-weight: bold; }
.critical { color: #ffffff; font-weight: bold; background-color: #dc3545; padding: 2px 4px; border-radius: 3px; }
.normal { color: #ffffff; }
.text-abnormal { color: #ff5c5c; font-weight: bold; }
/* 強制檢查結果表格所有欄位（含未個別標記的名稱、單位、參考值）顯示白色 */
#examination-results,
#examination-results td,
#examination-results th {
    color: #ffffff !important;
}
/* 表格內的異常標記仍保留各自顏色（覆蓋上方白色強制） */
#examination-results td .abnormal-high,
#examination-results td .abnormal-high * { color: #ff5c5c !important; }
#examination-results td .abnormal-low,
#examination-results td .abnormal-low * { color: #5c9eff !important; }
#examination-results td .critical,
#examination-results td .critical * { color: #ffffff !important; }
</style>
""", unsafe_allow_html=True)


# examination.csv 欄位順序（與 sheet[0] 一致）：英文名、中文名、參考值、單位
CSV_HEADER = ["englishName", "chineseName", "referenceValue", "unit"]


def parse_text_result(result_text):
    """解析文字型檢查報告結尾的機器可讀標記 [NORMAL]/[ABNORMAL]。

    回傳 (顯示用報告文字, has_abnormal)。
    - 找到 [ABNORMAL] → has_abnormal = True
    - 找到 [NORMAL]   → has_abnormal = False
    - 兩者皆無（模型未遵守）→ 保守起見視為異常 True
    顯示文字會移除該標記行。
    """
    text = result_text or ""
    has_abnormal = True
    upper = text.upper()
    if "[ABNORMAL]" in upper:
        has_abnormal = True
    elif "[NORMAL]" in upper:
        has_abnormal = False
    # 移除標記（不分大小寫）後回傳乾淨報告
    import re as _re
    cleaned = _re.sub(r'\[\s*(NORMAL|ABNORMAL)\s*\]', '', text, flags=_re.IGNORECASE)
    cleaned = cleaned.rstrip()
    return cleaned, has_abnormal


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
        # 合併送單時單一 examiner 回傳會涵蓋多個 subcategory 的項目；
        # 此處僅處理屬於當前 subcategory（full_items_dict）的項目，其餘安靜略過，
        # 避免以例外驅動過濾而產生誤導性的 [Error processing] 日誌。
        if data.get('englishName') not in full_items_dict:
            continue
        try:
            item_info = full_items_dict[data['englishName']]
            ref_str = item_info['reference_value']
            value_str = data['value']

            # 解析參考值並判斷異常
            ref_parsed = parse_reference(ref_str)
            abnormal, direction = is_abnormal(value_str, ref_parsed, gender)
            critical = is_critical(value_str, data['englishName'])
            implausible = is_implausible(value_str, data['englishName'])

            # 偵測到生理上不可能的值：明顯標記並記錄（最小變體：偵測、標記、記錄）
            if implausible:
                has_abnormal = True
                warn_msg = (
                    f"[EXAM] implausible value detected: {data['englishName']}="
                    f"{value_str} (unit={item_info['unit']}, ref={ref_str})"
                )
                print(warn_msg)
                try:
                    util.record(ss.log, warn_msg)
                except Exception:
                    pass
                rows.append({
                    "檢驗項目": data['englishName'],
                    "中文名稱": item_info['chinese_name'],
                    "參考值": ref_str,
                    "檢測值": f'<span class="critical">⚠ {value_str}（數值異常，疑似生成錯誤）</span>',
                    "單位": item_info['unit'],
                })
                continue

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


def _image_query(hist, manifest=None):
    """Derive the (safety-gated) find_image inputs from a text-imaging history
    entry, so attach-time and render-time apply the *same* gate."""
    modality = hist.get("image_modality") or image_bank.resolve_modality(
        hist.get("subcategory", ""),
        " ".join(hist.get("items") or []),
        " ".join(hist.get("items_chinese") or []),
    )
    if not modality:
        return None
    return image_bank.find_image(
        modality,
        has_abnormal=hist.get("has_abnormal", False),
        report_text=hist.get("result_html", "") or "",
        item_terms=(hist.get("items") or []) + (hist.get("items_chinese") or []),
        manifest=manifest,
    ), modality


def _attach_image_ref(entry):
    """For an imaging (text-type) history entry, resolve a real bank image and
    store its manifest id on the entry (persisted; re-validated at render). No-op
    for non-imaging exams, when the examiner's normal/abnormal verdict is unknown
    (we never guess), or when nothing safe matches."""
    try:
        # Normality unknown (examiner omitted the tag) → never guess an image.
        if not entry.get("normality_known"):
            return
        resolved = _image_query(entry)
        if not resolved:
            return
        match, modality = resolved
        if match:
            entry["image_ref"] = match.get("id")
            entry["image_modality"] = modality
    except Exception as e:  # image bank must never break the exam flow
        print(f"[IMAGE] attach failed: {e}")


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

        # 一鍵組套：常見檢查套餐，一次加入多項，免去逐項點選。
        with st.expander("⚡ 一鍵組套（常見檢查套餐）", expanded=False):
            try:
                with open("examination_file/exam_panels.json", "r", encoding="utf-8") as pf:
                    _panels = json.load(pf)
            except (OSError, ValueError):
                _panels = {}
            with open("examination_file/examination.csv", "r", encoding="utf-8") as cf:
                _panel_csv_rows = list(csv.reader(cf))
            _pcols = st.columns(2)
            for _pi, (_pname, _psubs) in enumerate(_panels.items()):
                with _pcols[_pi % 2]:
                    if st.button(f"＋ {_pname}", key=f"panel_{_pi}", use_container_width=True):
                        _entries = exam_panels.expand_panels(
                            _psubs, examination_choice, _panel_csv_rows, TEXT_TYPE_EXAMS
                        )
                        _added = 0
                        for _e in _entries:
                            if any(c["eng"] == _e["eng"] for c in ss.exam_cart):
                                continue
                            ss.exam_cart.append(_e)
                            _added += 1
                        st.success(f"已加入 {_added} 項（{_pname}）")
                        st.rerun()
            st.caption("組套會將該套餐涵蓋的檢查項目一次加入檢查單，可再於下方調整或移除。")

        # 自由輸入逃生口：標準清單未涵蓋的檢查（如 RF、Creatinine、Stool OB、KUB、
        # 四肢 X 光…）仍可開立，避免「想做的檢查沒有」導致案例無法收斂。
        with st.expander("🔎 找不到想做的檢查？自由輸入其他檢查／影像", expanded=False):
            custom_name = st.text_input(
                "檢查名稱（中文或英文皆可）",
                key="custom_exam_name",
                placeholder="例如：Rheumatoid factor (RF)、Stool occult blood、手部 X 光",
            )
            if st.button("加入自訂檢查", use_container_width=True, key="add_custom_exam"):
                name = (custom_name or "").strip()
                if not name:
                    st.warning("請輸入檢查名稱")
                elif any(c["eng"] == name for c in ss.exam_cart):
                    st.info("此自訂檢查已在檢查單中")
                else:
                    ss.exam_cart.append({
                        "category": "自訂",
                        "subcategory": "其他檢查",
                        "display": name,
                        "eng": name,
                        "chinese": name,
                        "reference": "",
                        "unit": "",
                        "result_type": "text",  # 以敘述方式生成結果
                    })
                    st.success(f"已加入自訂檢查：{name}")
                    st.rerun()
            st.caption("自訂檢查結果以敘述方式生成，協助你在標準清單未涵蓋時仍能完成必要檢查。")

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

                if st.button("加入檢查單", use_container_width=True):
                    if not item_names:
                        st.warning("請先選擇至少一個檢查細項")
                    else:
                        added = 0
                        for item in item_names:
                            row = full_options[item]
                            eng = row[0]
                            # 跨子類別累積；避免在檢查單內重複加入同一細項
                            if any(c["eng"] == eng for c in ss.exam_cart):
                                continue
                            ss.exam_cart.append({
                                "category": category,
                                "subcategory": examination,
                                "display": item,
                                "eng": eng,
                                "chinese": row[1],
                                "reference": row[2] if len(row) > 2 else "",
                                "unit": row[3] if len(row) > 3 else "",
                                "result_type": "text" if examination in TEXT_TYPE_EXAMS else "value",
                            })
                            added += 1
                        if added:
                            st.success(f"已加入 {added} 項檢查至檢查單")
                            st.rerun()
                        else:
                            st.info("所選細項皆已在檢查單中")

    def render_cart():
        """渲染檢查單內容，提供逐項移除與清空功能。"""
        with selection_container:
            if not ss.exam_cart:
                return
            st.subheader("檢查單")
            with st.container(border=True):
                for idx, c in enumerate(ss.exam_cart):
                    cols = st.columns([8, 2])
                    with cols[0]:
                        st.markdown(f"**{c['chinese']}** （{c['subcategory']}）`{c['eng']}`")
                    with cols[1]:
                        if st.button("移除", key=f"cart_rm_{idx}", use_container_width=True):
                            ss.exam_cart.pop(idx)
                            st.rerun()
                if st.button("清空檢查單", use_container_width=True):
                    ss.exam_cart = []
                    st.rerun()

    def render_result():
        with result_container:
            if ss.examination_result != []: st.header("檢查結果")

            with st.container(border=True):
                for name, res in ss.examination_result:
                    st.subheader(name)
                    st.markdown(res, unsafe_allow_html=True)

                # §6-A: show the real de-identified image paired with each
                # imaging report so students can practice reading actual films.
                # We re-run the full safety gate against the CURRENT manifest and
                # only show the image if it still resolves to the persisted id —
                # so a re-curated/relabelled bank or a reloaded session can never
                # pair a report with a now-mismatched film.
                man = image_bank.load_manifest()
                for h in ss.examination_history:
                    ref = h.get("image_ref")
                    if not ref or not h.get("normality_known"):
                        continue
                    resolved = _image_query(h, manifest=man)
                    if not resolved:
                        continue
                    match, _modality = resolved
                    if not match or match.get("id") != ref:
                        continue  # binding no longer clears the safety bar → text only
                    meta = image_bank.describe(match)
                    if not meta["path"]:
                        continue
                    st.markdown(f"**{'、'.join(h.get('items_chinese') or [])} — 影像判讀**")
                    st.image(meta["path"], caption=meta["caption"], use_container_width=True)
                    st.caption(meta["badge"])
                    if meta["provenance"]:
                        line = f"來源：{meta['provenance']}"
                        if meta["source_url"]:
                            line += f"（[原始連結]({meta['source_url']})）"
                        st.caption(line)

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

                    target_ddx_value = []
                    if ss.preliminary_ddx:
                        ddx_options = [item["name"] for item in ss.preliminary_ddx]
                        target_ddx_value = st.multiselect(
                            "此檢查欲鑑別/排除哪些初步鑑別？（選填）",
                            options=ddx_options,
                            key=f"target_ddx_{latest['order_number']}",
                        )

                    if st.button("儲存判讀", key=f"save_interp_{latest['order_number']}"):
                        latest["interpretation"] = interp
                        latest["target_ddx"] = target_ddx_value
                        if interp.strip():
                            ai_feedback = _request_lab_feedback(latest)
                            if ai_feedback:
                                latest["ai_feedback"] = ai_feedback
                        st.success("判讀已儲存")
                        st.rerun()
                elif latest.get("ai_feedback"):
                    with st.expander("💡 AI 判讀提示（點擊查看）", expanded=False):
                        st.info(latest["ai_feedback"])

    with button_container:
        st.container(height=50, border=False)

        if st.button("開始檢查", use_container_width=True) and util.check_progress():
            if not ss.exam_cart:
                st.warning("檢查單為空，請先以「加入檢查單」選擇檢查項目")
            else:
                # 檢查重複開立（跨整個檢查單）
                duplicate_items = [c["chinese"] for c in ss.exam_cart if c["eng"] in ss.ordered_exam_set]

                if duplicate_items and not ss.get("confirm_duplicate", False):
                    st.warning(f"以下檢查已做過：{'、'.join(duplicate_items)}。如需重複開立，請再次點擊「開始檢查」。")
                    ss.confirm_duplicate = True
                else:
                    ss.confirm_duplicate = False

                    value_cart = [c for c in ss.exam_cart if c["result_type"] == "value"]
                    text_cart = [c for c in ss.exam_cart if c["result_type"] == "text"]

                    # 交易式開單：成功取得結果的項目才標記「已開立」並移出檢查單；
                    # 失敗（429/5xx/連線）的項目保留在檢查單供重試，不會被重複開立
                    # 檢查誤判成「已做過」。
                    completed = set()
                    exam_failed = False

                    # === 數值型：合併成單一 examiner 呼叫以節省 token ===
                    if value_cart:
                        create_value_examiner_model(ss.problem)
                        # full_items 為 [header, row, row, ...]；row = [eng, chinese, ref, unit]
                        merged_full_items = [CSV_HEADER] + [
                            [c["eng"], c["chinese"], c["reference"], c["unit"]] for c in value_cart
                        ]
                        with st.spinner("進行檢查中..."):
                            try:
                                raw_result = ss.value_examiner.send_message(
                                    f"請為以下檢驗項目生成檢驗結果（每項皆須輸出）：{merged_full_items}"
                                ).text
                            except Exception as e:
                                util.record(ss.log, f"[EXAM] value examiner error: {e}")
                                raw_result = None

                        if not raw_result:
                            exam_failed = True
                        else:
                            # 依子類別分組，逐組產生一筆 examination_history（沿用既有寫入邏輯）
                            seen_order = []
                            groups = {}
                            for c in value_cart:
                                if c["subcategory"] not in groups:
                                    groups[c["subcategory"]] = []
                                    seen_order.append(c["subcategory"])
                                groups[c["subcategory"]].append(c)

                            for subcat in seen_order:
                                items = groups[subcat]
                                sub_full_items = [CSV_HEADER] + [
                                    [c["eng"], c["chinese"], c["reference"], c["unit"]] for c in items
                                ]
                                result_html, has_abnormal = process_examination_result(sub_full_items, raw_result)
                                ss.examination_result.append((subcat, result_html))
                                ss.examination_history.append({
                                    "order_number": len(ss.examination_history) + 1,
                                    "category": items[0]["category"],
                                    "subcategory": subcat,
                                    "items": [c["eng"] for c in items],
                                    "items_chinese": [c["chinese"] for c in items],
                                    "result_type": "value",
                                    "result_html": result_html,
                                    "has_abnormal": has_abnormal,
                                    "interpretation": "",
                                })
                            for c in value_cart:
                                ss.ordered_exam_set.add(c["eng"])
                                completed.add(c["eng"])

                    # === 文字型：每個子類別各自一次 text examiner 呼叫 ===
                    if text_cart and not exam_failed:
                        create_text_examiner_model(ss.problem)
                        seen_order = []
                        groups = {}
                        for c in text_cart:
                            if c["subcategory"] not in groups:
                                groups[c["subcategory"]] = []
                                seen_order.append(c["subcategory"])
                            groups[c["subcategory"]].append(c)

                        for subcat in seen_order:
                            items = groups[subcat]
                            items_chinese = [c["chinese"] for c in items]
                            item_payload = [[c["eng"], c["chinese"]] for c in items]
                            with st.spinner("進行檢查中..."):
                                try:
                                    result_text = ss.text_examiner.send_message(
                                        f"Please provide the examination findings for the following ({subcat}): {item_payload}"
                                    ).text
                                except Exception as e:
                                    util.record(ss.log, f"[EXAM] text examiner error ({subcat}): {e}")
                                    result_text = None
                            if not result_text:
                                exam_failed = True
                                break
                            result_html, has_abnormal = parse_text_result(result_text)
                            # Did the examiner emit an explicit normal/abnormal tag?
                            # If not, has_abnormal is only a conservative default and
                            # must NOT drive image retrieval (§6-A never guesses).
                            _upper = result_text.upper()
                            normality_known = ("[NORMAL]" in _upper) or ("[ABNORMAL]" in _upper)
                            ss.examination_result.append(("、".join(items_chinese), result_html))
                            hist_entry = {
                                "order_number": len(ss.examination_history) + 1,
                                "category": items[0]["category"],
                                "subcategory": subcat,
                                "items": [c["eng"] for c in items],
                                "items_chinese": items_chinese,
                                "result_type": "text",
                                "result_html": result_html,
                                "has_abnormal": has_abnormal,
                                "normality_known": normality_known,
                                "interpretation": "",
                            }
                            # §6-A: pair imaging reports with a real bank image.
                            _attach_image_ref(hist_entry)
                            ss.examination_history.append(hist_entry)
                            for c in items:
                                ss.ordered_exam_set.add(c["eng"])
                                completed.add(c["eng"])

                    # 完成的項目移出檢查單；失敗的保留供重試
                    ss.exam_cart = [c for c in ss.exam_cart if c["eng"] not in completed]
                    if exam_failed:
                        st.warning("部分檢查暫時無法取得結果（系統忙碌或連線問題），"
                                   "未完成的項目仍在檢查單中，請稍後再點一次「開始檢查」。")
                    else:
                        st.rerun()

        if st.button("完成檢查", use_container_width=True) and util.check_progress():
            util.next_page()

    render_cart()
    render_result()
    render_interpretation()

with column[3]:
    util.show_patient_profile()

    # 初步鑑別清單摘要
    if ss.preliminary_ddx:
        st.subheader("初步鑑別清單")
        with st.container(border=True):
            for i, item in enumerate(ss.preliminary_ddx, 1):
                st.markdown(f"**{i}. {item['name']}** （{item.get('likelihood', '中')}）")
                reason = item.get("reason") or item.get("plan") or ""
                if reason:
                    st.caption(f"支持理由：{reason}")

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
                    if entry.get("target_ddx"):
                        st.caption(f"目標鑑別：{'、'.join(entry['target_ddx'])}")
                    if entry.get("interpretation"):
                        st.info(f"判讀：{entry['interpretation']}")
                    if entry.get("ai_feedback"):
                        st.success(f"AI 回饋：{entry['ai_feedback']}")
