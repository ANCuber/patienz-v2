# PaTiENZ 升級成果測試報告 + 手動驗證步驟

> 對應 `UPGRADE_PROPOSAL.md` 的 P0（速度急救 + 正確性）。本批次已實作、靜態驗證並通過自動化測試；因本機**無 `GEMINI_API_KEY`、無法跑互動式 Streamlit**，凡需真實 API 的行為皆附「請你手動驗證」步驟。

---

## A. 本次完成的升級（對應回饋與會議）

| # | 升級 | 解決的真實回饋 | 主要檔案 |
|---|---|---|---|
| 1 | **遷移到 `google-genai` 新 SDK + 關閉 thinking** | 「問一個問題等 2–3 分鐘」「回覆很慢也沒回答完」（截斷） | `util/llm.py`、`model/*.py` |
| 2 | **移除執行期 Selenium 爬蟲**（病人/顧問改用病例 JSON 接地） | 「爬蟲時間長」「公用網路無法使用」「病人太典型全講出來」 | `model/patient.py`、`model/advisor.py`、`util/tools.py` |
| 3 | **評分並行化（OSCE∥ACGME）+ 序列 fallback** | 「grading 時間有點久」 | `util/grading_pipeline.py`、`page/grade.py` |
| 4 | **評分模式分流（OSCE / ACGME / 兩者）** | 「很難及格，有簡易模式嗎」＋會議「從一開始分流」 | `page/config.py`、`page/grade.py` |
| 5 | **詳解（標準答案）面板 + 同義詞容忍對照** | 「希望結束後有正確診斷/詳解」「DMARD 差一個 s 不給分」 | `page/grade.py`、`util/grading_normalize.py` |
| 6 | **小兒/年齡-生日一致性硬檢查 + 病人年齡釘定/家屬** | 「標註53歲、生日2013、回答11歲、無家屬陪伴」 | `util/demographics.py`、`model/patient.py`、`page/config.py` |
| 7 | **檢查自由輸入逃生口（其他檢查）** | 「RA 抽不到 RF」「不能驗 Creatinine/stool」「想做的檢查沒有」 | `page/examination.py` |

**思考預算政策**（速度核心）：病人 / 檢查 / 顧問 / lab_advisor = `thinking_budget=0`（關閉）；problem_setter / mark_scheme = 512；grader_v2 / acgme = 2048。

---

## B. 驗證結果（自動化，無需 API 金鑰）

全部於本機 `venv`（Python 3.13）執行通過：

| 驗證 | 指令 | 結果 |
|---|---|---|
| 全部變更檔語法編譯 | `python -m py_compile <18 個檔>` | ✅ ALL OK |
| 模組可匯入 + **Schema 實際建構**（最易出錯處） | 匯入 `model.*` / `util.*` | ✅ 12/12 OK |
| Grader 工廠建出 ModelHandle + thinking 預算正確 | — | ✅ grader_v2=2048, acgme=2048 |
| 新 SDK API 形狀（chats/config/schema/safety/thinking/response.text）| 對 `google-genai 2.10.0` 實測建構 | ✅ 全部相符 |
| 單元測試 | `python -m pytest tests/ -q` | ✅ **23 passed** |
| 程式碼殘留舊 SDK 引用 | grep | ✅ 無（僅 docstring 說明文字） |

**測試涵蓋（`tests/`）**：
- `test_demographics.py`：重現「53歲/生日2013」bug 會被修正、真實小兒被標記、年齡字串/超界正規化、無法解析生日會重建。
- `test_grading_normalize.py`：DMARD≡DMARDs、x光攝影≡x-ray、肺栓塞≡PE；**並驗證短縮寫不再誤判**（RF≠CRF、CT≠ECT）。
- `test_grading_pipeline.py`：並行執行、單一 task 失敗被隔離捕捉、真實時間重疊（並行）。
- `test_llm_and_agents.py`：`build_config` thinking 設定、safety 為 4 項 list、`ModelHandle` 行為、各 agent thinking 預算、病人為 thinking-off 且**不再含 getPDF 爬蟲**、小兒 note、檢查 agent schema。

### 對抗式審查（multi-agent）
已對全部 diff 跑 4 視角對抗式審查（SDK 正確性 / 執行期·執行緒 / 回歸 / 邏輯），共 19 項原始發現，經彙整保留 **5 項真實缺陷並全數修復**：
1. （critical）「僅 ACGME」模式原本是死的（`want_acgme` 只認 `both`）→ 已修為支援 `acgme`。
2. （critical）ACGME 區塊渲染原綁定 OSCE 輸出 → 已解耦。
3. （high）ACGME-only 不會自動存檔 → 已支援三種模式存檔。
4. （high）`terms_match` 短縮寫子字串誤判 → 已加長度防護並補測試。
5. （low）ACGME 失敗缺診斷日誌 → 已改用 `_grading_response_text`。
（其餘 14 項為誤報/風格，審查報告已逐一說明，例如 thread closure crash 實際被 `run_parallel` 攔截。）

---

## C. 你需要手動驗證的項目（需 `GEMINI_API_KEY` + 實際操作）

> 因本環境無金鑰與互動瀏覽器，以下為**端到端**驗證；自動化已涵蓋邏輯層。

### 0. 安裝與啟動
```bash
cd "patienz-v2"
# 啟用 venv 後：
pip install -r requirements.txt          # 會安裝 google-genai（已移除 google-generativeai）
export GEMINI_API_KEY="<你的金鑰>"        # Windows PowerShell: $env:GEMINI_API_KEY="..."
python -m pytest tests/ -q               # 應顯示 23 passed
streamlit run home.py
```

### 1. 速度（thinking 關閉）— 對應「問一句等 2–3 分鐘」
- 在「問診區」問病人 3–5 句。**主觀感受**：回應應明顯變快、且**不再出現回答到一半被截斷**。
- 打開 `data/log/<SID>.txt`，看 `[PERF]` 行：`grader_v2=…s`、`acgme_grader=…s`、`mark_scheme=…s` 應比過去縮短。
- ✅ 驗證點：互動回應變快、無截斷、PERF 數字下降。

### 2. 無爬蟲 / 公用網路 — 對應「爬蟲時間長 / 公用網路無法使用」
- 進入問診時，**不應再看到「正在搜尋病症特徵…」**這個 spinner（該步驟已移除）。
- 在受限網路（如公用 Wi-Fi）下，建立病人模型不應因連 UpToDate 失敗而卡住。
- ✅ 驗證點：問診第一輪明顯更快、無外部抓取。

### 3. 評分模式分流 — 對應「有沒有簡易模式 / 從一開始分流」
- 在「病患設定區」最上方有新的 **「評分模式」** 下拉：`OSCE + ACGME（完整）`／`僅 OSCE（較快）`／`僅 ACGME`。
- 跑一題選「僅 OSCE」→ 評分頁**只出現 OSCE**、無 ACGME 區塊、速度更快。
- 跑一題選「僅 ACGME」→ 評分頁**只出現 ACGME 區塊**（不再是空白頁）。
- 跑一題選「完整」→ 兩者都出現；看 `[PERF]`：`grader_v2` 與 `acgme_grader` 應為**並行**（總等待 ≈ 較慢的那一個，而非兩者相加）。
- ✅ 驗證點：三種模式都正確渲染且各自存檔到 `data/grading_results/`。

### 4. 詳解（標準答案）面板 — 對應「希望有正確答案與詳解」
- 評分頁應出現 **「📖 解答與標準作法（詳解）」**：正確主診斷、應排除鑑別、標準處置，並列出「你的作答對照」與**處置覆蓋對照**（✅/⬜）。
- ✅ 驗證點：詳解內容正確、覆蓋對照合理。

### 5. 同義詞容忍 — 對應「DMARD 差一個 s 不給分」
- 在「診斷區」處置欄輸入 `DMARD`、`x光攝影`（而標準答案為 `DMARDs`、`x-ray`）。
- 到評分頁詳解的**處置覆蓋對照**，這些應顯示 ✅（視為同概念）。
- ✅ 驗證點：單複數／中英／同義詞被視為相同。

### 6. 小兒一致性 — 對應「53歲/生日2013/回答11歲、無家屬」
- 病患設定把年齡區間設在 0–12（小兒），出題。
- 在「病人資料」確認**年齡與生日一致**；於問診問「你今年幾歲？」→ 病人回答應與設定一致，且涉及病史時表現出**家屬代答/陪同**口吻。
- ✅ 驗證點：年齡前後一致、未成年有家屬情境。

### 7. 檢查自由輸入 — 對應「RA 抽不到 RF / 想做的檢查沒有」
- 「檢查區」展開 **「🔎 找不到想做的檢查？自由輸入其他檢查／影像」**，輸入 `Rheumatoid factor (RF)` → 加入 → 開始檢查。
- 應產生該檢查的敘述式結果，且可被後續評分採計。
- ✅ 驗證點：清單外的檢查也能開立、有結果。

---

## D. 限制與回滾

**目前限制（誠實揭露）**：
- 端到端（真實 Gemini 回應品質、Streamlit UI 行為）**需你以金鑰實測**；本機僅能驗證到「建構/匯入/邏輯/並行」層。
- 詳解的「處置覆蓋對照」是**同義詞啟發式顯示輔助**，實際分數仍以 AI 考官評分為準（刻意保守，不會把考官沒給的硬說成有）。
- 影像（真實 ECG/X 光圖庫）、組套一鍵全選、Enter 送出等 UI 細節屬 P1/P2，本批未做（見 `UPGRADE_PROPOSAL.md` 路線圖）。

**回滾**（若線上實測有問題）：
```bash
pip install google-generativeai      # 還原舊 SDK
# 還原本批變更的檔案（git）：
git checkout -- util/ model/ page/ requirements.txt
```
（新增檔 `util/llm.py`、`util/grading_*.py`、`util/demographics.py`、`tests/` 可直接刪除。）

---

## E. P1 批次（已完成，接續於同一分支/PR）

| # | 升級 | 對應回饋／會議 | 檔案 |
|---|---|---|---|
| §9-A | **評分表重用快取** | 「scheme consistent」「存 grading scheme 資料庫，相似就拿」＋加速 | `util/mark_scheme_cache.py`、`page/grade.py` |
| §3-A | **一鍵組套（檢查套餐）** | 「要一個一個點/可以設定組套」「CBC/DC 不應逐項點」 | `util/exam_panels.py`、`examination_file/exam_panels.json`、`page/examination.py` |
| §5-A | **病人不再「只說不舒服而拒答」** | 「持續顯示不舒服無法回答」 | `instruction_file/patient_instruction.txt` |
| §5-B | **可選病人個性／情緒** | 「設定個性機車一點／焦慮的病人」 | `config_options.json`、`page/config.py`、`model/patient.py` |

**已確認既有 prompt 已涵蓋、未重複實作**：§4-A 行為即證據、§4-B 語意等價（DMARD≡DMARDs、x光≡x-ray）、§4-C 不重複計分、§5 病人擬真（繁中／不主動揭露／不背 LQQOPPERA／情緒語氣）。

**驗證**：新增 `tests/test_mark_scheme_cache.py`、`tests/test_exam_panels.py` 及 persona 測試；全套 **33 passed**；變更檔 py_compile 通過。已對本批做對抗式審查（14 項 → 保留 3 項），修復 2 項真實 robustness（快取原子寫入、版本失效），並具理由駁回 1 項 high 誤報（評分表只依「病例」非「學生表現」，故重用正確；把病例內容塞進 key 反而會破壞 §9-A 的跨病人重用）。

### P1 手動驗證
- **組套**：檢查區 →「⚡ 一鍵組套」→ 點「常規入院抽血」→ 檢查單一次加入多項。
- **重用快取**：同一疾病/身份/難度連續出兩題並評分；第二題 `data/log/<SID>.txt` 應出現 `[PERF] mark_scheme=…s cache=True`（命中、幾乎 0 秒）。改 `instruction_file/mark_scheme_setter_instruction.txt` 後想強制重生，可刪 `data/mark_scheme_cache/` 或將 `SCHEME_CACHE_VERSION` +1。
- **病人個性**：設定區選「焦慮緊張」→ 問診時病人語氣明顯焦慮、會追問嚴重性；病情事實不變。
- **不舒服不再卡死**：問診時病人即使表達不適也會回答問題，不再只回「我很不舒服」。

## F. §7 流程彈性（已完成）

| 項目 | 內容 | 檔案 |
|---|---|---|
| §7-A | **自由探索模式（opt-in）**：開啟後可在問診／理學／檢查／診斷各階段間自由前進返回（先做篩檢、檢查後補問診等）；**預設關閉**，維持標準 OSCE 逐步順序以保留「鑑別診斷提出時機」的評分。 | `util/navigation.py`、`util/tools.py`、`page/config.py` |
| §7-A | **初步鑑別改為可回頭修改的檢查點**：鎖定後新增「✏️ 重新編輯清單」。 | `page/pre_ddx.py` |
| 強健性 | 計時器容忍被跳過的階段（`start_time=None` 不再 crash）；自由模式側欄各階段可點選。 | `util/tools.py` |

**設計取捨**：自由探索預設**關閉**——因為「先提出初步鑑別、再看檢查結果」是本系統刻意的臨床推理時序評分點。想要臨床交錯流程者可於設定區開啟。

**驗證**：新增 `tests/test_navigation.py`（標準/自由模式 gating）；全套 **37 passed**。對抗式審查（兩視角）確認「**關閉時為完全無行為變更（true no-op）**」，並抓到 1 個 critical：自由模式直接跳到評分時 `ss.diagnosis/ddx/treatment` 未初始化會 crash → 已於 `init_all` 補預設值（標準模式下會被診斷頁覆寫，無副作用）。

### §7 手動驗證
- 設定區勾選「自由探索模式」→ 側欄各階段（問診/理學/檢查/診斷/評分）皆可直接點選切換；可先做檢查、再回問診。
- 不勾選 → 行為與升級前完全相同（仍需逐步解鎖、檢查前須鎖定初步鑑別）。
- 初步鑑別鎖定後 → 出現「✏️ 重新編輯清單」可解鎖修改。

## G. §7-B 闖關模式（已完成）

| 項目 | 內容 | 檔案 |
|---|---|---|
| §7-B | **流程模式三選一**：標準 OSCE／自由探索／**闖關**。闖關模式把看診拆成「臆斷→篩檢→再鑑別→確診」四關卡，側欄即時顯示**闖關進度**（✅通關／▶進行中＋本關目標／🔒未解鎖），著重 thinking process 而非一次性標準答案。 | `util/stages.py`、`util/tools.py`、`page/config.py` |

**設計**：闖關沿用**標準逐步 gating**（`free_navigation=False`），只額外加上闖關引導 UI，故不更動既有導覽/評分；關卡完成判定為純函式（`util/stages.py`）。

**驗證**：新增 `tests/test_stages.py`（關卡狀態、邊界、非連續進度）；全套 **43 passed**；py_compile 通過。對抗式審查（兩視角）→ **0 項真實缺陷**（5 項皆為「session state 被竄改」的誤報，實際資料流已保證型別正確），確認**非闖關模式完全不受影響**。

### §7-B 手動驗證
- 設定區「流程模式」選「闖關模式」→ 側欄出現「闖關進度」，隨問診/臆斷/檢查/診斷推進，關卡依序 ✅。
- 選「標準」或「自由探索」→ 不顯示闖關進度，行為與先前一致。

## H. §1-C Gemini 顯式 Context Caching（已完成）

| 項目 | 內容 | 檔案 |
|---|---|---|
| §1-C | 病人＋三個檢查官（數值/文字/PE）這四條互動熱路徑，每一輪都重送「agent 指令＋完整病例 JSON」的固定前綴（數千 tokens）。現改為以 **Gemini 顯式 context cache**（`client.caches.create`）上傳一次、之後每輪以名稱引用 → 前綴不必每輪重新處理（**降低每輪延遲**），且快取 tokens 以折扣計價（**降低成本**）。 | `util/context_cache.py`（新）、`util/llm.py`、`model/patient.py`、`model/examiner.py` |

**安全設計（任何失敗都不影響功能）**：
- **建立失敗即退回**：前綴低於模型最低門檻（2.5 Flash 為 1,024 tokens）、配額、網路等任何錯誤 → 自動走原本「每輪帶 system_instruction」路徑，行為與 §1-C 之前完全相同。建立呼叫帶 **10 秒逾時**，受限網路（如公用網路）不會卡在「正在建立模型」。
- **執行中自癒（只認快取死亡）**：快取過期/失效（API 回 400/403/404）時，代理層**以原 system_instruction 重建對話（歷史保留）並自動重試一次**，同時刪除死快取；**瞬時錯誤（429/5xx/連線）直接上拋**由頁面顯示「請再試一次」，不會因一次抖動就永久放棄健康的快取。
- **不留計費孤兒**：讀取進度存檔重建模型時，舊對話的伺服器端快取會**主動刪除**（顯式快取按 token-小時計儲存費，不刪只能等 TTL 到期）。
- **可關閉**：`PATIENZ_DISABLE_CONTEXT_CACHE=1` 完全停用；`PATIENZ_CACHE_TTL_SECONDS` 可調 TTL（預設 7200）。
- **不動評分器**：graders 為一次性呼叫，快取無益，維持原路徑。advisor 的大宗上下文在對話歷史（primer）而非 system instruction，且為低頻問答，暫不納入。

**連帶強化（審查發現的既有弱點，一併修復）**：
- `page/physical_exam.py`／`page/examination.py` 的檢查官呼叫原本**沒有錯誤處理**——任何 API 錯誤都會讓學生看到英文 traceback。現改為與問診頁相同的中文警告＋重試提示。
- `page/examination.py` 原本在**送出前**就把整張檢查單標記為「已做過」——中途失敗會讓未完成的檢查被重複開立檢查誤判鎖住。現改為**交易式**：成功取得結果的項目才標記並移出檢查單，失敗項目保留供重試。

**對抗式審查**：3 視角 × 逐項反駁驗證（13 agents）→ 10 項原始發現、9 項確認（2M+2H+5L，其中 3 項為同一快取洩漏問題的不同面向）、1 項駁回；**全部修復**並各自補上單元測試（瞬時錯誤不觸發回退、回退時刪快取、建立逾時、conftest 硬性斷網）。

**驗證（無金鑰、自動化）**：新增 `tests/test_context_cache.py`（15 tests：env 開關、TTL、建立成功/失敗/逾時參數、`build_config` 互斥防呆、快取聊天保留 generation 設定與 response_schema、過期自癒重試一次、不無限重試、429/5xx/連線錯誤不放棄快取、刪除快取容錯、病人/檢查官整合）→ 全套 **58 passed**；py_compile 全數通過。

### §1-C 手動驗證（需 `GEMINI_API_KEY`）
1. `streamlit run home.py` → 建立病例、進入問診。伺服器 console 應出現 `` [CACHE] created cachedContents/… (patienz-patient-<SID>) ``。
2. 問診多輪，對話正常；到檢查區下檢查時應再看到 `patienz-examval-…`／`patienz-examtext-…`／`patienz-pe-…` 的 `[CACHE] created` 行。
3. 用量核對（可選）：`python -c "from google import genai,os; [print(c.name, c.display_name) for c in genai.Client().caches.list()]"` 應列出本 session 的快取。
4. 回退驗證：`PATIENZ_DISABLE_CONTEXT_CACHE=1 streamlit run home.py` → console 無 `[CACHE]` 行，一切行為與先前版本相同。
5. 延遲比較（可選）：同一病例分別在開/關快取下問診 5 輪，比較回覆時間；快取路徑的第 2 輪起應明顯較快（前綴越大差距越明顯）。

**剩餘建議（P2）**：§6 真實影像庫（PTB-XL/NIH CXR，需取得資料集授權且須以真實影像而非生成影像）。（Enter 送出已由 `st.chat_input` 內建。）
