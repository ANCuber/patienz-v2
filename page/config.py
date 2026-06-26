import streamlit as st
from model.problem_setter import create_problem_setter_model
import util.dialog as dialog
import util.tools as util
import util.save_load as save_load
import util.constants as const
import util.demographics as demographics
import os
import random
import json
import datetime
import time

ss = st.session_state

util.init(0)
util.note()

with open("examination_file/config_options.json", "r", encoding="utf-8") as f:
    OPTS = json.load(f)


def parse_lines(text):
    if not text:
        return []
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def serialize_config(cfg):
    sections = []

    basic = []
    if cfg.get("年齡") is not None:
        basic.append(f"年齡：{cfg['年齡']}")
    if cfg.get("性別"):
        basic.append(f"性別：{cfg['性別']}")
    if basic:
        sections.append("【基本條件】\n" + "\n".join(basic))

    disease = []
    if cfg.get("出題模式"):
        disease.append(f"出題模式：{cfg['出題模式']}")
    if cfg.get("疾病領域"):
        disease.append(f"疾病領域：{cfg['疾病領域']}")
    if cfg.get("目標疾病清單"):
        disease.append("目標疾病清單：")
        for d in cfg["目標疾病清單"]:
            disease.append(f"  - {d}")
    if cfg.get("指定鑑別診斷清單"):
        disease.append("指定鑑別診斷清單：")
        for d in cfg["指定鑑別診斷清單"]:
            disease.append(f"  - {d}")
    if cfg.get("主訴症狀"):
        disease.append(f"主訴症狀：{cfg['主訴症狀']}")
    if disease:
        sections.append("【疾病設定】\n" + "\n".join(disease))

    clinical = []
    if cfg.get("難度"):
        clinical.append(f"難度：{cfg['難度']}")
    if cfg.get("就診情境"):
        clinical.append(f"就診情境：{cfg['就診情境']}")
    if cfg.get("看診時間"):
        clinical.append(f"看診時間：{cfg['看診時間']}")
    if cfg.get("急性度"):
        clinical.append(f"急性度：{cfg['急性度']}")
    if cfg.get("共病程度"):
        clinical.append(f"共病程度：{cfg['共病程度']}")
    if cfg.get("主訴提示風格"):
        clinical.append(f"主訴提示風格：{cfg['主訴提示風格']}")
    if clinical:
        sections.append("【臨床情境】\n" + "\n".join(clinical))

    teaching = []
    if cfg.get("適合年級"):
        teaching.append(f"適合年級：{cfg['適合年級']}")
    if cfg.get("教學重點"):
        teaching.append(f"教學重點：{'、'.join(cfg['教學重點'])}")
    if teaching:
        sections.append("【教學設定】\n" + "\n".join(teaching))

    if cfg.get("額外要求"):
        sections.append("【額外要求】\n" + cfg["額外要求"])

    return "\n\n".join(sections)


config = {
    "年齡": None,
    "性別": None,
    "疾病領域": None,
    "出題模式": None,
    "目標疾病清單": None,
    "指定鑑別診斷清單": None,
    "主訴症狀": None,
    "難度": None,
    "就診情境": None,
    "看診時間": None,
    "急性度": None,
    "共病程度": None,
    "主訴提示風格": None,
    "適合年級": None,
    "教學重點": None,
    "額外要求": None,
}

save_file = None

major_column = st.columns([2, 8, 2])

with major_column[1]:
    st.header("病患資訊設定")

    # === 學員身份（影響 ACGME 評核基準） ===
    learner_options = OPTS.get("learner_role_options", [])
    if learner_options:
        labels = [r["label"] for r in learner_options]
        # 預設指向 PGY-1（若不存在則第一項）
        default_idx = next(
            (i for i, r in enumerate(learner_options) if r["id"] == "pgy1"),
            0,
        )
        # 沿用先前選擇
        if ss.get("acgme_learner_role"):
            try:
                default_idx = next(
                    i for i, r in enumerate(learner_options)
                    if r["id"] == ss.acgme_learner_role.get("id")
                )
            except StopIteration:
                pass
        chosen_label = st.selectbox(
            "學員身份（用於 ACGME 評核基準）",
            labels,
            index=default_idx,
            help="不同訓練階段對應不同的 Milestone Level 預期；evaluator 會依此調整評級基準。",
        )
        chosen = next(r for r in learner_options if r["label"] == chosen_label)
        ss.acgme_learner_role = chosen
        st.caption(
            f"預期 Level：**{chosen['level_low']}–{chosen['level_high']}** ｜ {chosen['description']}"
        )

    # === 評分模式（從一開始分流，避免每次都跑兩套評分而拉長等待） ===
    GRADING_MODE_OPTIONS = {
        "OSCE + ACGME（完整）": "both",
        "僅 OSCE（較快）": "osce",
        "僅 ACGME": "acgme",
    }
    _gm_labels = list(GRADING_MODE_OPTIONS.keys())
    _gm_default = 0
    if ss.get("grading_mode"):
        _gm_default = next(
            (i for i, lab in enumerate(_gm_labels) if GRADING_MODE_OPTIONS[lab] == ss.grading_mode),
            0,
        )
    _gm_label = st.selectbox(
        "評分模式",
        _gm_labels,
        index=_gm_default,
        help="OSCE 為逐項配分的考官評分；ACGME 為六大核心能力 Milestone 評級。"
             "只選一套可顯著縮短評分等待時間。",
    )
    ss.grading_mode = GRADING_MODE_OPTIONS[_gm_label]

    # === 病人個性／情緒（訓練不同應對方式；不影響病情事實） ===
    persona_options = OPTS.get("patient_persona_options", [])
    if persona_options:
        _persona_labels = [p["label"] for p in persona_options]
        _persona_default = 0
        if ss.get("patient_persona"):
            _persona_default = next(
                (i for i, p in enumerate(persona_options) if p["id"] == ss.patient_persona.get("id")),
                0,
            )
        _persona_label = st.selectbox(
            "病人個性／情緒（選填）",
            _persona_labels,
            index=_persona_default,
            help="讓虛擬病人表現出不同個性（焦慮、不耐煩、話少…），訓練不同的醫病溝通應對；病情事實不受影響。",
        )
        ss.patient_persona = next(p for p in persona_options if p["label"] == _persona_label)

    # === 流程模式（§7 流程彈性） ===
    ss.free_navigation = st.checkbox(
        "自由探索模式（可自由切換問診／理學／檢查等階段，不強制順序）",
        value=ss.get("free_navigation", False),
        help="開啟後可在各看診階段間自由前進／返回，貼近真實臨床的交錯流程（如先做篩檢、檢查後再補問診）。"
             "關閉則維持標準 OSCE 逐步順序，評分會評估你提出鑑別診斷的時機。",
    )

    ss.config_type = st.radio("選擇設定方式", ["模板題", "輸入參數", "題目存檔", "進度存檔"], horizontal=True)

    if ss.config_type == "輸入參數":
        st.subheader("基本條件")
        minor_column_1 = st.columns([10, 1, 10])
        with minor_column_1[0]:
            config["年齡"] = st.slider("年齡（隨機區間）", 0, 100, (15, 100))
        with minor_column_1[2]:
            config["性別"] = st.radio("性別", ["隨機", "男", "女"], horizontal=True)

        field_choices = OPTS["field_specials"] + OPTS["field_options"]
        config["疾病領域"] = st.selectbox("疾病領域", field_choices)

        st.subheader("出題模式")
        mode = st.radio("出題模式", OPTS["mode_options"], horizontal=True)
        config["出題模式"] = mode

        if mode == "指定症狀":
            symptom_pick = st.selectbox(
                "常見症狀（病人主訴）",
                OPTS["symptom_options"],
                index=None,
                placeholder="請選擇症狀...",
            )
            symptom_free = st.text_input(
                "或自由輸入其他症狀（選填）",
                placeholder="例：手抖、視力模糊",
            )
            config["主訴症狀"] = (symptom_free.strip() if symptom_free and symptom_free.strip() else symptom_pick) or None
        elif mode == "指定疾病":
            disease_picks = st.multiselect(
                "常見病態（可複選快速加入）",
                OPTS["common_disease_options"],
                placeholder="從清單挑選...",
            )
            disease_text = st.text_area(
                "或自由輸入疾病（每行一個，可與上方併用）",
                placeholder="例：\n急性支氣管炎\n肺結核",
                height=100,
            )
            merged = list(dict.fromkeys(disease_picks + parse_lines(disease_text)))
            config["目標疾病清單"] = merged or None
        elif mode == "指定鑑別診斷":
            ddx_text = st.text_area(
                "指定鑑別診斷（每行一個，案例會被設計成這些都需被排除）",
                placeholder="例：\n肺栓塞\n心肌梗塞\n主動脈剝離",
                height=120,
            )
            config["指定鑑別診斷清單"] = parse_lines(ddx_text) or None

        st.subheader("就診情境")
        setting_column = st.columns([10, 1, 10])
        with setting_column[0]:
            setting = st.selectbox("就診情境", OPTS["setting_options"])
            config["就診情境"] = setting if setting != "隨機" else None
        with setting_column[2]:
            visit_time = st.selectbox("看診時間", OPTS["visit_time_options"])
            config["看診時間"] = visit_time if visit_time != "隨機" else None

        with st.expander("進階選項（選填）", expanded=False):
            adv_col_1 = st.columns([10, 1, 10])
            with adv_col_1[0]:
                difficulty = st.select_slider("難度", OPTS["difficulty_options"], value="中等")
                config["難度"] = difficulty if difficulty != "中等" else None
            with adv_col_1[2]:
                comorbidity = st.select_slider("共病程度", OPTS["comorbidity_options"], value="一般")
                config["共病程度"] = comorbidity if comorbidity != "一般" else None

            adv_col_2 = st.columns([10, 1, 10])
            with adv_col_2[0]:
                acuity = st.selectbox("急性度", OPTS["acuity_options"])
                config["急性度"] = acuity if acuity != "隨機" else None

            adv_col_3 = st.columns([10, 1, 10])
            with adv_col_3[0]:
                style = st.selectbox("主訴提示風格", OPTS["complaint_style_options"])
                config["主訴提示風格"] = style if style != "隨機" else None
            with adv_col_3[2]:
                grade = st.selectbox("適合年級", OPTS["grade_options"])
                config["適合年級"] = grade if grade != "不指定" else None

            focus = st.multiselect("教學重點（可複選）", OPTS["teaching_focus_options"])
            config["教學重點"] = focus or None

            config["額外要求"] = st.text_area(
                "額外要求（自由文字，選填）",
                placeholder="例如：病患為計程車司機、近期有東南亞旅遊史...",
                height=100,
            ) or None

    elif ss.config_type == "模板題":
        problem_set = os.listdir("data/template_problem_set/")
        problem = st.selectbox("模板題選單", sorted(problem_set), index=None)
    elif ss.config_type == "題目存檔":
        problem_set = os.listdir("data/problem_set/")
        problem = st.selectbox("過去練習記錄", problem_set, index=None)
    elif ss.config_type == "進度存檔":
        save_files = save_load.list_saves()
        save_file = st.selectbox("讀取進度存檔", save_files, index=None,
                                 placeholder="請選擇要繼續的進度...")
        if save_file:
            st.caption("讀取後將還原到當時的階段，可繼續操作。")

    if st.button("確認設定並開始看診", use_container_width=True) and util.check_progress():
        if ss.config_type == "進度存檔":
            if not save_file:
                dialog.error("請先選擇要載入的存檔")
            else:
                save_load.load_progress(save_file)
                target_progress = ss.get("current_progress", 0)
                st.switch_page(f"page/{const.section_name[target_progress]}.py")
        elif "problem" in ss:
            dialog.error("請先完成目前的題目", "test")
            pass
        elif ss.config_type == "輸入參數":
            config["年齡"] = random.randint(config["年齡"][0], config["年齡"][1])

            if config["性別"] == "隨機":
                config["性別"] = random.choice(["男", "女"])

            if config["疾病領域"] == "隨機":
                config["疾病領域"] = random.choice(OPTS["field_options"])

            ss.user_config = config
        elif ss.config_type == "模板題":
            with open(f"data/template_problem_set/{problem}", "r") as f:
                ss.problem = f.read()
            print(f"Problem: {problem}")
            ss.data = json.loads(ss.problem)

            util.next_page()
        else:
            with open(f"data/problem_set/{problem}", "r") as f:
                ss.problem = f.read()
            print(f"Problem: {problem}")
            ss.data = json.loads(ss.problem)

            util.next_page()

def _is_valid_english_disease_name(name):
    if not name or not isinstance(name, str):
        return False
    return name.strip() != "" and name.isascii()


if "user_config" in ss and "problem" not in ss:
    if "problem_setter_model" not in ss:
        create_problem_setter_model()

    config_str = serialize_config(ss.user_config)
    # 看診時間已由 serialize_config 寫入【臨床情境】，此處不再重複注入。
    prompt = (
        f"請利用以下資訊幫我出題：\n"
        f"今日日期：{datetime.datetime.now().strftime('%Y/%m')} （年/月）\n"
        f"\n"
        f"{config_str}"
    )
    _t0 = time.perf_counter()
    ss.problem = ss.problem_setter.send_message(prompt).text
    _dt = time.perf_counter() - _t0
    util.record(ss.log, f"[PERF] case_gen={_dt:.2f}s")

    ss.data = json.loads(ss.problem)
    demographics.validate_demographics(ss.data)

    # Validate englishDiseaseName (used downstream for PDF lookup); fallback to flash if invalid
    eng_name = ss.data.get("Problem", {}).get("englishDiseaseName")
    if not _is_valid_english_disease_name(eng_name):
        util.record(ss.log, f"[FALLBACK] invalid englishDiseaseName={eng_name!r}, retry with gemini-2.5-flash")
        del ss.problem_setter_model
        del ss.problem_setter
        create_problem_setter_model(model_name="gemini-2.5-flash")
        _t0 = time.perf_counter()
        ss.problem = ss.problem_setter.send_message(prompt).text
        _dt = time.perf_counter() - _t0
        util.record(ss.log, f"[PERF] case_gen_fallback={_dt:.2f}s")
        ss.data = json.loads(ss.problem)
        demographics.validate_demographics(ss.data)

    print(prompt)
    util.record(ss.log, prompt)
    util.record(ss.log, ss.problem)

    util.next_page()
