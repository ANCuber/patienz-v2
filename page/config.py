import streamlit as st
from model.problem_setter import create_problem_setter_model
import util.dialog as dialog
import util.tools as util
import util.save_load as save_load
import util.constants as const
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
                setting = st.selectbox("就診情境", OPTS["setting_options"])
                config["就診情境"] = setting if setting != "隨機" else None
            with adv_col_2[2]:
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
    prompt = (
        f"請利用以下資訊幫我出題：\n"
        f"今日日期：{datetime.datetime.now().strftime('%Y/%m')} （年/月）\n\n"
        f"{config_str}"
    )
    _t0 = time.perf_counter()
    ss.problem = ss.problem_setter.send_message(prompt).text
    _dt = time.perf_counter() - _t0
    util.record(ss.log, f"[PERF] case_gen={_dt:.2f}s")

    ss.data = json.loads(ss.problem)

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

    print(prompt)
    util.record(ss.log, prompt)
    util.record(ss.log, ss.problem)

    util.next_page()
