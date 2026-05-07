import math
import streamlit as st
import pandas as pd
import altair as alt
from model.advisor import create_advisor_model
from model.mark_scheme_setter import create_mark_scheme_setter_model
from model.grader_v2 import create_grader_v2_model
from model.acgme_grader import create_acgme_grader_model
import util.dialog as dialog
import util.tools as util
import util.chat as chat
import util.acgme_selector as acgme_selector
import util.acgme_aggregator as acgme_aggregator
import util.save_load as save_load
import datetime
import json
import time

INSTRUCTION_FOLDER = "instruction_file/"
DOMAIN_LABELS = {
    "PC": "病人照護 (PC)",
    "MK": "醫學知識 (MK)",
    "PBLI": "從工作中學習 (PBLI)",
    "ICS": "人際與溝通 (ICS)",
    "PROF": "專業素養 (PROF)",
    "SBP": "制度下臨床 (SBP)",
}
DOMAIN_ORDER = ["PC", "MK", "PBLI", "ICS", "PROF", "SBP"]

ss = st.session_state

util.init(6)
util.note()


# ---- ACGME / 評分區共用樣式 ----
# 各 Level 的色彩語意：L1 灰（beginning）→ L5 紫（expert）
LEVEL_COLORS = {
    0: {"bg": "#3a3a44", "fg": "#cfcfd6", "label": "未評估"},
    1: {"bg": "#6c757d", "fg": "#ffffff", "label": "L1 起步"},
    2: {"bg": "#d97706", "fg": "#ffffff", "label": "L2 發展中"},
    3: {"bg": "#2563eb", "fg": "#ffffff", "label": "L3 達標"},
    4: {"bg": "#16a34a", "fg": "#ffffff", "label": "L4 精熟"},
    5: {"bg": "#7c3aed", "fg": "#ffffff", "label": "L5 卓越"},
}


def level_badge(level: int, *, compact: bool = False) -> str:
    """回傳 Level 的 HTML 徽章字串，可內嵌到 markdown。"""
    lv = max(0, min(5, int(level or 0)))
    c = LEVEL_COLORS[lv]
    label = f"L{lv}" if compact and lv > 0 else c["label"]
    if compact and lv == 0:
        label = "—"
    return (
        f"<span class='lv-badge' style='background:{c['bg']};color:{c['fg']};'>"
        f"{label}</span>"
    )


def level_bar(current_level: int) -> str:
    """5 格進度條，標示目前 Level（0 顯示空條）。"""
    lv = max(0, min(5, int(current_level or 0)))
    cells = []
    for i in range(1, 6):
        if i <= lv:
            color = LEVEL_COLORS[lv]["bg"]
            cells.append(f"<span class='lv-cell on' style='background:{color};'></span>")
        else:
            cells.append("<span class='lv-cell'></span>")
    return f"<div class='lv-bar'>{''.join(cells)}</div>"


st.markdown(
    """
<style>
/* === Level 徽章 === */
.lv-badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 0.82rem;
    font-weight: 600;
    letter-spacing: 0.3px;
    line-height: 1.4;
}
/* === Level 進度條 === */
.lv-bar { display: inline-flex; gap: 3px; vertical-align: middle; }
.lv-cell {
    width: 22px; height: 8px; border-radius: 2px;
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.12);
}
.lv-cell.on { border-color: transparent; }

/* === ACGME 摘要卡片 === */
.acgme-meta {
    background: linear-gradient(135deg, rgba(124,58,237,0.10), rgba(37,99,235,0.10));
    border: 1px solid rgba(124,58,237,0.25);
    border-radius: 10px;
    padding: 12px 16px;
    margin: 6px 0 14px;
    font-size: 0.9rem;
    line-height: 1.65;
}
.acgme-meta b { color: #a78bfa; }
.acgme-meta .pill {
    display: inline-block;
    padding: 1px 8px;
    margin-right: 4px;
    border-radius: 8px;
    background: rgba(167,139,250,0.18);
    color: #c4b5fd;
    font-size: 0.78rem;
}
.acgme-meta .warn {
    color: #fbbf24;
    font-weight: 600;
}

/* === ACGME 細項區塊 === */
.acgme-detail-box {
    background: rgba(255,255,255,0.02);
    border-left: 3px solid rgba(124,58,237,0.55);
    border-radius: 4px;
    padding: 8px 12px;
    margin: 6px 0;
    font-size: 0.92rem;
}
.acgme-detail-box .label {
    color: #a78bfa;
    font-weight: 600;
    margin-right: 4px;
}

/* === 完整評分表細項標題 === */
.acgme-item-head {
    display: flex; align-items: center; gap: 10px;
    font-size: 1.0rem;
}
.acgme-item-head .id-tag {
    background: rgba(255,255,255,0.06);
    padding: 1px 7px; border-radius: 4px;
    font-family: ui-monospace, monospace;
    font-size: 0.82rem;
    color: #cbd5e1;
}

/* 覆蓋率小註腳 */
.acgme-coverage {
    text-align: right;
    color: #94a3b8;
    font-size: 0.82rem;
    margin-top: 6px;
}
</style>
    """,
    unsafe_allow_html=True,
)


# Helper function to run grading models
def get_grading_result_sync(current_model, messages_for_grading):
    grader = current_model.start_chat()
    response = grader.send_message(messages_for_grading)
    return response.text


# === Collect all student data for v2 grading ===
def collect_student_data():
    """Collect all student performance data for mark scheme generation and grading."""
    data_parts = []

    data_parts.append(f"## 虛擬病人設定\n{json.dumps(ss.data, ensure_ascii=False, indent=2)}")

    data_parts.append("## 問診紀錄")
    for msg in ss.diagnostic_messages:
        data_parts.append(f"{msg['role']}：{msg['content']}")

    if ss.pe_result:
        data_parts.append("## 理學檢查紀錄")
        for name, result in ss.pe_result:
            data_parts.append(f"### {name}\n{result}")

    if ss.preliminary_ddx:
        data_parts.append("## 初步鑑別清單（問診+PE 後提出）")
        for i, item in enumerate(ss.preliminary_ddx, 1):
            reason = item.get("reason") or item.get("plan") or ""
            data_parts.append(
                f"- {i}. {item['name']}（可能性：{item.get('likelihood', '中')}）"
                f"｜支持理由：{reason}"
            )

    if ss.examination_history:
        data_parts.append("## 檢查紀錄")
        data_parts.append(f"共開立 {len(ss.examination_history)} 次檢查")

        for entry in ss.examination_history:
            data_parts.append(f"### 第{entry['order_number']}次檢查 — {entry['subcategory']}")
            data_parts.append(f"- 類別：{entry['category']}")
            data_parts.append(f"- 項目：{'、'.join(entry.get('items_chinese', entry['items']))}")
            if entry.get('target_ddx'):
                data_parts.append(f"- 學生標註欲鑑別：{'、'.join(entry['target_ddx'])}")
            if entry.get('interpretation'):
                data_parts.append(f"- 學生判讀：{entry['interpretation']}")
            if entry.get('ai_feedback'):
                data_parts.append(f"- AI 即時回饋：{entry['ai_feedback']}")
            data_parts.append(f"- 結果：\n{entry['result_html']}")

        sequence = " → ".join([f"第{e['order_number']}次:{e['subcategory']}" for e in ss.examination_history])
        data_parts.append(f"\n### 檢查開立順序\n{sequence}")

        all_items = []
        for e in ss.examination_history:
            all_items.extend(e.get('items_chinese', e['items']))
        data_parts.append(f"\n### 已開立檢查項目總覽\n{'、'.join(all_items)}")

    elif ss.examination_result:
        data_parts.append("## 檢查紀錄")
        for name, result in ss.examination_result:
            data_parts.append(f"### {name}\n{result}")

    data_parts.append(f"## 診斷紀錄")
    data_parts.append(f"主診斷：{ss.diagnosis}")
    data_parts.append(f"鑑別診斷（最終保留+新增）：{ss.ddx}")
    if ss.final_ddx_status:
        data_parts.append("### 對初步鑑別的處理")
        for name, status in ss.final_ddx_status.items():
            data_parts.append(f"- {name} → {status}")
    if ss.get("comorbidities"):
        data_parts.append(f"### 已存在共病\n{ss.comorbidities}")
    data_parts.append(f"處置：{ss.treatment}")

    return "\n\n".join(data_parts)


STANDARD_CATEGORIES = [
    '病史詢問', '溝通技巧', '身體檢查與檢驗判讀',
    '臨床推理與鑑別診斷', '疾病處置與治療計畫', '整體專業表現', '全域評級',
]


def _normalize_category(cat):
    if cat in STANDARD_CATEGORIES:
        return cat
    cleaned = cat.replace('_', '').replace(' ', '')
    if cleaned in STANDARD_CATEGORIES:
        return cleaned
    best, best_score = cat, 0
    for std in STANDARD_CATEGORIES:
        score = sum(1 for ch in cleaned if ch in std)
        if score > best_score:
            best, best_score = std, score
    return best


def process_v2_grading_result(input_json):
    grading_result = json.loads(input_json)
    sorted_result = sorted(grading_result, key=lambda x: x['item_id'])

    for item in sorted_result:
        item['category'] = _normalize_category(item['category'])

    regular_items = [item for item in sorted_result if item['category'] != '全域評級']
    global_rating = [item for item in sorted_result if item['category'] == '全域評級']

    categories = {}
    for item in regular_items:
        cat = item['category']
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(item)

    return categories, global_rating


def render_v2_html_table(items):
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


def build_acgme_radar_chart(summary, max_level=5, size=380):
    """6 領域 ACGME Level 雷達圖。資料不足之 domain 以 0 顯示，避免拉高視覺。"""
    n = len(DOMAIN_ORDER)
    angles = [math.pi / 2 - i * 2 * math.pi / n for i in range(n)]

    poly_rows = []
    for i, d in enumerate(DOMAIN_ORDER):
        info = summary.get(d, {})
        insufficient = info.get("insufficient_data", False)
        avg = 0 if insufficient else (info.get("average_level", 0) or 0)
        if insufficient:
            display = "資料不足"
        elif avg > 0:
            display = f"L{avg}"
        else:
            display = "未評估"
        poly_rows.append({
            "領域": DOMAIN_LABELS[d],
            "Level": avg,
            "顯示": display,
            "已評估": info.get("assessed_count", 0),
            "總子能力": info.get("total_count", 0),
            "x": avg * math.cos(angles[i]),
            "y": avg * math.sin(angles[i]),
            "order": i,
        })
    poly_df = pd.DataFrame(poly_rows)

    spoke_rows = []
    for i, d in enumerate(DOMAIN_ORDER):
        spoke_rows.append({"spoke": DOMAIN_LABELS[d], "x": 0.0, "y": 0.0, "order": 0})
        spoke_rows.append({
            "spoke": DOMAIN_LABELS[d],
            "x": max_level * math.cos(angles[i]),
            "y": max_level * math.sin(angles[i]),
            "order": 1,
        })
    spoke_df = pd.DataFrame(spoke_rows)

    grid_rows = []
    for level in range(1, max_level + 1):
        for i in range(n):
            grid_rows.append({
                "level": str(level),
                "x": level * math.cos(angles[i]),
                "y": level * math.sin(angles[i]),
                "order": i,
            })
    grid_df = pd.DataFrame(grid_rows)

    label_radius = max_level + 0.9
    label_rows = []
    for i, d in enumerate(DOMAIN_ORDER):
        label_rows.append({
            "label": DOMAIN_LABELS[d],
            "x": label_radius * math.cos(angles[i]),
            "y": label_radius * math.sin(angles[i]),
        })
    label_df = pd.DataFrame(label_rows)

    level_label_df = pd.DataFrame([
        {"text": f"L{lv}", "x": 0.18, "y": lv}
        for lv in range(1, max_level + 1)
    ])

    extent = max_level + 2.2
    base_x = alt.X("x:Q", axis=None, scale=alt.Scale(domain=[-extent, extent]))
    base_y = alt.Y("y:Q", axis=None, scale=alt.Scale(domain=[-extent, extent]))

    grid = alt.Chart(grid_df).mark_line(
        strokeOpacity=0.25, color="#888", interpolate="linear-closed",
    ).encode(x=base_x, y=base_y, order="order:O", detail="level:N")

    spokes = alt.Chart(spoke_df).mark_line(
        strokeOpacity=0.35, color="#888",
    ).encode(x=base_x, y=base_y, detail="spoke:N", order="order:O")

    perimeter = alt.Chart(poly_df).mark_line(
        color="#7c3aed", strokeWidth=2.5, interpolate="linear-closed",
    ).encode(x=base_x, y=base_y, order="order:O")

    points = alt.Chart(poly_df).mark_point(
        filled=True, size=110, color="#7c3aed", stroke="white", strokeWidth=1.5,
    ).encode(
        x=base_x, y=base_y,
        tooltip=[
            alt.Tooltip("領域:N"),
            alt.Tooltip("顯示:N", title="平均 Level"),
            alt.Tooltip("已評估:Q"),
            alt.Tooltip("總子能力:Q"),
        ],
    )

    domain_label_chart = alt.Chart(label_df).mark_text(
        fontSize=12, fontWeight="bold",
    ).encode(x=base_x, y=base_y, text="label:N")

    level_label_chart = alt.Chart(level_label_df).mark_text(
        fontSize=10, color="#94a3b8", baseline="middle", align="left",
    ).encode(x=base_x, y=base_y, text="text:N")

    return alt.layer(
        grid, spokes, perimeter, points, domain_label_chart, level_label_chart,
    ).properties(width=size, height=size).configure_view(strokeWidth=0)


def reset_grading():
    """Drop cached grading state so the next render re-runs everything."""
    for key in (
        "grader_v2_response", "mark_scheme_raw",
        "advisor", "advisor_model", "advice_messages",
        "v2_score_percentage",
        "acgme_grader_response", "acgme_domain_summary",
        "acgme_milestone_used", "acgme_milestone_data",
        "acgme_error", "acgme_selection_meta",
        "acgme_grader_parsed", "advisor_acgme_briefed",
        "grading_result_saved", "grading_result_file",
    ):
        if key in ss:
            del ss[key]
    ss.advice_messages = []


# ========================================
# 評分流程（首次進入或重新評分時觸發）
# 1) 動態生成 OSCE 評分表（mark scheme）
# 2) v2 grader 依評分表逐項評分
# 3) advisor 以 v2 結果為 priming 建立對話
# ========================================
if "mark_scheme_raw" not in ss and "problem" in ss:
    mark_scheme_model = create_mark_scheme_setter_model()
    patient_setup_str = f"## 虛擬病人設定\n{json.dumps(ss.data, ensure_ascii=False, indent=2)}"
    mark_scheme_prompt = f"請根據以下虛擬病人設定，設計一份OSCE評分表：\n\n{patient_setup_str}"

    with st.spinner("生成評分表中..."):
        _t0 = time.perf_counter()
        ss.mark_scheme_raw = get_grading_result_sync(mark_scheme_model, mark_scheme_prompt)
        util.record(ss.log, f"[PERF] mark_scheme={time.perf_counter() - _t0:.2f}s")
        util.record(ss.log, f"[V2] Mark Scheme: {ss.mark_scheme_raw}")

if "mark_scheme_raw" in ss and "grader_v2_response" not in ss:
    student_data = collect_student_data()
    with st.spinner("AI考官評分中..."):
        _t0 = time.perf_counter()
        grader_v2_model = create_grader_v2_model(ss.mark_scheme_raw)
        grader_v2_chat = grader_v2_model.start_chat()
        grader_v2_response = grader_v2_chat.send_message(
            f"請根據評分表，對以下學生的臨床表現進行逐項評分：\n\n{student_data}"
        )
        ss.grader_v2_response = grader_v2_response.text
        util.record(ss.log, f"[PERF] grader_v2={time.perf_counter() - _t0:.2f}s")
        util.record(ss.log, f"[V2] Grading Result: {ss.grader_v2_response}")

if "advisor" not in ss and "grader_v2_response" in ss:
    _t0 = time.perf_counter()
    create_advisor_model(f"{INSTRUCTION_FOLDER}advisor_instruction.txt")
    util.record(ss.log, f"[PERF] advisor_setup={time.perf_counter() - _t0:.2f}s")


# ========================================
# ACGME 核心能力評核（接 grader_v2 之後）
# ========================================
if "grader_v2_response" in ss and "acgme_grader_response" not in ss and not ss.get("acgme_error"):
    try:
        problem = ss.data.get("Problem", {}) if isinstance(ss.get("data"), dict) else {}
        disease = problem.get("疾病", "")
        symptoms = problem.get("症狀", "")

        selection = acgme_selector.select_milestone(disease, symptoms)
        ss.acgme_milestone_data = selection["milestone_data"]
        ss.acgme_milestone_used = selection["milestone_name"]
        ss.acgme_selection_meta = {
            "selection_reason": selection["selection_reason"],
            "matched_key": selection["matched_key"],
            "fallback_reason": selection["fallback_reason"],
            "excluded_subcompetencies": selection.get("excluded_subcompetencies", []),
        }
        util.record(ss.log, f"[ACGME] milestone={ss.acgme_milestone_used} "
                            f"reason={selection['selection_reason']} "
                            f"matched={selection['matched_key']!r} "
                            f"fallback={selection['fallback_reason']}")
        excluded = selection.get("excluded_subcompetencies", [])
        if excluded:
            excluded_brief = ", ".join(
                f"{e['id']}({e['domain']}:{e.get('name_en','')})" for e in excluded
            )
            util.record(ss.log, f"[ACGME] excluded_subcompetencies={excluded_brief}")

        student_data = collect_student_data()
        v2_summary = ss.grader_v2_response
        acgme_input = (
            f"## 病例資訊\n疾病：{disease}\n症狀：{symptoms}\n\n"
            f"## 學員完整表現\n{student_data}\n\n"
            f"## OSCE Grader v2 評分結果（供參考，請勿直接複製其分數判定 ACGME Level）\n{v2_summary}\n\n"
            f"請對提供的每一個 ACGME 子能力逐項評定 Milestone Level，並引用學員具體表現作為佐證。"
        )

        learner_role = ss.get("acgme_learner_role")
        util.record(ss.log, f"[ACGME] learner_role={learner_role.get('id') if learner_role else 'default(pgy1)'}")
        with st.spinner("ACGME 核心能力評核中..."):
            _t0 = time.perf_counter()
            acgme_model = create_acgme_grader_model(ss.acgme_milestone_data, learner_role)
            acgme_chat = acgme_model.start_chat()
            acgme_response = acgme_chat.send_message(acgme_input)
            ss.acgme_grader_response = acgme_response.text
            util.record(ss.log, f"[PERF] acgme_grader={time.perf_counter() - _t0:.2f}s")
            util.record(ss.log, f"[ACGME] Grading Result: {ss.acgme_grader_response}")

        # 彙總到 6 domain
        try:
            parsed = json.loads(ss.acgme_grader_response)
            parsed = acgme_aggregator.reconcile_missing_subcompetencies(
                parsed, ss.acgme_milestone_data
            )
            ss.acgme_domain_summary = acgme_aggregator.aggregate_to_domains(
                parsed, ss.acgme_milestone_data
            )
            ss.acgme_grader_parsed = parsed
            util.record(
                ss.log,
                "[ACGME] domain_summary=" + json.dumps(
                    {d: {"avg": v["average_level"], "n": v["assessed_count"]}
                     for d, v in ss.acgme_domain_summary.items()},
                    ensure_ascii=False,
                ),
            )
        except Exception as e:
            ss.acgme_error = True
            util.record(ss.log, f"[ACGME] aggregator failed: {e}")
    except FileNotFoundError as e:
        ss.acgme_error = True
        util.record(ss.log, f"[ACGME] milestone file missing: {e}")
    except Exception as e:
        ss.acgme_error = True
        util.record(ss.log, f"[ACGME] grader failed: {e}")


# ========================================
# 自動存檔：V2 完成且 ACGME 已完成或已失敗時觸發一次
# ========================================
if (
    "grader_v2_response" in ss
    and not ss.get("grading_result_saved")
    and ("acgme_grader_response" in ss or ss.get("acgme_error"))
):
    try:
        ss.grading_result_file = save_load.save_grading_result()
        ss.grading_result_saved = True
        util.record(ss.log, f"[GRADE] auto-saved grading result: {ss.grading_result_file}")
    except Exception as e:
        util.record(ss.log, f"[GRADE] auto-save failed: {e}")


# ========================================
# Layout - Summary dashboard + V2 primary
# ========================================
st.header("📋 OSCE 評分結果")
st.caption("根據 OSCE 國際標準，動態生成專屬於本次案例的評分表，並由 AI 考官逐項評分。")
if ss.get("grading_result_file"):
    st.caption(f"✅ 本次評分結果已自動存檔：`data/grading_results/{ss.grading_result_file}`")

if "grader_v2_response" in ss:
    categories, global_rating = process_v2_grading_result(ss.grader_v2_response)

    # Compute totals
    total_score, total_max = 0, 0
    for items in categories.values():
        for it in items:
            total_score += int(it['score'])
            total_max += int(it['max_score'])

    score_pct = round(total_score / total_max * 100, 1) if total_max else 0
    ss.v2_score_percentage = score_pct

    # === Top dashboard（包進有邊框容器，視覺統一）===
    rating_labels = {1: "不及格", 2: "邊緣", 3: "通過", 4: "優良", 5: "傑出"}
    with st.container(border=True):
        summary_cols = st.columns(3)
        with summary_cols[0]:
            st.metric("總得分", f"{total_score} / {total_max}")
        with summary_cols[1]:
            st.metric("得分率", f"{score_pct}%")
        with summary_cols[2]:
            if global_rating:
                gr = global_rating[0]
                st.metric(
                    "全域評級",
                    f"{gr['score']} / 5",
                    delta=rating_labels.get(int(gr['score']), ''),
                    delta_color="off",
                )
            else:
                st.metric("全域評級", "—")

        if global_rating:
            st.info(f"**考官綜合評語：** {global_rating[0]['feedback']}")

    # === V2 detail tabs ===
    category_names = list(categories.keys())
    if category_names:
        st.markdown("#### 各類別細項")
        v2_tabs = st.tabs(category_names)
        for i, cat_name in enumerate(category_names):
            with v2_tabs[i]:
                items = categories[cat_name]
                html_table, cat_max, cat_score = render_v2_html_table(items)
                cat_pct = round(cat_score / cat_max * 100) if cat_max else 0
                with st.container(border=True):
                    head_l, head_r = st.columns([3, 1])
                    with head_l:
                        st.markdown(f"**{cat_name}**")
                    with head_r:
                        st.markdown(
                            f"<div style='text-align:right;color:#cbd5e1;'>"
                            f"<b>{cat_score} / {cat_max}</b> "
                            f"<span style='color:#94a3b8;'>（{cat_pct}%）</span></div>",
                            unsafe_allow_html=True,
                        )
                    with st.container(height=320):
                        st.markdown(html_table, unsafe_allow_html=True)

    with st.expander("查看本次生成的 OSCE 評分表", expanded=False):
        mark_scheme_items = json.loads(ss.mark_scheme_raw)
        for item in sorted(mark_scheme_items, key=lambda x: x['item_id']):
            st.markdown(
                f"**{item['item_id']}. [{item['category']}]** {item['description']}（配分：{item['max_score']}）\n"
                f"> {item['scoring_guide']}"
            )


# ========================================
# 🎯 ACGME 核心能力評估區塊
# ========================================
if "grader_v2_response" in ss:
    st.divider()
    st.subheader("🎯 ACGME 核心能力評估")

    if ss.get("acgme_error"):
        st.warning("ACGME 評核暫時無法產生。可點選下方「🔄 重新評分」重試。")
    elif "acgme_domain_summary" in ss and "acgme_grader_parsed" in ss:
        meta = ss.get("acgme_selection_meta", {})
        milestone_data = ss.get("acgme_milestone_data", {})
        used = ss.get("acgme_milestone_used", "internal_medicine")
        learner_role = ss.get("acgme_learner_role")

        # === 標頭資訊卡（取代多列 caption，整合 milestone + learner role）===
        meta_pills = [
            f"<span class='pill'>📚 {used}</span>",
            f"<span class='pill'>🏷 {milestone_data.get('version', '-')}</span>",
        ]
        if meta.get("matched_key"):
            meta_pills.append(f"<span class='pill'>🎯 {meta['matched_key']}</span>")
        if learner_role:
            meta_pills.append(
                f"<span class='pill'>👤 {learner_role.get('label', '')}（預期 L"
                f"{learner_role.get('level_low')}–L{learner_role.get('level_high')}）</span>"
            )

        meta_html = "<div class='acgme-meta'>" + "".join(meta_pills)
        meta_html += f"<div style='margin-top:6px;'>選用原因：{meta.get('selection_reason', '-')}"
        if meta.get("fallback_reason"):
            meta_html += f" <span class='warn'>⚠ {meta['fallback_reason']}</span>"
        meta_html += "</div></div>"
        st.markdown(meta_html, unsafe_allow_html=True)

        # OSCE 不適用之子能力提示
        excluded = meta.get("excluded_subcompetencies", [])
        if excluded:
            with st.expander(
                f"ℹ️ 已排除 {len(excluded)} 項 OSCE 不適用子能力（不納入評核）",
                expanded=False,
            ):
                for e in excluded:
                    st.markdown(
                        f"- **{e['id']}（{e['domain']}）** {e.get('name_zh','')} "
                        f"／ {e.get('name_en','')}"
                    )
                st.caption(
                    "排除原因：本系統為單次門診 OSCE 模擬，無 EHR/Telehealth、無團隊互動、"
                    "無跨次反思資料、無體系政策面決策；上述子能力結構性無法在此情境評估。"
                )

        summary = ss.acgme_domain_summary

        # === Domain Dashboard ===
        # 為避免「資料不足」domain（assessed_count<2）拉高雷達圖／長條圖視覺，
        # chart 只繪製非 insufficient 的 domain。
        chart_rows = []
        for d in DOMAIN_ORDER:
            info = summary.get(d, {})
            if info.get("insufficient_data"):
                continue
            chart_rows.append({
                "領域": DOMAIN_LABELS[d],
                "平均 Level": info.get("average_level", 0),
                "已評估": info.get("assessed_count", 0),
                "總子能力": info.get("total_count", 0),
            })
        chart_df = pd.DataFrame(chart_rows)

        with st.container(border=True):
            col_chart, col_metrics = st.columns([3, 2])
            with col_chart:
                radar_tab, bar_tab = st.tabs(["雷達圖", "長條圖"])
                with radar_tab:
                    radar = build_acgme_radar_chart(summary)
                    st.altair_chart(radar, use_container_width=False)
                    st.caption("資料不足之領域於雷達圖中以 0 表示（避免拉高視覺），請以右側摘要為準。")
                with bar_tab:
                    if not chart_df.empty:
                        bar = (
                            alt.Chart(chart_df)
                            .mark_bar(cornerRadiusEnd=4, height=22)
                            .encode(
                                x=alt.X(
                                    "平均 Level:Q",
                                    scale=alt.Scale(domain=[0, 5]),
                                    axis=alt.Axis(values=[0, 1, 2, 3, 4, 5], title=None),
                                ),
                                y=alt.Y(
                                    "領域:N",
                                    sort=[DOMAIN_LABELS[d] for d in DOMAIN_ORDER],
                                    axis=alt.Axis(title=None, labelLimit=160),
                                ),
                                color=alt.Color(
                                    "平均 Level:Q",
                                    scale=alt.Scale(
                                        domain=[1, 2, 3, 4, 5],
                                        range=[
                                            LEVEL_COLORS[1]["bg"],
                                            LEVEL_COLORS[2]["bg"],
                                            LEVEL_COLORS[3]["bg"],
                                            LEVEL_COLORS[4]["bg"],
                                            LEVEL_COLORS[5]["bg"],
                                        ],
                                    ),
                                    legend=None,
                                ),
                                tooltip=["領域", "平均 Level", "已評估", "總子能力"],
                            )
                            .properties(height=260)
                        )
                        st.altair_chart(bar, use_container_width=True)
                    else:
                        st.info("各領域評核資料皆不足，無法繪製圖表。")

                # Level 色彩圖例（雷達圖與長條圖共用）
                legend_html = (
                    "<div style='display:flex;gap:6px;flex-wrap:wrap;"
                    "margin-top:6px;font-size:0.78rem;'>"
                )
                for lv in (1, 2, 3, 4, 5):
                    legend_html += level_badge(lv) + " "
                legend_html += "</div>"
                st.markdown(legend_html, unsafe_allow_html=True)

            with col_metrics:
                st.markdown("##### 各領域摘要")
                # 6 個 domain 排成 2 列 × 3 欄，視覺更平衡
                rows_layout = [DOMAIN_ORDER[:3], DOMAIN_ORDER[3:]]
                for row in rows_layout:
                    cols = st.columns(3)
                    for col, d in zip(cols, row):
                        info = summary.get(d, {})
                        assessed = info.get("assessed_count", 0)
                        total = info.get("total_count", 0)
                        with col:
                            if info.get("insufficient_data"):
                                st.metric(
                                    d,
                                    "—",
                                    delta=f"資料不足 {assessed}/{total}",
                                    delta_color="off",
                                    help=f"{DOMAIN_LABELS[d]}：本領域可評估子能力過少（<2 項），"
                                         f"平均 Level 樣本不足，不予顯示。",
                                )
                            else:
                                avg = info.get("average_level", 0)
                                st.metric(
                                    d,
                                    f"L{avg}" if avg > 0 else "—",
                                    delta=f"{assessed}/{total}",
                                    delta_color="off",
                                    help=DOMAIN_LABELS[d],
                                )

        # === 完整評分表（6 domain tabs）===
        st.markdown("#### 完整 ACGME 評分表細項")
        domain_tabs = st.tabs([DOMAIN_LABELS[d] for d in DOMAIN_ORDER])
        items_by_domain = {d: [] for d in DOMAIN_ORDER}
        for item in ss.acgme_grader_parsed:
            d = item.get("domain")
            if d in items_by_domain:
                items_by_domain[d].append(item)

        for i, d in enumerate(DOMAIN_ORDER):
            with domain_tabs[i]:
                items = sorted(items_by_domain[d], key=lambda x: x.get("subcompetency_id", ""))
                if not items:
                    st.info("本案例未提供此領域的子能力評核資料。")
                    continue
                if summary.get(d, {}).get("insufficient_data"):
                    st.warning(
                        "⚠ 本領域僅 1 項子能力可評估，平均 Level 樣本不足，"
                        "上方儀表板已標示「資料不足」；以下細項僅供參考。"
                    )
                # === 領域內逐項細節（不再使用 dataframe + expander 重複呈現）===
                for it in items:
                    lvl = int(it.get("level", 0) or 0)
                    sub_id = it.get("subcompetency_id", "")
                    sub_name = it.get("subcompetency_name", "")
                    # expander 標題保持為文字（Streamlit 不支援 HTML）
                    head_label = f"{sub_id}　{sub_name}　— " + (
                        LEVEL_COLORS[lvl]["label"] if lvl else "未評估"
                    )
                    with st.expander(head_label, expanded=False):
                        # 子能力標題列：徽章 + 進度條
                        st.markdown(
                            f"<div class='acgme-item-head'>"
                            f"<span class='id-tag'>{sub_id}</span>"
                            f"<span style='font-weight:600;'>{sub_name}</span>"
                            f"{level_badge(lvl)}{level_bar(lvl)}"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                        # 對應 milestone 描述
                        sub_def = next(
                            (s for s in milestone_data.get("subcompetencies", [])
                             if s["id"] == sub_id),
                            None,
                        )
                        if sub_def and 1 <= lvl <= 5:
                            st.markdown(
                                f"<div class='acgme-detail-box'>"
                                f"<span class='label'>📖 對應 Milestone Level {lvl} 描述</span><br/>"
                                f"{sub_def['levels'].get(str(lvl), '')}</div>",
                                unsafe_allow_html=True,
                            )

                        # 評級三要素：理由、佐證、改進
                        for label_text, key, icon in (
                            ("評級理由", "level_rationale", "🧭"),
                            ("佐證（學員表現）", "evidence", "🔍"),
                            ("改進建議", "improvement", "💡"),
                        ):
                            if it.get(key):
                                st.markdown(
                                    f"<div class='acgme-detail-box'>"
                                    f"<span class='label'>{icon} {label_text}</span> "
                                    f"{it[key]}</div>",
                                    unsafe_allow_html=True,
                                )

                        if sub_def:
                            with st.expander("查看本子能力 Level 1-5 完整描述", expanded=False):
                                for ll in ("1", "2", "3", "4", "5"):
                                    desc = sub_def["levels"].get(ll, "")
                                    is_current = str(lvl) == ll
                                    badge = level_badge(int(ll), compact=True)
                                    bg = (
                                        f"background:rgba(124,58,237,0.08);"
                                        f"border-left:3px solid {LEVEL_COLORS[int(ll)]['bg']};"
                                        if is_current else
                                        f"border-left:3px solid rgba(255,255,255,0.06);"
                                    )
                                    st.markdown(
                                        f"<div style='{bg}padding:6px 10px;margin:4px 0;"
                                        f"border-radius:4px;font-size:0.9rem;'>"
                                        f"{badge} {'<b>' if is_current else ''}{desc}"
                                        f"{'</b>' if is_current else ''}</div>",
                                        unsafe_allow_html=True,
                                    )

        assessed_total, total_total = acgme_aggregator.overall_coverage(summary)
        excluded_count = len(meta.get("excluded_subcompetencies", []))
        coverage_pct = round(assessed_total / total_total * 100) if total_total else 0
        coverage_html = (
            f"<div class='acgme-coverage'>"
            f"OSCE 適用子能力 <b style='color:#cbd5e1;'>{total_total}</b> 項　｜　"
            f"本案評估 <b style='color:#cbd5e1;'>{assessed_total}</b> 項　（覆蓋率 {coverage_pct}%）"
        )
        if excluded_count:
            coverage_html += f"　｜　OSCE 不適用而排除 {excluded_count} 項"
        coverage_html += "</div>"
        st.markdown(coverage_html, unsafe_allow_html=True)
    else:
        st.info("ACGME 評核生成中，請稍候…")


# ========================================
# Lab interpretation summary
# ========================================
if ss.examination_history and any(e.get("interpretation") or e.get("ai_feedback") for e in ss.examination_history):
    st.divider()
    st.subheader("🧪 Lab 判讀總覽")
    st.caption("您每次檢查後輸入的判讀及 AI 即時回饋彙整於此。")
    for entry in ss.examination_history:
        if not (entry.get("interpretation") or entry.get("ai_feedback")):
            continue
        with st.expander(f"第{entry['order_number']}次：{entry['subcategory']}", expanded=False):
            if entry.get("target_ddx"):
                st.caption(f"目標鑑別：{'、'.join(entry['target_ddx'])}")
            if entry.get("interpretation"):
                st.markdown(f"**學生判讀：**{entry['interpretation']}")
            if entry.get("ai_feedback"):
                st.markdown(f"**AI 回饋：**{entry['ai_feedback']}")


# ========================================
# 重點摘要
# ========================================
st.divider()
st.subheader("📌 重點摘要")
_problem = ss.data.get("Problem", {}) if isinstance(ss.get("data"), dict) else {}
_disease = _problem.get("疾病", "—")
_treatment = _problem.get("處置方式", "—")
_score_pct_v2 = ss.get("v2_score_percentage", 0)
with st.container(border=True):
    for icon, label, value in (
        ("📊", "本次得分率", f"{_score_pct_v2}%"),
        ("🩺", "病人疾病", _disease),
        ("💊", "標準處置", _treatment),
    ):
        label_col, value_col = st.columns([1, 4])
        with label_col:
            st.markdown(f"**{icon} {label}**")
        with value_col:
            st.markdown(value)


# ========================================
# Advisor Q&A
# ========================================
st.divider()
st.subheader("💬 顧問問答")

if "advisor_qa_messages" not in ss:
    ss.advisor_qa_messages = []

qa_container = st.container()
qa_chat_area = qa_container.empty()

if ss.advisor_qa_messages:
    chat.update(qa_chat_area, ss.advisor_qa_messages, height=400, show_all=True)
else:
    st.info("💡 在下方輸入框輸入想詢問 AI 顧問的關於此次問診的問題，即可獲得專業回饋。")

if "advisor" in ss:
    if (prompt := st.chat_input("輸入您對評分的問題")) and util.check_progress():
        chat.append(ss.advisor_qa_messages, "student", prompt)
        chat.update(qa_chat_area, msgs=ss.advisor_qa_messages, height=400, show_all=True)

        # 首次提問時，把 ACGME 評核結果一併送給 advisor 作為背景
        prefix = ""
        if ss.get("acgme_grader_parsed") and not ss.get("advisor_acgme_briefed"):
            try:
                acgme_brief = json.dumps(
                    [
                        {
                            "id": it.get("subcompetency_id"),
                            "name": it.get("subcompetency_name"),
                            "domain": it.get("domain"),
                            "level": it.get("level"),
                            "rationale": it.get("level_rationale"),
                            "evidence": it.get("evidence"),
                            "improvement": it.get("improvement"),
                        }
                        for it in ss.acgme_grader_parsed
                    ],
                    ensure_ascii=False,
                )
                summary_brief = json.dumps(
                    {d: {"avg_level": v["average_level"],
                         "assessed": v["assessed_count"],
                         "total": v["total_count"]}
                     for d, v in ss.acgme_domain_summary.items()},
                    ensure_ascii=False,
                )
                prefix = (
                    f"（系統補充：本次 ACGME 核心能力評核結果如下，供你回饋學生使用。）\n"
                    f"## ACGME 6 領域摘要\n{summary_brief}\n\n"
                    f"## ACGME 子能力細項\n{acgme_brief}\n\n"
                )
                ss.advisor_acgme_briefed = True
            except Exception as e:
                util.record(ss.log, f"[ACGME] advisor brief failed: {e}")

        response = ss.advisor.send_message(f"{prefix}學生：{prompt}")
        chat.append(ss.advisor_qa_messages, "advisor", response.text)
        chat.update(qa_chat_area, msgs=ss.advisor_qa_messages, height=400, show_all=True)


# ========================================
# Bottom buttons
# ========================================
st.divider()
subcolumns = st.columns(3)

with subcolumns[0]:
    if st.button("🔄 重新評分", use_container_width=True, help="若您回退補做了問診、檢查或診斷，點此重新評分"):
        reset_grading()
        st.rerun()

with subcolumns[1]:
    if st.button("結束評分", use_container_width=True) and util.check_progress():
        dialog.refresh()

with subcolumns[2]:
    if st.button("儲存本次病患設定", use_container_width=True) and util.check_progress():
        data = ss.data
        file_name = f"{datetime.datetime.now().strftime('%Y%m%d')} - {data['基本資訊']['姓名']} - {data['Problem']['疾病']} - {ss.get('v2_score_percentage', 0)}%.json"
        with open(f"data/problem_set/{file_name}", "w") as f:
            f.write(ss.problem)

        dialog.config_saved(file_name)
