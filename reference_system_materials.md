# Patienz-v2 系統參考資料總整理

## 一、AI Agent 系統指令 (`instruction_file/`)

| 檔案 | Agent 角色 | 用途 |
|------|-----------|------|
| `patient_instruction.txt` | 虛擬病人 | 角色扮演指令：用白話描述症狀、不使用醫學術語、資訊與病歷一致 |
| `problem_setter_instruction.txt` | 出題者 | 生成完整病患 JSON（基本資訊、MH/FH/SH、LQQOPERA、鑑別診斷、處置） |
| `pe_instruction.txt` | 理學檢查模擬 | 各身體系統檢查結果生成：視/觸/叩/聽、含具體數值與 pertinent negatives |
| `examiner_instruction_text.txt` | 文字型檢查報告 | 影像/內視鏡/功能檢查的 markdown 報告格式（impression + findings） |
| `examiner_instruction_val.txt` | 數值型檢驗結果 | JSON 格式實驗室數值生成（englishName + value），含危險值閾值 |
| `mark_scheme_setter_instruction.txt` | 評分表生成 | 依病例生成 OSCE 評分表：6 大類別、25-40 題、40-60 分 |
| `grader_v2_instruction.txt` | 評分官 (v2) | 全面評分指令：二元/三元計分、各類別評分準則、JSON 輸出 |
| `advisor_instruction.txt` | 教學顧問 | Pendleton 回饋模式、鷹架策略、鼓勵性回饋 |
| `grader_inst_A.txt` ~ `grader_inst_E.txt` | 評分官 (v1 legacy) | 舊版分維度評分（A-E 五大面向） |

---

## 二、檢查與理學檢查資料庫 (`examination_file/`)

### examination.csv — 檢驗項目主資料庫（250+ 項）
包含欄位：檢驗項目、名稱、參考值（含性別差異）、單位

涵蓋範圍：
- **血液檢驗**：CBC（Hb, RBC, WBC, Hct, Platelets, MCV, MCH, MCHC）
- **糖尿病**：Glucose, HbA1c, Insulin, C-peptide
- **肝功能**：AST, ALT, Bilirubin, Albumin, ALP
- **血脂肪**：Triglyceride, Total cholesterol, HDL, LDL
- **腫瘤標記**：AFP, CEA, CA125, CA199, PSA
- **甲狀腺**：T3, T4, TSH, Free T4, 抗體
- **生化**：Iron, Calcium, Na, K, Cl, Mg, P
- **特殊血液**：LDH, CK, Troponin, CRP, D-dimer
- **腎功能**：BUN, Creatinine, eGFR
- **凝血功能**：PT, APTT, INR, Fibrinogen
- **尿液/體液**：比重, pH, 蛋白, 糖, 膽紅素
- **微生物/培養**：血液培養, 尿液培養, 快篩
- **血氣分析**：動脈/靜脈 pH, pO2, pCO2, HCO3

### examination_choice.json — 檢查分類選單
```
實驗室檢查: 血液/糖尿病/肝功能/血脂肪/腫瘤標記/甲狀腺/生化/特殊血液/腎功能/凝血/免疫學
尿液體液:   一般尿液/特殊尿液/糞便/腦脊髓液
微生物:     培養/快篩
影像檢查:   X光/超音波/CT/MRI/其他影像
特殊檢查:   心電圖/動脈血氣/靜脈血氣/功能檢查/內視鏡
```

### pe_choice.json — 理學檢查系統分類
```
一般檢查: 生命徵象, 一般外觀
頭頸部:   眼, 耳鼻, 口咽, 頸部
胸部:     心臟, 肺臟
腹部:     視診, 聽診, 叩診, 觸診
四肢:     上肢, 下肢
神經學:   意識, 腦神經, 運動系統, 感覺系統, 反射
皮膚:     皮膚檢查
```

---

## 三、知識庫 PDF (`knoledge_base/`)

| PDF 檔案 | 用途 |
|----------|------|
| OSCE_Cases_With_Mark_Schemes.pdf | OSCE 標準病例與評分範本 |
| Harrison's Principles of Internal Medicine (Vol.1 & Vol.2) | 內科學聖經 |
| CURRENT Medical Diagnosis & Treatment 2026 (65th ed.) | 最新診斷與治療指引 |
| Oxford Handbook of Clinical Examination and Practical Skills | 理學檢查技巧手冊 |
| Clinical Examination: A Systematic Guide to Physical Diagnosis | 系統性身體檢查指南 |
| Short and OSCE Cases in Internal Medicine Clinical Exams | 臨床案例練習 |
| Symptom to Diagnosis: An Evidence Based Guide | 症狀導向診斷邏輯 |

---

## 四、模板題目 (`data/template_problem_set/`)

| 檔案 | 內容 |
|------|------|
| 模板題 - A.json | 預設病例 A（如：A 型流感） |
| 模板題 - B.json | 預設病例 B |
| 模板題 - C.json | 預設病例 C |
| 模板題 - D.json | 預設病例 D |
| 問卷測試用 1.json / 2.json | 問卷系統測試用病例 |

### 病例 JSON 結構
```json
{
  "基本資訊": { "姓名", "年齡", "身高", "體重", "性別", "生日", "職業" },
  "MH": { "既往疾病", "目前病史"(含 LQQOPERA), "過敏", "藥物史" },
  "FH": { "家族病史摘要" },
  "SH": { "社會史、旅遊/接觸史、生活習慣" },
  "Problem": { "疾病", "鑑別診斷", "處置方式", "englishDiseaseName" }
}
```

---

## 五、系統常數與 UI 設定

### constants.py — UI 文字常數
- **六階段名稱**: 病患設定 → 問診 → 理學檢查 → 檢查 → 診斷 → 評分
- **頁面代號**: config(0), test(1), physical_exam(2), examination(3), diagnosis(4), grade(5)
- **圖示**: 🩺📝🏥🔬💊📚
- **角色頭像**: student⚕️, patient😥, advisor🏫, grader🏫

### 技術設定
- **AI 模型**: Google Gemini 2.5 Flash
- **環境變數**: `GEMINI_API_KEY`
- **語言**: 全 UI 使用繁體中文 (zh-TW)
- **Session ID**: 時間戳格式，用於 log 檔名 (`data/log/{SID}.txt`)

---

## 六、部署與依賴

### requirements.txt 主要依賴
- `streamlit` — Web UI 框架
- `google-generativeai` — Gemini API
- `googlesearch-python` — 網路搜尋
- `pdfkit` — PDF 生成
- `SpeechRecognition` — 語音轉文字 (zh-TW)
- `selenium` + `webdriver-manager` — 網頁擷取/PDF 轉換

### 啟動方式
```bash
source init.sh        # 初始化 venv + 安裝依賴
export GEMINI_API_KEY="..."
streamlit run home.py  # 或 ./run.sh
```
