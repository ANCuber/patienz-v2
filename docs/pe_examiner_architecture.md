# PE Examiner 系統架構圖

## 完整資料流與系統互動

```mermaid
flowchart TB
    subgraph Phase0["Phase 0：病患設定"]
        CONFIG["page/config.py"]
        TEMPLATE["模板題目 JSON<br/>(A/B/C/D)"]
        PROBLEM_SETTER["Problem Setter Agent"]
        SS_PROBLEM["ss.problem<br/>(病患完整 JSON)"]
        
        TEMPLATE -->|載入| CONFIG
        PROBLEM_SETTER -->|AI 生成| CONFIG
        CONFIG -->|儲存| SS_PROBLEM
    end

    subgraph Phase1["Phase 1：問診"]
        PATIENT_AGENT["Patient Agent"]
        SS_MESSAGES["ss.diagnostic_messages<br/>(對話紀錄)"]
        PATIENT_AGENT --> SS_MESSAGES
    end

    subgraph Phase2["Phase 2：理學檢查（PE Examiner 核心）"]
        direction TB

        subgraph UI["UI 層 — page/physical_exam.py"]
            RADIO1["① st.radio 選擇<br/>檢查部位（7大系統）"]
            RADIO2["② st.radio 選擇<br/>檢查項目（子分類）"]
            MULTI["③ st.multiselect 選擇<br/>檢查細項（預設全選）"]
            BTN["④ 按鈕：開始理學檢查"]
            DISPLAY["⑤ 結果顯示區<br/>st.markdown 渲染"]
            
            RADIO1 --> RADIO2 --> MULTI --> BTN
        end

        subgraph DATA["資料來源"]
            PE_CHOICE["pe_choice.json<br/>─────────────<br/>一般檢查：生命徵象, 外觀<br/>頭頸部：眼, 耳鼻, 口咽, 頸部<br/>胸部：心臟, 肺臟<br/>腹部：視/聽/叩/觸<br/>四肢：上肢, 下肢<br/>神經學：意識, 腦神經, 運動...<br/>皮膚：皮膚檢查"]
            PE_INST["pe_instruction.txt<br/>─────────────<br/>• 角色：理學檢查模擬器<br/>• 四大手法對應規則<br/>• 臨床準確性要求<br/>• Pertinent negatives<br/>• 不可直接提及診斷名"]
        end

        subgraph MODEL_INIT["模型初始化 — model/examiner.py"]
            direction TB
            SEARCH["getPDF 網路搜尋<br/>關鍵字：{disease} + <br/>physical examination findings"]
            SELENIUM["Selenium Chrome<br/>網頁 → PDF 轉換"]
            UPLOAD["genai.upload_file<br/>上傳 PDF 至 Gemini"]
            CREATE_MODEL["建立 Gemini 2.5 Flash<br/>─────────────<br/>temperature: 1<br/>top_p: 0.95<br/>max_tokens: 8192<br/>response: text/plain"]
            START_CHAT["啟動 Chat Session<br/>history 含 PDF context"]

            SEARCH --> SELENIUM --> UPLOAD --> CREATE_MODEL --> START_CHAT
        end

        subgraph API_CALL["API 呼叫與回應"]
            SEND["send_message<br/>─────────────<br/>Please provide the<br/>physical examination<br/>findings for: {items_str}"]
            RESPONSE["AI 回應（純文字 Markdown）<br/>─────────────<br/>**胸部 - 肺臟**<br/>• 視診：胸廓對稱<br/>• 觸診：無異常觸覺震顫<br/>• 叩診：雙肺叩診聲清<br/>• 聽診：左下肺細濕囉音"]
            SEND --> RESPONSE
        end

        SS_PE["ss.pe_result<br/>─────────────<br/>List of Tuples:<br/>[(category, result_text), ...]"]

        PE_CHOICE -->|提供選單| UI
        PE_INST -->|系統指令| MODEL_INIT
        SS_PROBLEM -->|病患資料| MODEL_INIT
        BTN -->|觸發| MODEL_INIT
        MODEL_INIT --> API_CALL
        RESPONSE -->|append| SS_PE
        SS_PE -->|讀取渲染| DISPLAY
    end

    subgraph Phase5["Phase 5：評分"]
        subgraph GRADER_V1["Grader v1（A-E 五面向）"]
            CONCAT_V1["串接所有資料<br/>對話 + PE結果 + 診斷"]
            PARALLEL["5 個 Grader 平行評分<br/>(ThreadPoolExecutor)"]
            CONCAT_V1 --> PARALLEL
        end

        subgraph GRADER_V2["Grader v2（OSCE 標準）"]
            COLLECT["collect_student_data()<br/>─────────────<br/>## 問診紀錄<br/>## 理學檢查紀錄 ← PE<br/>## 輔助檢查紀錄<br/>## 診斷與處置"]
            MARK_SCHEME["Mark Scheme Setter<br/>動態生成評分表"]
            GRADER_V2_AGENT["Grader v2 Agent<br/>逐項評分"]
            COLLECT --> MARK_SCHEME --> GRADER_V2_AGENT
        end
    end

    SS_PROBLEM --> Phase1
    Phase1 --> Phase2
    SS_PE -->|"## 理學檢查紀錄"| CONCAT_V1
    SS_PE -->|"## 理學檢查紀錄"| COLLECT
    SS_MESSAGES -->|對話紀錄| CONCAT_V1
    SS_MESSAGES -->|對話紀錄| COLLECT

    classDef phase fill:#e8f4f8,stroke:#2196F3,stroke-width:2px
    classDef core fill:#fff3e0,stroke:#FF9800,stroke-width:2px
    classDef data fill:#e8f5e9,stroke:#4CAF50,stroke-width:2px
    classDef ai fill:#fce4ec,stroke:#E91E63,stroke-width:2px
    classDef storage fill:#f3e5f5,stroke:#9C27B0,stroke-width:2px

    class Phase0,Phase1,Phase5 phase
    class UI,MODEL_INIT,API_CALL core
    class PE_CHOICE,PE_INST,DATA data
    class PATIENT_AGENT,PROBLEM_SETTER,CREATE_MODEL,SEND,RESPONSE,PARALLEL,MARK_SCHEME,GRADER_V2_AGENT ai
    class SS_PROBLEM,SS_MESSAGES,SS_PE storage
```

## PE Examiner 詳細互動序列圖

```mermaid
sequenceDiagram
    actor Student as 學生
    participant UI as physical_exam.py<br/>(Streamlit UI)
    participant JSON as pe_choice.json
    participant Init as examiner.py<br/>(create_pe_examiner_model)
    participant Web as getPDF<br/>(Selenium + Chrome)
    participant Gemini as Gemini 2.5 Flash<br/>(PE Examiner)
    participant SS as Session State<br/>(ss.pe_result)
    participant Grade as grade.py<br/>(Grading Phase)

    Note over Student,Grade: Phase 2：理學檢查流程

    UI->>JSON: 載入 pe_choice.json
    JSON-->>UI: 7大系統 + 子分類 + 細項

    Student->>UI: ① 選擇檢查部位（如：胸部）
    Student->>UI: ② 選擇檢查項目（如：肺臟）
    Student->>UI: ③ 選擇檢查細項（預設全選）
    Student->>UI: ④ 點擊「開始理學檢查」

    UI->>Init: create_pe_examiner_model(ss.problem, items_str)

    Note over Init: 步驟 1：取得醫學文獻 PDF
    Init->>Web: getPDF("{disease} physical examination findings")
    Web->>Web: Google 搜尋 → 取得 URL
    Web->>Web: Selenium 開啟 Chrome → 列印為 PDF
    Web-->>Init: tmp/{sid}_pe_features.pdf

    Note over Init: 步驟 2：設定 AI 模型
    Init->>Init: 讀取 pe_instruction.txt
    Init->>Gemini: 建立模型（system_instruction = 指令 + 病患資料）
    Init->>Gemini: upload_file(PDF) 作為多模態上下文
    Init->>Gemini: start_chat(history=[PDF context])

    Note over Init: 步驟 3：發送查詢
    Init->>Gemini: send_message("請提供 {items_str} 的理學檢查結果")

    Gemini->>Gemini: 根據病例 + PDF + 指令<br/>生成臨床準確的檢查結果
    
    Note over Gemini: 輸出規則：<br/>1. 異常發現 → 詳細描述<br/>2. 正常發現 → 簡要記錄<br/>3. Pertinent negatives → 必須報告<br/>4. 不可提及疾病名稱

    Gemini-->>Init: Markdown 格式檢查結果

    Init-->>UI: result (文字)
    UI->>SS: ss.pe_result.append(("胸部-肺臟", result))
    UI->>UI: st.rerun() → 重新渲染頁面
    UI-->>Student: 顯示理學檢查結果

    Note over Student,Grade: 學生可重複選擇不同系統進行多次檢查

    Student->>UI: 選擇下一個系統...
    UI->>Gemini: 重新建立模型 + 發送查詢
    Gemini-->>UI: 新的檢查結果
    UI->>SS: ss.pe_result.append((...))

    Note over Student,Grade: Phase 5：PE 結果流向評分

    Grade->>SS: 讀取 ss.pe_result
    
    Note over Grade: Grader v1：<br/>串接為 "***理學檢查結果***" 段落
    Grade->>Grade: chat_history += PE results

    Note over Grade: Grader v2：<br/>collect_student_data() 中<br/>格式化為 "## 理學檢查紀錄"
    Grade->>Grade: data_parts.append(PE markdown)
```

## 資料格式摘要

### 輸入資料
| 資料 | 來源 | 格式 |
|------|------|------|
| 病患資料 | ss.problem | JSON 字串 |
| 選擇的檢查項目 | pe_choice.json → UI | `"胸部 - 肺臟: 視診, 觸診, 叩診, 聽診"` |
| 醫學文獻 | getPDF → Selenium | PDF 檔案（多模態上下文） |
| 系統指令 | pe_instruction.txt | 純文字 prompt |

### 輸出資料
| 資料 | 目的地 | 格式 |
|------|--------|------|
| 檢查結果 | ss.pe_result | `List[Tuple[str, str]]` |
| 傳至 Grader v1 | chat_history 串接 | 純文字段落 |
| 傳至 Grader v2 | collect_student_data() | Markdown `## 理學檢查紀錄` |
