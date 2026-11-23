#!/usr/bin/env python3
"""Write the ten image-bank demonstration cases into data/template_problem_set/.

Every case is written *from one specific film* in image_bank/manifest.json:

* ``Problem.檢查預期所見`` holds the finished report for each imaging exam, in
  the examiner's markdown format and ending in the machine-read
  ``[ABNORMAL]``/``[NORMAL]`` tag. ``page/examination.py`` serves it verbatim —
  no model call — so the report is deterministic;
* that report describes what the target film actually shows: for the ECGs
  every cardiologist statement on the PTB-XL record (all of them — the bank's
  ``co_findings`` are validated), the heart axis and the infarct stadium from
  the cardiologist's own free-text reading; for the chest films the side from
  the radiologist's bounding box and the devices seen on the film;
* the patient's age/sex equal the film's, so the demographic tiebreak lands on
  that exact film, and the impression line names ONLY the principal finding so
  no secondary finding can outrank it in retrieval;
* ``_template_meta`` records the target id and the expected text;
  ``page/config.py`` strips it before the case reaches any prompt and
  ``tests/test_image_bank_templates.py`` proves the text retrieves the target.

Two consequences of writing from the film rather than from the textbook:
PTB-XL carries no acute-STEMI tracing (no record in the bank codes ST
elevation; the infarct films are established Q-wave infarcts), so the two
infarct cases are *old / indeterminate-age* infarcts, and NIH's pneumothorax
films are all post-drainage, so there is no pneumothorax case.

Re-run after changing a case:  python tools/make_image_bank_templates.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "template_problem_set")

SIDE_SOURCE = ("胸片側別依 NIH BBox_List_2017.csv 放射科醫師定位框座標推得（正面片影像左＝病人右），"
               "並逐張開圖檢視排除插管後影像；ECG 所見依 PTB-XL 心臟科醫師之全部 statement 與原始判讀文字。"
               "此非本課程臨床教師之審閱。")


def report(exam, impression, findings, negatives, abnormal):
    """The examiner's markdown shape, so a fixed report looks like a generated one."""
    lines = ["**[%s] 檢查結果：**" % exam, "- **整體印象：** %s" % impression, "- **具體發現：**"]
    lines += ["  - %s" % f for f in findings]
    lines.append("- **重要陰性發現：** %s" % negatives)
    lines.append("[ABNORMAL]" if abnormal else "[NORMAL]")
    return "\n".join(lines)


def case(*, fname, meta, basic, mh, fh, sh, ros, vitals, problem):
    return fname, {"_template_meta": meta, "基本資訊": basic, "MH": mh, "FH": fh, "SH": sh,
                   "ROS": ros, "VitalSigns": vitals, "Problem": problem}


def meta(kind, targets, note=""):
    return {"purpose": "影像庫示範案例（%s）：報告依指定影像的實際所見撰寫，檢查時逐字顯示" % kind,
            "image_targets": targets, "laterality_source": SIDE_SOURCE, "note": note}


def target(exam, modality, target_id, has_abnormal, expected_report):
    return {"exam": exam, "modality": modality, "target_id": target_id,
            "has_abnormal": has_abnormal, "expected_report": expected_report}


NORMAL_CXR = report("胸部X光", "無明顯異常",
                    ["心影大小正常，心胸比約 0.45", "兩側肺野清晰，肺紋理正常", "兩側肋膈角銳利，橫膈輪廓平滑",
                     "縱膈與肺門未見異常增寬", "骨骼與軟組織未見異常"],
                    "未見肺水腫、肋膜積水、氣胸或實質浸潤", False)


def normal_ecg(rate):
    return report("心電圖", "無明顯異常",
                  ["竇性心律，心率約 %d 次/分" % rate, "電軸正常", "PR 間期 160 ms，QRS 90 ms，QTc 410 ms",
                   "各導程 ST 段與 T 波無異常"],
                  "無 ST 段上升或壓低、無病理性 Q 波、無傳導阻滯、無心律不整", False)


# ===========================================================================
# ECG cases (PTB-XL, cardiologist-validated; films re-checked by eye)
# ===========================================================================
# ptbxl-IMI-4166  M/31  codes IMI 100, ABQRS, SR; axis ARAD; cardiologist: sinus
# rhythm, extreme right axis, R/S transition shifted left, abnormal QRS(T),
# inferior infarct "probably old", ST&T abnormal, probable high-lateral
# ischaemia or left strain.
ECG1 = report("心電圖", "陳舊性下壁心肌梗塞",
              ["竇性心律，心率約 60 次/分，節律規則",
               "電軸極度右偏（約 +150°）",
               "II、III、aVF 導程呈 QS 型（病理性 Q 波），無 ST 段上升，符合陳舊性下壁心肌梗塞",
               "胸前導程 R 波遞增不良，R/S 移行帶左移至 V5–V6",
               # the cardiologist's free text added "probable high-lateral ischaemia
               # or left strain" but coded no lateral statement; wording it with
               # lead names would localize the report to a wall the film's coded
               # statements do not claim, so it is kept non-localizing
               "另見輕微非特異性 ST-T 變化（原判讀認為可能與左心負荷有關）",
               "PR 間期 160 ms，QRS 100 ms，QTc 420 ms"],
              "無 ST 段上升、無急性缺血變化、無束支傳導阻滯、無心律不整", True)

CASE_ECG1 = case(
    fname="影像示範 ECG-1 陳舊性下壁心肌梗塞.json",
    meta=meta("ECG", [target("心電圖", "ECG", "ptbxl-IMI-4166", True, ECG1),
                      target("胸部X光", "CXR", None, False, NORMAL_CXR)],
              "年輕男性陳舊性下壁梗塞：教學重點為辨識病理性 Q 波、年輕人心肌梗塞的病因（家族性高膽固醇血症、"
              "藥物、SCAD、冠狀動脈異常）與後續檢查。"),
    basic={"姓名": "陳柏宇", "年齡": 31, "身高": 176, "體重": 84, "性別": "男",
           "生日": "1995/06/18", "職業": "業務員"},
    mh={"主訴": "運動時胸悶與喘 2 個月",
        "既往疾病": "無已知慢性病；20 歲健檢曾被告知膽固醇偏高，未追蹤",
        "目前病史": "病人為 31 歲男性，2 個月來打籃球或快走時出現胸口悶脹感與喘，休息數分鐘可緩解，"
                   "近兩週爬三層樓即需停下。追問之下，病人表示約 6 個月前出差時曾有一次胸骨後劇烈壓迫痛，"
                   "持續約 3 小時並伴隨冒冷汗與噁心，當時以為是胃痛，未就醫，之後自行緩解。"
                   "否認近期胸痛、暈厥、心悸、發燒或下肢水腫。",
        "過敏史": "無",
        "藥物史": "無"},
    fh={"直系血親疾病": "父親 45 歲心肌梗塞、55 歲猝死；祖父 50 歲心肌梗塞；母親健康"},
    sh={"生活習慣": "業務工作應酬多，作息不規律，過去每週運動 2 次，近 2 個月因症狀減少",
        "飲食": "外食為主，常吃燒烤與炸物",
        "菸酒": "抽菸 12 年，每天一包；每週飲酒 2–3 次",
        "旅遊史": "無"},
    ros={"全身性症狀": "無發燒、體重變化",
         "相關系統症狀": "【心血管】勞力性胸悶、活動耐受力下降，無靜息胸痛、心悸、暈厥、下肢水腫；"
                        "眼瞼可見黃色瘤、雙側跟腱增厚【呼吸】勞力性呼吸困難，無咳嗽【腸胃】無胃食道逆流症狀"},
    vitals={"體溫": "36.6°C", "血壓": "138/88 mmHg", "心跳": "60 bpm（規則）",
            "呼吸次數": "16 breaths/min", "血氧飽和度": "98% on room air"},
    problem={
        "疾病": "陳舊性下壁心肌梗塞併穩定型心絞痛（疑似家族性高膽固醇血症）",
        "排除可能疾病之診斷": "1. 不穩定型心絞痛／急性冠心症（連續心電圖與心肌旋轉蛋白）2. 肥厚型心肌病變（心臟超音波）"
                            "3. 心肌炎後遺症 4. 肺栓塞 5. 胃食道逆流",
        "確認正確疾病之診斷": "12 導程心電圖 II、III、aVF 病理性 Q 波且無急性 ST 變化；高敏感度心肌旋轉蛋白正常"
                            "（排除急性事件）；心臟超音波下壁運動異常；LDL-C 顯著升高（≥190 mg/dL）與家族史符合"
                            "家族性高膽固醇血症；冠狀動脈電腦斷層或心導管顯示右冠狀動脈病灶",
        "處置方式": "先排除急性冠心症（連續 ECG、troponin）；aspirin、高強度 statin（目標 LDL-C <55 mg/dL，"
                   "必要時加 ezetimibe／PCSK9 抑制劑）、β 阻斷劑、依需要硝酸甘油；安排心臟超音波與冠狀動脈影像，"
                   "評估血運重建；戒菸、運動處方、心臟復健；家族性高膽固醇血症之家族篩檢（一等親）。",
        "englishDiseaseName": "Old Inferior Myocardial Infarction with Stable Angina (suspected Familial Hypercholesterolemia)",
        "檢查預期所見": {"心電圖": ECG1, "胸部X光": NORMAL_CXR},
    },
)

# ptbxl-ASMI-1440  F/48  codes ASMI 100, SR; axis right; cardiologist: sinus
# rhythm, right axis, abnormal QRS(T), anterior infarct, age not determinable.
ECG2 = report("心電圖", "前間壁心肌梗塞（年代不明）",
              ["竇性心律，心率約 65 次/分，節律規則",
               "電軸右偏",
               "V1–V3 導程呈 QS 型（病理性 Q 波），V4 R 波低小，胸前導程 R 波遞增不良，符合前間壁心肌梗塞",
               "各導程無 ST 段上升，T 波無明顯倒置，無法判定梗塞新舊",
               "PR 間期 150 ms，QRS 90 ms，QTc 420 ms"],
              "無 ST 段上升或壓低、無急性缺血變化、無傳導阻滯、無心律不整", True)

CASE_ECG2 = case(
    fname="影像示範 ECG-2 無症狀前間壁心肌梗塞.json",
    meta=meta("ECG", [target("心電圖", "ECG", "ptbxl-ASMI-1440", True, ECG2),
                      target("胸部X光", "CXR", None, False, NORMAL_CXR)],
              "糖尿病女性的無症狀（silent）心肌梗塞：教學重點為 V1–V3 病理性 Q 波的辨識、"
              "非典型表現與後續評估。"),
    basic={"姓名": "林淑惠", "年齡": 48, "身高": 158, "體重": 72, "性別": "女",
           "生日": "1978/02/11", "職業": "會計助理"},
    mh={"主訴": "容易疲倦、活動時喘 3 個月",
        "既往疾病": "第 2 型糖尿病 10 年（HbA1c 8.5%）、高血壓 5 年、肥胖（BMI 29）",
        "目前病史": "病人為 48 歲女性，3 個月來明顯疲倦、爬樓梯會喘，偶有上腹不適與噁心，自認是胃不好。"
                   "否認胸痛、胸悶、心悸、暈厥；否認夜間端坐呼吸與下肢水腫。近半年體重無明顯變化。"
                   "此次因糖尿病門診例行心電圖異常被轉介。",
        "過敏史": "Sulfa 類藥物（皮疹）",
        "藥物史": "Metformin 1000 mg 每日兩次、Glimepiride 2 mg 每日一次、Amlodipine 5 mg 每日一次"},
    fh={"直系血親疾病": "母親糖尿病；父親 60 歲中風"},
    sh={"生活習慣": "久坐辦公，無規律運動",
        "飲食": "偏好甜食與含糖飲料",
        "菸酒": "不菸不酒",
        "旅遊史": "無"},
    ros={"全身性症狀": "疲倦；無發燒、體重減輕",
         "相關系統症狀": "【心血管】無胸痛、心悸、暈厥、下肢水腫【呼吸】勞力性呼吸困難，無咳嗽"
                        "【腸胃】偶發上腹不適、噁心，無吞嚥困難或黑便【神經】無局部神經症狀"},
    vitals={"體溫": "36.5°C", "血壓": "146/90 mmHg", "心跳": "66 bpm（規則）",
            "呼吸次數": "18 breaths/min", "血氧飽和度": "97% on room air"},
    problem={
        "疾病": "無症狀（silent）前間壁心肌梗塞，年代不明",
        "排除可能疾病之診斷": "1. 急性冠心症（連續心電圖、心肌旋轉蛋白）2. 肥厚型心肌病變或浸潤性心肌病變（假性梗塞型態）"
                            "3. 左束支傳導阻滯或 WPW 造成之假性 Q 波 4. 慢性阻塞性肺病造成之 R 波遞增不良 5. 導程放置錯誤",
        "確認正確疾病之診斷": "12 導程心電圖 V1–V3 病理性 Q 波、R 波遞增不良且無急性 ST 變化；心肌旋轉蛋白正常；"
                            "心臟超音波前壁與中隔運動異常、LVEF；冠狀動脈電腦斷層或心導管證實左前降支病灶；"
                            "糖尿病與高血壓的整體心血管風險評估",
        "處置方式": "排除急性事件後啟動二級預防：aspirin、高強度 statin、ACEI/ARB（糖尿病＋高血壓）、"
                   "β 阻斷劑（若 LVEF 降低）；糖尿病治療加入具心血管保護的 SGLT2 抑制劑或 GLP-1 受體促效劑；"
                   "血壓與血糖目標控制；安排心臟超音波與冠狀動脈影像決定是否血運重建；"
                   "衛教糖尿病病人的非典型缺血症狀。",
        "englishDiseaseName": "Silent Anteroseptal Myocardial Infarction of Indeterminate Age",
        "檢查預期所見": {"心電圖": ECG2, "胸部X光": NORMAL_CXR},
    },
)

# ptbxl-AFIB-8215  F/71  codes AFIB 100; cardiologist: normocardic atrial
# fibrillation, left axis.
ECG3 = report("心電圖", "心房顫動，心室速率正常",
              ["心房顫動：RR 間隔絕對不規則，無可辨識之 P 波，基線可見細小 f 波",
               "心室速率約 84 次/分（速率控制範圍內）",
               "電軸左偏",
               "QRS 窄（90 ms），型態正常",
               "QTc 約 430 ms"],
              "無 ST 段上升或壓低、無病理性 Q 波、無束支傳導阻滯、無心室早期收縮", True)

CASE_ECG3 = case(
    fname="影像示範 ECG-3 心房顫動.json",
    meta=meta("ECG", [target("心電圖", "ECG", "ptbxl-AFIB-8215", True, ECG3),
                      target("胸部X光", "CXR", None, False, NORMAL_CXR)],
              "心室速率正常的心房顫動：教學重點為 CHA₂DS₂-VASc 評估、抗凝決策，以及「速率已控制」時仍需處理的事。"),
    basic={"姓名": "王秀蘭", "年齡": 71, "身高": 156, "體重": 58, "性別": "女",
           "生日": "1955/02/08", "職業": "退休（曾任小學老師）"},
    mh={"主訴": "間歇性心悸 2 週",
        "既往疾病": "高血壓 15 年、第 2 型糖尿病 10 年",
        "目前病史": "病人為 71 歲女性，2 週來間歇感到心跳「亂跳、不整齊」，每次持續數十分鐘到數小時，"
                   "多在休息時察覺，活動耐受力略降但無明顯喘。否認胸痛、暈厥、單側肢體無力或口齒不清。"
                   "否認發燒、甲狀腺疾病史、近期感染或飲酒。",
        "過敏史": "Penicillin（皮疹）",
        "藥物史": "Losartan 50 mg 每日一次、Metformin 850 mg 每日兩次"},
    fh={"直系血親疾病": "母親 78 歲中風；父親高血壓"},
    sh={"生活習慣": "每日晨間散步 30 分鐘",
        "飲食": "清淡，少鹽",
        "菸酒": "不菸不酒；每日 2 杯咖啡",
        "旅遊史": "無"},
    ros={"全身性症狀": "輕微疲倦；否認發燒、體重變化、怕熱、手抖",
         "相關系統症狀": "【心血管】間歇性心悸、脈搏不規則，無胸痛、無下肢水腫【呼吸】無明顯喘，無咳嗽"
                        "【神經】無頭暈、暈厥、肢體無力、言語障礙"},
    vitals={"體溫": "36.6°C", "血壓": "138/86 mmHg", "心跳": "84 bpm（不規則）",
            "呼吸次數": "16 breaths/min", "血氧飽和度": "97% on room air"},
    problem={
        "疾病": "心房顫動（心室速率已在控制範圍）",
        "排除可能疾病之診斷": "1. 甲狀腺機能亢進（TSH、free T4）2. 心房撲動（鋸齒狀 F 波）3. 多源性心房頻脈"
                            "4. 頻繁心房早期收縮 5. 急性冠心症",
        "確認正確疾病之診斷": "12 導程心電圖 RR 間隔絕對不規則且無 P 波；TSH 正常；心臟超音波評估左心房大小、瓣膜與 LVEF；"
                            "CHA₂DS₂-VASc 評分（年齡 65–74 歲 1 分、女性 1 分、高血壓 1 分、糖尿病 1 分 = 4 分）",
        "處置方式": "心室速率已正常，暫不需額外速率控制藥物；CHA₂DS₂-VASc = 4 分 → 建議長期口服抗凝血劑（NOAC），"
                   "評估 HAS-BLED 出血風險；找尋並治療誘發因子（甲狀腺、感染、酒精、睡眠呼吸中止）；"
                   "依症狀與心臟超音波結果討論節律控制（藥物或電燒）；門診追蹤。",
        "englishDiseaseName": "Atrial Fibrillation with Controlled Ventricular Rate",
        "檢查預期所見": {"心電圖": ECG3, "胸部X光": NORMAL_CXR},
    },
)

# ptbxl-CLBBB-2135  F/54  codes CLBBB 100, SR; axis LAD; cardiologist: sinus
# rhythm, left axis, left bundle branch block.
ECG4 = report("心電圖", "完全性左束支傳導阻滯",
              ["竇性心律，心率約 88 次/分",
               "電軸左偏",
               "QRS 增寬達 150 ms；V1 呈 QS 型，V5–V6 及 I、aVL 呈寬而有切跡之 R 波且無 q 波",
               "與 QRS 主波方向相反之續發性 ST-T 變化（discordant ST-T）",
               "PR 間期 170 ms"],
              "無心律不整、無房室傳導阻滯；因左束支阻滯，無法評估缺血性 ST 變化", True)
# Wording follows the adjudicated read of nih-cardiomegaly-00005827_000
# (image_bank/annotations/cxr/): enlarged silhouette, CTR ~0.55, lungs clear,
# apical + supraclavicular clips, no lines.
ECG4_CXR = report("胸部X光", "心臟肥大",
                  ["心影擴大，心胸比約 0.55（橫徑約 18 公分），左心緣圓鈍並向左下方延伸，符合心臟肥大",
                   "氣管居中，上縱膈與主動脈弓未見增寬",
                   "左肺門略為突出，考量為血管性，未見明確腫塊",
                   "兩側肺野清晰，肺紋理正常；兩側肋膈角銳利，橫膈輪廓平順，左橫膈下可見胃泡",
                   "左肺尖及左鎖骨上區可見數枚細小金屬手術夾（既往左上肺葉手術變化）",
                   "兩側乳房軟組織陰影重疊於下肺野"],
                  "未見肺水腫、肋膜積水、肺實變或氣胸；未見導管、引流管或心律調節器", True)

CASE_ECG4 = case(
    fname="影像示範 ECG-4 完全性左束支傳導阻滯.json",
    meta=meta("ECG", [target("心電圖", "ECG", "ptbxl-CLBBB-2135", True, ECG4),
                      # cardiomegaly films F/43 and F/65 are equidistant from 54; id order
                      # picks F/43 (nih-cardiomegaly-00005827_000): enlarged silhouette,
                      # a few apical surgical clips (written into the report), no lines.
                      target("胸部X光", "CXR", "nih-cardiomegaly-00005827_000", True, ECG4_CXR)],
              "擴張型心肌病變併心衰竭與 CLBBB：教學重點為 LBBB 判讀、缺血判讀的限制（Sgarbossa 概念）、CRT 適應症。"),
    basic={"姓名": "張美玲", "年齡": 54, "身高": 160, "體重": 70, "性別": "女",
           "生日": "1972/06/30", "職業": "會計"},
    mh={"主訴": "活動時喘、雙下肢水腫 2 個月",
        "既往疾病": "高血壓 10 年（控制不佳）；20 年前左上肺葉良性結節手術切除",
        "目前病史": "病人為 54 歲女性，2 個月來爬樓梯即喘，近 2 週出現夜間平躺喘醒需坐起（端坐呼吸）、"
                   "需墊 3 個枕頭入睡，雙側小腿水腫至傍晚加重，體重增加 4 公斤。否認胸痛、心悸、暈厥、"
                   "發燒或咳嗽。半年前曾有一次上呼吸道感染。",
        "過敏史": "無",
        "藥物史": "Amlodipine 10 mg 每日一次（自述常漏服）"},
    fh={"直系血親疾病": "無心臟病或猝死家族史"},
    sh={"生活習慣": "久坐辦公，缺乏運動",
        "飲食": "口味偏鹹",
        "菸酒": "不菸；偶爾小酌",
        "旅遊史": "無"},
    ros={"全身性症狀": "疲倦、體重增加；否認發燒",
         "相關系統症狀": "【心血管】勞力性呼吸困難、端坐呼吸、陣發性夜間呼吸困難、雙下肢凹陷性水腫，"
                        "無胸痛、心悸【呼吸】無咳嗽、咳血【腸胃】食慾略降，無腹痛"},
    vitals={"體溫": "36.5°C", "血壓": "158/98 mmHg", "心跳": "88 bpm",
            "呼吸次數": "20 breaths/min", "血氧飽和度": "95% on room air"},
    problem={
        "疾病": "擴張型心肌病變併心臟衰竭（HFrEF）合併完全性左束支傳導阻滯",
        "排除可能疾病之診斷": "1. 缺血性心肌病變（冠狀動脈攝影）2. 高血壓性心臟病 3. 瓣膜性心臟病"
                            "4. 慢性阻塞性肺病 5. 腎病症候群（尿蛋白）",
        "確認正確疾病之診斷": "心電圖 QRS ≥120 ms 之完全性左束支傳導阻滯；NT-proBNP 顯著升高；"
                            "胸部 X 光心臟肥大（肺野清晰不排除心衰竭）；心臟超音波左心室擴大、LVEF 約 30%、"
                            "整體收縮功能下降；冠狀動脈攝影無顯著狹窄",
        "處置方式": "利尿劑（furosemide）緩解鬱血；四大基石：ACEI/ARNI、β 阻斷劑（bisoprolol/carvedilol）、"
                   "MRA、SGLT2 抑制劑逐步啟動並調至目標劑量；限鹽限水、每日量體重。"
                   "LVEF ≤35% 且 LBBB QRS ≥150 ms 於最佳藥物治療 3 個月後仍有症狀 → 評估心臟再同步治療（CRT）。"
                   "排除缺血與可逆因子（甲狀腺、酒精、心肌炎）。",
        "englishDiseaseName": "Dilated Cardiomyopathy with Heart Failure and Complete Left Bundle Branch Block",
        "檢查預期所見": {"心電圖": ECG4, "胸部X光": ECG4_CXR},
    },
)

# ptbxl-3AVB-959  F/88  codes 3AVB 100, IRBBB 100, SR; cardiologist: "sinus
# rhythm, 98/min, with complete heart block and ventricular escape rate of
# 40/min; incomplete right bundle branch block; in view of syncope, permanent
# pacing should be considered."
ECG5 = report("心電圖", "第三度（完全性）房室傳導阻滯",
              ["心房率約 98 次/分（規則之竇性 P 波），心室率約 40 次/分，P 波與 QRS 波群完全無關（房室分離）",
               "PR 間期不固定，無任何 P 波下傳",
               "心室逸脫節律，QRS 約 120 ms，呈不完全性右束支傳導阻滯型態（V1 rSr'）",
               "QT 間期因心搏過緩而延長，QTc 約 450 ms",
               "無 ST 段上升"],
              "無病理性 Q 波、無急性缺血變化；無高血鉀之 T 波高尖", True)

CASE_ECG5 = case(
    fname="影像示範 ECG-5 完全性房室傳導阻滯.json",
    meta=meta("ECG", [target("心電圖", "ECG", "ptbxl-3AVB-959", True, ECG5),
                      target("胸部X光", "CXR", None, False, NORMAL_CXR)],
              "完全房室傳導阻滯併暈厥（原始判讀即建議永久節律器）：教學重點為房室分離辨識、"
              "停用房室結抑制劑、經皮/永久節律器。"),
    basic={"姓名": "李阿桂", "年齡": 88, "身高": 150, "體重": 48, "性別": "女",
           "生日": "1938/01/17", "職業": "退休（務農）"},
    mh={"主訴": "反覆頭暈 1 週，今晨暈厥一次",
        "既往疾病": "高血壓 20 年、退化性關節炎",
        "目前病史": "病人為 88 歲女性，1 週來反覆頭暈、疲倦、走路無力，家人覺得她「反應變慢」。"
                   "今晨如廁站起時突然眼前一黑倒地，約 10 秒後自行清醒，無抽搐、無大小便失禁、"
                   "無舌頭咬傷，清醒後無混亂。否認胸痛、心悸、發燒。近一個月藥物無調整。",
        "過敏史": "無",
        "藥物史": "Bisoprolol 5 mg 每日一次、Amlodipine 5 mg 每日一次、Acetaminophen 需要時"},
    fh={"直系血親疾病": "不詳（父母早逝）"},
    sh={"生活習慣": "與女兒同住，日常可自理，每日在庭院活動",
        "飲食": "清淡",
        "菸酒": "不菸不酒",
        "旅遊史": "無"},
    ros={"全身性症狀": "疲倦、無力；否認發燒、體重減輕",
         "相關系統症狀": "【心血管】無胸痛、心悸；有活動時喘【神經】頭暈、一次暈厥，無局部神經症狀、"
                        "無抽搐【腸胃】無黑便、腹痛"},
    vitals={"體溫": "36.4°C", "血壓": "150/70 mmHg", "心跳": "40 bpm（規則）",
            "呼吸次數": "16 breaths/min", "血氧飽和度": "96% on room air"},
    problem={
        "疾病": "第三度（完全性）房室傳導阻滯併暈厥",
        "排除可能疾病之診斷": "1. 藥物引起之心搏過緩（β 阻斷劑）2. 病竇症候群 3. 高血鉀"
                            "4. 下壁心肌梗塞 5. 姿勢性低血壓 6. 神經反射性暈厥",
        "確認正確疾病之診斷": "12 導程心電圖顯示房室分離（P 波與 QRS 無固定關係）、心室逸脫心律；"
                            "電解質（鉀）與心肌旋轉蛋白正常；連續心電圖監測；停用 bisoprolol 後阻滯持續",
        "處置方式": "持續心電圖監測；**停用 bisoprolol 等房室結抑制劑**；有症狀之心搏過緩 → atropine"
                   "（對阻滯位置在希氏束以下者常無效）、經皮節律或 dopamine/isoproterenol 過渡；"
                   "排除可逆原因（藥物、高血鉀、缺血）後，**永久性心臟節律器**為確定治療。"
                   "暈厥期間跌倒之傷害評估。",
        "englishDiseaseName": "Complete (Third-Degree) Atrioventricular Block with Syncope",
        "檢查預期所見": {"心電圖": ECG5, "胸部X光": NORMAL_CXR},
    },
)

# ===========================================================================
# CXR cases (NIH ChestX-ray14, radiologist-boxed; every report below is worded
# from the film's adjudicated three-reader read in image_bank/annotations/cxr/)
# ===========================================================================
CXR1 = report("胸部X光", "右側大量肋膜積水",
              ["右側中下肺野呈大片均質緻密陰影，上緣呈外高內低之弧形（meniscus sign），垂直範圍約 15 公分",
               "右側橫膈、右側肋膈角及右心緣均被積水遮蔽，心臟大小無法評估",
               "右肺門旁、積水上內緣可見圓形分葉狀緻密影，疑為肺門腫塊或包裹性積水，無法與積水區分",
               "氣管居中，未見明顯縱膈偏移",
               "左肺野清晰，左側肋膈角銳利；左橫膈下可見胃泡",
               "左前胸壁可見植入式人工血管（port-A）貯槽，導管走向在本片無法完整追蹤"],
              "未見氣胸；未見胸管或引流管；左肺未見實質浸潤或結節", True)
CXR1_ECG = report("心電圖", "竇性心搏過速",
                  ["竇性心搏過速，心率約 96 次/分", "電軸正常", "PR、QRS、QTc 間期正常"],
                  "無 ST 段上升或壓低、無病理性 Q 波、無 S1Q3T3 型態、無右心負荷徵象", True)

CASE_CXR1 = case(
    fname="影像示範 CXR-1 惡性肋膜積水.json",
    meta=meta("CXR", [target("胸部X光", "CXR", "nih-effusion-00013337_000", True, CXR1),
                      target("心電圖", "ECG", None, True, CXR1_ECG)],
              "影像：右側大量肋膜積水，左前胸 port-A（病史已寫入）。教學重點為 Light's criteria、"
              "惡性積水的處置與預後溝通。"),
    basic={"姓名": "黃志明", "年齡": 65, "身高": 170, "體重": 62, "性別": "男",
           "生日": "1961/04/09", "職業": "退休工程師"},
    mh={"主訴": "漸進性呼吸困難 2 週，右側胸悶",
        "既往疾病": "肺腺癌第四期（8 個月前診斷，左前胸置有人工血管 port-A，目前接受第二線化療中）、高血壓",
        "目前病史": "病人為 65 歲男性，2 週來活動時喘逐漸加重，近 3 天平躺時更喘、需右側臥才較舒服，"
                   "伴隨右側胸悶與乾咳，體重 1 個月減輕 3 公斤。否認發燒、咳血、下肢腫痛。"
                   "上次化療為 3 週前，療程後有輕微噁心已緩解。",
        "過敏史": "無",
        "藥物史": "Pemetrexed 化療中；Amlodipine 5 mg 每日一次"},
    fh={"直系血親疾病": "父親肺癌（吸菸者）"},
    sh={"生活習慣": "退休後在家休養，活動量已明顯下降",
        "飲食": "食慾差，進食量減半",
        "菸酒": "已戒菸 8 個月（過去抽菸 40 年，每天一包）；不飲酒",
        "旅遊史": "無"},
    ros={"全身性症狀": "疲倦、食慾差、體重減輕；否認發燒、盜汗",
         "相關系統症狀": "【呼吸】漸進性呼吸困難、右側胸悶、乾咳，無咳血【心血管】無胸痛、心悸；"
                        "右下胸叩診濁音、呼吸音減弱【腸胃】無腹痛【肢體】無單側下肢腫脹"},
    vitals={"體溫": "36.8°C", "血壓": "128/78 mmHg", "心跳": "96 bpm",
            "呼吸次數": "24 breaths/min", "血氧飽和度": "92% on room air"},
    problem={
        "疾病": "惡性肋膜積水（肺腺癌第四期）",
        "排除可能疾病之診斷": "1. 肺栓塞（癌症病人高風險，需 CTPA）2. 心衰竭（NT-proBNP、超音波）"
                            "3. 肺炎併肋膜積水 4. 化療相關肺毒性 5. 上腔靜脈症候群",
        "確認正確疾病之診斷": "胸部 X 光右側大量肋膜積水；胸腔超音波定位；診斷性胸腔穿刺呈滲出液（Light's criteria）、"
                            "細胞學見惡性細胞；胸部電腦斷層評估肋膜結節與原發腫瘤",
        "處置方式": "治療性胸腔穿刺緩解症狀（單次勿超過 1–1.5 L）；評估肺部是否可再擴張；"
                   "反覆積水 → 留置式肋膜導管（IPC）或滑石粉肋膜沾黏術；持續腫瘤治療與症狀控制；"
                   "與病人及家屬討論治療目標與安寧緩和照護資源。",
        "englishDiseaseName": "Malignant Pleural Effusion (Stage IV Lung Adenocarcinoma)",
        "檢查預期所見": {"胸部X光": CXR1, "心電圖": CXR1_ECG},
    },
)

# nih-pneumonia-00007444_003 (M/38): faint patchy left mid-lower zone
# infiltrate, a TUNNELLED dual-lumen (dialysis-type) right IJ catheter with a
# hairpin loop, raised right hemidiaphragm with paracardiac haze (the readers'
# trap). The first draft wrote a plain post-operative CVC and an air
# bronchogram; neither is on the film, so the patient is now a haemodialysis
# patient and the report follows the adjudicated read.
CXR2 = report("胸部X光", "左側肺炎（左中下肺野）",
              ["左中下肺野、左肺門下方沿左心緣旁可見約 7 公分範圍之斑片狀、邊界不清之肺泡性浸潤陰影，血管紋理模糊，"
               "左心緣下段輪廓稍不清，符合左側肺炎",
               "右內頸靜脈路徑可見隧道式雙腔中心靜脈導管（透析導管），於右頸根部呈髮夾狀迴繞後沿右氣管旁下行，"
               "尖端位於上腔靜脈與右心房交界處",
               "右側橫膈較左側上抬約 3–4 公分，右心緣旁／右下肺野內側可見淡薄模糊影，研判為橫膈上抬與心影重疊或輕度基底肺膨脹不全，"
               "未見明確實變；右側肋膈角銳利",
               "心影大小約在正常上限（心胸比約 0.5）；兩側肺門血管紋理略增多",
               "右上縱膈／右氣管旁輪廓因導管與上腔靜脈投影稍突出，無明確腫塊或氣管偏移",
               "左橫膈下可見胃泡"],
              "未見氣胸、未見明顯肋膜積水；未見胸管或引流管；未見空氣支氣管徵", True)
CXR2_ECG = report("心電圖", "竇性心搏過速",
                  ["竇性心搏過速，心率約 108 次/分", "電軸正常", "PR、QRS、QTc 間期正常"],
                  "無 ST 段上升或壓低、無病理性 Q 波、無 S1Q3T3 型態、無高血鉀變化（T 波不高尖）", True)

CASE_CXR2 = case(
    fname="影像示範 CXR-2 院內肺炎.json",
    meta=meta("CXR", [target("胸部X光", "CXR", "nih-pneumonia-00007444_003", True, CXR2),
                      target("心電圖", "ECG", None, True, CXR2_ECG)],
              "影像：左中下肺野斑片狀肺炎，右頸隧道式透析導管（病史已寫入：末期腎病、"
              "住院第 5 天）。右橫膈上抬造成的右心緣旁模糊影是讀片陷阱，標準答案在左側。"
              "教學重點為院內肺炎的病原考量與經驗性抗生素、透析病人的劑量調整。"),
    basic={"姓名": "吳承翰", "年齡": 38, "身高": 175, "體重": 72, "性別": "男",
           "生日": "1988/07/22", "職業": "軟體工程師"},
    mh={"主訴": "住院第 5 天發燒、咳嗽帶黃痰、左側胸痛",
        "既往疾病": "IgA 腎病變進展至末期腎病，3 個月前開始經右側頸內靜脈隧道式雙腔導管進行血液透析（每週三次）；"
                   "高血壓；5 天前因左前臂動靜脈瘻管建置手術住院",
        "目前病史": "病人為 38 歲男性，動靜脈瘻管手術後恢復中，術後仍經右頸透析導管規律透析。昨日起發燒至 38.9°C、"
                   "咳嗽帶黃綠色痰，今晨出現左側胸部吸氣時刺痛與呼吸急促。否認導管出口處紅腫滲液、透析中畏寒發抖、"
                   "傷口紅腫或小腿腫痛。",
        "過敏史": "無",
        "藥物史": "Amlodipine 5 mg 每日一次；碳酸鈣（磷結合劑）隨餐；erythropoietin 透析時給予；"
                 "術後 acetaminophen 止痛"},
    fh={"直系血親疾病": "無特殊"},
    sh={"生活習慣": "透析後容易疲倦，活動量減少",
        "飲食": "低鉀低磷飲食、限水",
        "菸酒": "不菸；已戒酒",
        "旅遊史": "無"},
    ros={"全身性症狀": "發燒、畏寒、倦怠",
         "相關系統症狀": "【呼吸】咳嗽帶黃綠痰、左側胸膜性胸痛、呼吸急促；左下肺野呼吸音減弱併爆裂音"
                        "【心血管】心跳快，無胸悶【腎臟】無尿，規律透析【肢體】左前臂瘻管處可觸及震顫，無小腿腫痛；"
                        "右頸導管出口乾淨無紅腫"},
    vitals={"體溫": "38.9°C", "血壓": "138/84 mmHg", "心跳": "108 bpm",
            "呼吸次數": "26 breaths/min", "血氧飽和度": "91% on room air"},
    problem={
        "疾病": "院內肺炎（血液透析病人，左側肺炎）",
        "排除可能疾病之診斷": "1. 透析導管相關血流感染（導管與周邊血液配對培養）2. 肺栓塞（術後臥床）"
                            "3. 體液過多併肺水腫（透析間體重）4. 基底肺膨脹不全 5. 結核病（免疫低下）",
        "確認正確疾病之診斷": "住院 ≥48 小時後新發之肺部浸潤併發燒、膿痰、白血球升高；胸部 X 光左中下肺野斑片狀浸潤；"
                            "痰液革蘭氏染色與培養、血液培養（含導管血）；CRP/PCT 升高；透析間體重無明顯增加以排除肺水腫",
        "處置方式": "採血液（含導管血）與痰液培養後，依院內肺炎指引給予經驗性抗生素（透析病人 MDR 風險高，"
                   "覆蓋 MRSA 與 Pseudomonas，如 piperacillin-tazobactam ＋ vancomycin，"
                   "劑量依透析調整並於透析後給藥），依培養結果降階；氧氣治療維持 SpO2 ≥94%；"
                   "誘發性肺量計與胸腔物理治療；與腎臟科協調透析時程與體液管理；導管出口照護，"
                   "若培養指向導管感染則考慮移除。",
        "englishDiseaseName": "Hospital-Acquired Pneumonia in a Haemodialysis Patient (Left Lung)",
        "檢查預期所見": {"胸部X光": CXR2, "心電圖": CXR2_ECG},
    },
)

CXR3 = report("胸部X光", "心臟肥大併肺血管充血",
              ["心影呈球狀明顯擴大，心胸比約 0.6（橫徑約 17 公分），符合心臟肥大；吸氣不足與肥胖體型可能略為放大心影",
               "兩側肺門陰影增粗、肺門周圍血管紋理增加，符合肺血管充血，但未見明顯間質性或肺泡性肺水腫",
               "上縱膈增寬，胸主動脈迂曲、主動脈弓突出，未見明確縱膈腫塊",
               "吸氣不足、肺容積偏低，兩側橫膈上抬",
               "右側肋膈角因乳房及胸壁軟組織重疊而顯示不清，無法排除少量右側肋膜積水；左側肋膈角銳利",
               "右肺門可見小點狀高密度影，疑為鈣化淋巴結",
               "體型肥胖，兩側乳房軟組織陰影重疊於下肺野；左橫膈下可見大型胃泡"],
              "未見肺實變、結節或氣胸；未見任何導管、引流管或心律調節器", True)
CXR3_ECG = report("心電圖", "左心室肥厚",
                  ["竇性心律，心率約 92 次/分", "電軸左偏",
                   "符合左心室肥厚之電壓標準（SV1 + RV5 > 35 mm）",
                   "I、aVL、V5–V6 導程 ST 段下斜型壓低與 T 波倒置（strain pattern）"],
                  "無 ST 段上升、無病理性 Q 波、無心律不整", True)

CASE_CXR3 = case(
    fname="影像示範 CXR-3 心臟衰竭.json",
    meta=meta("CXR", [target("胸部X光", "CXR", "nih-cardiomegaly-00012288_000", True, CXR3),
                      target("心電圖", "ECG", None, True, CXR3_ECG)],
              "影像：心影明顯擴大、無任何管線。教學重點為心衰竭的 Framingham 準則、NT-proBNP 與 HFpEF/HFrEF 區分。"),
    basic={"姓名": "陳麗華", "年齡": 65, "身高": 158, "體重": 82, "性別": "女",
           "生日": "1961/09/03", "職業": "家庭主婦"},
    mh={"主訴": "活動時喘 1 個月、夜間躺平會喘、雙腳水腫",
        "既往疾病": "高血壓 20 年（控制不佳）、第 2 型糖尿病 12 年、肥胖（BMI 33）",
        "目前病史": "病人為 65 歲女性，1 個月來走路 100 公尺即喘，近 2 週夜間平躺約 1 小時後會喘醒需坐起，"
                   "需墊高枕頭入睡；雙側小腿水腫至傍晚加重，體重 1 個月增加 5 公斤，腹部脹滿感。"
                   "否認胸痛、心悸、暈厥、發燒或咳嗽帶痰。",
        "過敏史": "無",
        "藥物史": "Amlodipine 10 mg、Valsartan 80 mg、Metformin 1000 mg 每日兩次（自述常漏服）"},
    fh={"直系血親疾病": "母親心衰竭；兄長高血壓"},
    sh={"生活習慣": "少運動",
        "飲食": "口味偏鹹，常吃醃漬食物",
        "菸酒": "不菸不酒",
        "旅遊史": "無"},
    ros={"全身性症狀": "疲倦、體重增加；否認發燒",
         "相關系統症狀": "【心血管】勞力性呼吸困難、端坐呼吸、陣發性夜間呼吸困難、雙下肢凹陷性水腫、"
                        "頸靜脈怒張；無胸痛【呼吸】無咳嗽、咳血；兩側肺底可聞及濕囉音【腸胃】腹脹、食慾降"},
    vitals={"體溫": "36.5°C", "血壓": "165/95 mmHg", "心跳": "92 bpm",
            "呼吸次數": "22 breaths/min", "血氧飽和度": "94% on room air"},
    problem={
        "疾病": "高血壓性心臟病併心臟衰竭（急性失代償）",
        "排除可能疾病之診斷": "1. 缺血性心臟病 2. 瓣膜性心臟病 3. 慢性阻塞性肺病 4. 腎病症候群 5. 甲狀腺功能異常",
        "確認正確疾病之診斷": "Framingham 準則（端坐呼吸、陣發性夜間呼吸困難、頸靜脈怒張、肺囉音、心臟肥大）；"
                            "NT-proBNP 顯著升高；胸部 X 光心臟肥大併肺血管充血；心臟超音波評估 LVEF 與舒張功能",
        "處置方式": "靜脈利尿劑（furosemide）減輕鬱血、限鹽限水、每日量體重；血壓控制；"
                   "依 LVEF 分類啟動指引導向藥物治療（HFrEF：ACEI/ARNI、β 阻斷劑、MRA、SGLT2i；"
                   "HFpEF：SGLT2i、控制血壓與共病）；糖尿病與體重管理；心衰竭衛教與門診追蹤。",
        "englishDiseaseName": "Hypertensive Heart Disease with Acute Decompensated Heart Failure",
        "檢查預期所見": {"胸部X光": CXR3, "心電圖": CXR3_ECG},
    },
)

CXR4 = report("胸部X光", "右上肺野肺腫塊",
              ["右上肺野內側可見一大型、邊緣清楚、密度均勻之圓形腫塊，約 8 × 11 公分，佔據右上葉大部分並向下延伸至右肺門，"
               "內側緊貼右上縱膈，右氣管旁條紋增厚",
               "腫塊內未見空洞形成或鈣化",
               "右肺門可見數枚小而緻密之鈣化結節影，疑為鈣化淋巴結",
               "右側橫膈較左側略高約 2 公分；右側肺野整體密度略高於左側",
               "主動脈弓突出、迂曲；心影大小約在正常上限（心胸比約 0.5）",
               "左肺野清晰；兩側肋膈角銳利",
               "中線可見三枚圓形外物影（衣物暗扣或電極扣，非病灶）；兩側肩鎖關節退化性變化"],
              "未見肋膜積水、氣胸；未見胸管、引流管或其他治療痕跡；未見骨骼溶蝕性病灶", True)

CASE_CXR4 = case(
    fname="影像示範 CXR-4 肺癌.json",
    meta=meta("CXR", [target("胸部X光", "CXR", "nih-mass-00022192_003", True, CXR4),
                      target("心電圖", "ECG", None, False, normal_ecg(88))],
              "影像：右上肺野腫塊影緊鄰肺門、無管線。教學重點為肺癌的警訊症狀、分期檢查與告知壞消息（SPIKES）。"),
    basic={"姓名": "劉正雄", "年齡": 73, "身高": 165, "體重": 58, "性別": "男",
           "生日": "1953/03/28", "職業": "退休（前塑膠工廠作業員）"},
    mh={"主訴": "咳嗽 2 個月、痰中帶血 2 週、體重減輕",
        "既往疾病": "慢性阻塞性肺病 8 年",
        "目前病史": "病人為 73 歲男性，2 個月來持續乾咳，近 2 週痰中帶血絲，量少但每日出現；"
                   "3 個月內體重減輕 5 公斤、食慾差、容易疲倦。近 1 個月右側肩胛區隱隱作痛。"
                   "否認發燒、盜汗、呼吸困難惡化、聲音沙啞。",
        "過敏史": "無",
        "藥物史": "Tiotropium 吸入劑每日一次"},
    fh={"直系血親疾病": "弟弟 68 歲肺癌"},
    sh={"生活習慣": "退休後在家，活動量低",
        "飲食": "食慾差",
        "菸酒": "抽菸 50 年，每天一包半（75 包年），尚未戒菸；偶爾飲酒",
        "旅遊史": "無；曾於塑膠工廠工作 30 年"},
    ros={"全身性症狀": "體重減輕 5 公斤、食慾差、疲倦；否認發燒、盜汗",
         "相關系統症狀": "【呼吸】慢性咳嗽、痰中帶血絲，無呼吸困難加劇；右上肺野呼吸音略減弱"
                        "【肌肉骨骼】右側肩胛區鈍痛【神經】無頭痛、無局部神經症狀【腸胃】無腹痛"},
    vitals={"體溫": "37.2°C", "血壓": "132/80 mmHg", "心跳": "88 bpm",
            "呼吸次數": "20 breaths/min", "血氧飽和度": "93% on room air"},
    problem={
        "疾病": "肺癌（右上葉肺腫塊，高度懷疑非小細胞肺癌）",
        "排除可能疾病之診斷": "1. 肺結核（痰抗酸菌染色、TB PCR）2. 肺膿瘍 3. 肺部轉移腫瘤"
                            "4. 圓形肺炎 5. 肺部真菌感染",
        "確認正確疾病之診斷": "胸部 X 光右上肺野腫塊；胸部電腦斷層（含對比劑）評估腫塊、縱膈淋巴結與侵犯範圍；"
                            "支氣管鏡或 CT 導引切片取得組織病理；PET-CT 與腦部影像分期；痰液抗酸菌染色排除結核",
        "處置方式": "組織診斷與分期（TNM）為首要；依病理型態、分期與基因檢測（EGFR、ALK、PD-L1）"
                   "決定手術、放射治療、化療、標靶或免疫治療；戒菸諮詢；症狀控制（咳血、疼痛）；"
                   "以 SPIKES 架構告知診斷並討論治療目標。",
        "englishDiseaseName": "Lung Cancer (Right Upper Lobe Pulmonary Mass, suspected NSCLC)",
        "檢查預期所見": {"胸部X光": CXR4, "心電圖": normal_ecg(88)},
    },
)

# nih-nodule-00002578_000 (F/35) was the first choice; its three-reader read
# found the boxed nodule only "uncertain" (a faint 1 cm density over a rib
# crossing), so the case moved to nih-nodule-00012374_000 (M/34), whose 2.2 cm
# nodule is clearly visible and agreed by the box and both readers.
CXR5 = report("胸部X光", "左上肺野孤立性肺結節",
              ["左上肺野、主動脈弓外上方約左側第二前肋端處可見一顆邊緣清楚之圓形實質結節，直徑約 2.2 公分，部分與前肋重疊",
               "結節內未見明顯鈣化、空洞或周圍實質化",
               "餘兩側肺野清晰，兩側肺門無異常",
               "心臟大小正常，縱膈輪廓正常，氣管居中；兩側肋膈角銳利",
               "右下頸部及右鎖骨上區軟組織可見多枚數毫米長之細小線狀金屬異物，符合殘留針灸針斷片（位於胸腔外，與肺部無關）",
               "側別標記字樣呈鏡像反置，依主動脈弓、心尖及胃泡位置確認為常規方向"],
              "未見肋膜積水、氣胸；未見其他結節；未見導管、引流管或其他治療裝置", True)

CASE_CXR5 = case(
    fname="影像示範 CXR-5 孤立性肺結節.json",
    meta=meta("CXR", [target("胸部X光", "CXR", "nih-nodule-00012374_000", True, CXR5),
                      target("心電圖", "ECG", None, False, normal_ecg(72))],
              "影像：左上肺野 2.2 公分結節、無管線；右下頸部殘留針灸針斷片（病史已寫入）。"
              "教學重點為意外發現肺結節的風險分層（Fleischner）、CT 追蹤與病人溝通。"),
    basic={"姓名": "周柏翰", "年齡": 34, "身高": 172, "體重": 66, "性別": "男",
           "生日": "1992/05/14", "職業": "軟體工程師"},
    mh={"主訴": "公司健康檢查胸部 X 光發現異常，本人無不適",
        "既往疾病": "無；多年前曾因肩頸痠痛在中醫診所接受針灸與埋針治療",
        "目前病史": "病人為 34 歲男性，一週前公司年度健康檢查的胸部 X 光報告寫「左上肺野結節，建議進一步檢查」，"
                   "因而前來門診。病人本身完全無症狀：否認咳嗽、咳血、胸痛、呼吸困難、發燒、盜汗、體重減輕。"
                   "3 年前健檢胸部 X 光正常。否認結核病接觸史。",
        "過敏史": "無",
        "藥物史": "無"},
    fh={"直系血親疾病": "父親 62 歲肺癌（吸菸者）；母親健康"},
    sh={"生活習慣": "久坐辦公，每週慢跑 1–2 次",
        "飲食": "正常均衡",
        "菸酒": "不菸不酒；父親在家中吸菸（二手菸暴露約 20 年）",
        "旅遊史": "去年曾赴日本旅遊一週，無其他"},
    ros={"全身性症狀": "無發燒、盜汗、體重減輕、疲倦",
         "相關系統症狀": "【呼吸】無咳嗽、咳血、胸痛、呼吸困難【其他】無皮膚病灶、關節痛"},
    vitals={"體溫": "36.6°C", "血壓": "112/70 mmHg", "心跳": "72 bpm",
            "呼吸次數": "16 breaths/min", "血氧飽和度": "99% on room air"},
    problem={
        "疾病": "孤立性肺結節（意外發現，待定性）",
        "排除可能疾病之診斷": "1. 肉芽腫（結核、真菌）2. 錯構瘤 3. 早期肺腺癌（家族史、二手菸）"
                            "4. 類癌 5. 肺內淋巴結",
        "確認正確疾病之診斷": "胸部 X 光左上肺野 2.2 公分孤立性結節；低劑量或薄切胸部電腦斷層評估大小、密度、邊緣、"
                            "鈣化型態；依 Fleischner Society 指引與惡性風險模型（如 Brock）決定追蹤或切片；"
                            "必要時 PET-CT 或 CT 導引切片",
        "處置方式": "先取得舊片比對（3 年前正常 → 新發結節）；安排胸部 CT 定性；≥8 mm 且具風險因子者"
                   "依指引評估 PET-CT／組織切片，或 3 個月 CT 追蹤；與病人討論惡性可能性、追蹤計畫與焦慮處理；"
                   "避免二手菸暴露。",
        "englishDiseaseName": "Solitary Pulmonary Nodule (Incidental Finding)",
        "檢查預期所見": {"胸部X光": CXR5, "心電圖": normal_ecg(72)},
    },
)

CASES = [CASE_ECG1, CASE_ECG2, CASE_ECG3, CASE_ECG4, CASE_ECG5,
         CASE_CXR1, CASE_CXR2, CASE_CXR3, CASE_CXR4, CASE_CXR5]


def main():
    os.makedirs(OUT, exist_ok=True)
    # the two infarct cases were renamed when they were rewritten from the films
    for stale in ("影像示範 ECG-1 急性下壁心肌梗塞.json", "影像示範 ECG-2 急性前間壁心肌梗塞.json"):
        p = os.path.join(OUT, stale)
        if os.path.exists(p):
            os.remove(p)
            print("removed", p)
    for fname, data in CASES:
        path = os.path.join(OUT, fname)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
