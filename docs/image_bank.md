# 真實影像庫（§6-A Real Diagnostic Image Bank）

醫學生回饋一再要求「心電圖、X 光放**真實圖片**練習判讀」。本功能讓檢查區的影像報告
（心電圖／X 光／CT／MRI／超音波…）旁邊，附上一張**真實、去識別化**的示範影像。

## 為什麼一定是「真實影像」而非生成影像

放射科醫師辨識**合成胸部 X 光**的正確率僅約 62–78%，且生成模型有**幻覺出病灶**
（hallucinated pathology）的已知風險。用 AI 生成的 ECG/CXR/CT 來訓練判讀，等於教學生
讀「捏造的病灶」。因此**診斷關鍵影像一律使用真實影像庫檢索**；生成式影像僅可用於
非診斷性外觀示意（皮疹等），且必須標註「AI 生成、非真實」。

## 運作方式（安全設計）

- 影像存放於 `image_bank/`（可用環境變數 `PATIENZ_IMAGE_BANK_DIR` 覆寫），由
  `image_bank/manifest.json` 編目。**影像二進位不進版控**（`.gitignore`），由你在本機填充。
- 檢索（`util/image_bank.py`）**絕不顯示可能誤導的影像**——影像必須**全數通過**下列關卡，
  否則 `find_image` **回傳 None**，畫面只顯示文字報告（絕不亂猜）：
  - **顯示閘（`verified: true`）**：只有標為 `true` 的條目會顯示。自動抓取的項目一律
    `verified:false`，在有人把它改成 `true` 之前對學生**完全不顯示**——一張被誤標的
    Commons 圖不可能外洩。**注意這個欄位只回答「可不可以顯示」，不回答「有沒有醫師確認過」**，
    兩者是不同的軸（見下面「畫過框不等於驗證過」）。
  - **modality 硬性比對**：開心電圖不會回傳 X 光。
  - **異常影像**：該片**自身的 ground-truth 所見必須「出現在本次報告文字」中**（比對片子的
    `findings`，而非本案的主診斷）。所以「缺血性中風」的報告不會叫出「腦出血」片；ECG 報「心房顫動」
    不會叫出「STEMI」片。用 `util/grading_normalize.surface_forms` 做中英/同義的「受控詞彙→敘述」比對，
    只把片子自己的所見詞去報告裡找，不會把兩個不相干的概念用子字串硬湊在一起。
  - **否定子句不算證據**：報告寫「肺野**未見**浸潤」時，「浸潤」不會被當成陽性證據。比對以子句為
    單位，跳過含否定詞的子句（刻意排除「非」與單獨的 "negative"——「非特異性 ST 變化」與
    "negative T wave" 本身是所見，不是否定）。
  - **影像不得主張報告沒說的事**（互斥屬性閘）：見下節。
  - **正常影像**：單一部位的 modality（ECG、CXR、ECHO）直接視為安全示範；多部位的 modality
    （US、XR、CT、MRI、ENDO、NM）**必須與本次開單的部位相符**，所以「正常腹部超音波」不會被拿去
    配「頸動脈超音波」的開單。
  - 報告未附 `[NORMAL]`/`[ABNORMAL]` 標記時（正異常未知），**不顯示影像**（`page/examination.py`
    的 `normality_known` 關卡）——寧可純文字，也不猜。

### 互斥屬性閘：影像不得主張報告沒說的事

只比對「影像的所見有沒有出現在報告裡」是不夠的——它從不反問「影像有沒有主張報告沒說、或說了相反的事」。
一張 caption 寫著 `anteroseptal myocardial infarction` 的片，可以只憑上位詞「心肌梗塞」通過比對，
然後對著一份描述 II、III、aVF 上升的報告宣告錯誤的定位。**定位錯＝責任血管錯＝處置錯。**

因此檢索會抽出四個**互斥屬性族**並逐一比對（`util/image_bank.py`）：

| 族 | 值 | 來源 |
|---|---|---|
| `territory` 心肌定位 | anterior／septal／lateral／inferior／posterior（以**集合**表示）| 部位名（下壁、前間壁…）**或 ECG 導程**：II·III·aVF＝下壁、V1·V2＝中隔、V3·V4＝前壁、V1-V4＝前間壁、I·aVL·V5·V6＝側壁 |
| `completeness` | complete／incomplete | 完全性／不完全性傳導阻滯 |
| `lesion` | infarction／injury／ischemia | 梗塞／損傷／缺血 |
| `laterality` 側性 | right／left／bilateral | **不從資料集標籤推斷**，由審閱者以 `tools/annotate_image_bank.py` 填入 |

定位只在**病灶陳述**上成立（`lesion` 有值才算）：`left anterior fascicular block` 與
`septal hypertrophy` 講的是傳導路徑與解剖構造，不是梗塞定位，若把它們當成「帶定位」，
規則 5 會讓它們對所有沒點名心肌部位的報告一律不出圖。

五條規則：

1. **矛盾即淘汰**：報告與影像都點名同族的值、且**不相交** → 淘汰，不看分數。
   定位用集合交集判斷，所以 {inferior} 與 inferolateral 片相容（有交集）、與 anteroseptal 片不相容。
2. **上位詞不能單獨成立**：「心肌梗塞」這種上位詞只用來擴大召回；要算「具體命中」，必須是影像自己的
   所見被證實，**或**上位詞成立且定位相符。只要有具體命中的候選，全部僅靠上位詞的候選一律丟棄。
3. **人口學只在臨床同級之間排序**：年齡/性別退回純 tiebreak，不能跨所見決勝負。
4. **主要所見取自結論子句**：報告有「整體印象／印象／impression／診斷」標頭時，**只有**該標頭的子句（及其後一句）算結論；
   沒有標頭才退而找「符合／考慮／研判／consistent with」等提示詞（都沒有則取最後一句）。
   報告順帶提到的所見不能贏過結論——否則一份下壁 STEMI 報告會因為順帶寫了「竇性心搏過速」而配到心搏過速的片；
   具體發現裡的「研判為橫膈上抬……或輕度肺膨脹不全」也不會把肺膨脹不全升格成主要所見。
5. **報告沒說就不給宣稱的片**：影像宣稱了報告從未主張的值 → 淘汰。定位（territory）如此，
   **完全性／不完全性（completeness）與病灶型態（lesion）也一樣**——否則報告只寫「右束支傳導阻滯」時，
   人口學 tiebreak 會替考官決定那是完全性還是不完全性（兩者是不同的判讀）。
   例外是報告自己給了判準：束支阻滯的報告若寫出 QRS 時間，**QRS ≥120 ms 視為完全性、<120 ms 視為不完全性**
   （AHA/ACCF/HRS 2009 心電圖標準化建議），因此「QRS 140 ms 呈左束支傳導阻滯型態」仍會出圖；
   什麼都沒寫時寧可純文字。
   實際效果：報告只寫「符合急性心肌梗塞」時，庫裡 75 張帶定位的 MI 片全部不合格，退而顯示不宣稱定位的片
   （如 `non-specific ST elevation`）；只寫「心肌缺血」時退到 `non-specific ischemic`。

排序先看定位精確度，再看命中詞數，再看**報告把影像自己的名稱寫得多完整**
（臨床名稱會嵌套：「室上性心搏過速」是「陣發性室上性心搏過速」的子字串，兩張都會命中，
報告寫了較長那個就該給較具體的那張），最後才是人口學。

**對側性（reciprocal）變化不計入定位**：含「對側性／對應性／鏡像／reciprocal」的子句，其導程不計，
否則「aVL 呈對側性 ST 壓低」會把純下壁梗塞誤判成下側壁。

配套：`instruction_file/examiner_instruction_text.txt` 要求 examiner 在出現 ST-T 變化或病理性 Q 波時
**必須列出受影響導程並標示心肌部位**、對側性變化必須明講、傳導阻滯必須寫明完全性/不完全性、
影像檢查病灶必須寫明左右側——報告夠具體，這些閘才有東西可比對。

### 「畫過框」不等於「驗證過」

`BBox_List_2017.csv` 是一位放射科專科醫師 **針對已經存在的 NLP 探勘標籤**，在影像上標出該所見的位置。
那是 **localization（定位標註），不是 adjudication（對標籤正確性的獨立覆核）**：單一判讀者、
無第二人複核、判讀時已知標籤（錨定效應）、只涵蓋 14 個所見中的 8 個，而且資料集裡
**沒有「醫師認為標籤錯、拒絕畫框」的紀錄**——看不到分母。

因此 manifest 把兩件事拆開：

| 欄位 | 意思 |
|---|---|
| `verified` | **這張可不可以顯示**（顯示閘）|
| `label_verification` / `clinician_reviewed` | **誰為這個標籤背書**（`review_status()`）|

`review_status()` 回傳四種值，頁面依此決定警示強度：

| 值 | 意思 | UI |
|---|---|---|
| `clinician` | 本課程臨床教師實際看過並認可 | `st.warning`（只剩一般落差提示）|
| `cardiologist-validated` | PTB-XL：心臟科醫師標註＋`validated_by_human` | ⚠️ `st.warning` |
| `radiologist-bbox` | NIH：醫師畫過定位框（**非標籤驗證**）| 🟠 `st.warning` |
| `nlp-mined` | 僅由報告文字探勘，約 90% 準確，無人檢查 | 🔴 **`st.error`** |

`--publish` 可以在尚未覆核前就讓影像上線（以上面的警示為代價），但**不會**把它們標成已審閱，
也**不會**從待審清單上移除。

### 影像審閱（`tools/annotate_image_bank.py`）

有兩個欄位需要人：`verified`／`clinician_reviewed`（NIH 標籤是 NLP 探勘的，約 90% 準確）
與 `attributes.laterality`（沒有任何開放胸片資料集記錄左右側）。

```bash
python tools/annotate_image_bank.py --status                  # 還有多少要審
python tools/annotate_image_bank.py --export-csv review.csv   # 產出待審清單（缺口排前面）
#   逐張看圖，在 review.csv 填 verified 與 laterality（right/left/bilateral/none）
python tools/annotate_image_bank.py --apply-csv review.csv    # 寫回 manifest
python tools/annotate_image_bank.py --backfill                # 舊 manifest 就地升級，免重建
python tools/annotate_image_bank.py --recompute-attributes    # 詞彙表改過後重算屬性（保留審閱者填的側性）
python tools/annotate_image_bank.py --publish CXR             # 尚未覆核就先上線（頁面會出紅色警示）
```

在 CSV 裡把 `verified` 填成 `true` 等於「有人真的看過這張圖並認可」，工具會同時註記
`clinician_reviewed`，該筆才會從待審清單消失、頁面也才會拿掉標籤警示。

`laterality` 填 `none` 表示「已審閱，此所見無側性」（如心臟肥大），與留白的「尚未審閱」不同；
未審閱前側性閘沒有東西可比對，行為與現況相同，不會把整個影像庫藏起來。

審閱者在 CSV 寫 `verified=false`（**看過之後認定這張片不該顯示**）會記成 `review_verdict: "rejected"`，
並且**不會**標 `clinician_reviewed`（那代表「醫師為標籤背書」，退件不是背書）。後續 `--publish` 會跳過這些片、
`--status`／`--export-csv` 也不再把它們列回待審清單——否則退件與「還沒看」在 `verified` 欄位上長得一模一樣，
一次 `--publish` 就會把醫師退掉的片重新推給學生。

### 圖片下方的風險提示（`risk_notes()`）

上面所有閘門拿掉的是「會誤導」的影像；剩下的是「拿另一位真實病人的片當範例」本身就有的落差。
這些落差會**逐張計算後顯示在圖片下方**，而非一段通用免責聲明：

| 提示 | 何時出現 |
|---|---|
| 本圖是另一位真實病人的影像 | 每一張 |
| 標籤未經任何醫師確認（紅）／未經獨立覆核（橙）／心臟科醫師已複核 | 依 `review_status()`，見上節 |
| 左右側尚未標註，可能與報告相反 | 所見有側性意義且 `laterality` 未填 |
| 側別由「定位框＋AI 讀片一致」推得（83 張）／**僅由 AI 讀片**推得（8 張，並明說本圖沒有定位框）| 依 `attributes.laterality_source` 分別措辭，兩者都註明未經放射科醫師確認 |
| 心肌定位有重疊但不完全相同 | 影像與報告的 territory 交集非空但不相等 |
| 年齡/性別與本病人不同 | 性別不同，或年齡相差 ≥10 歲 |
| AP 片不可套 PA 的心胸比標準 | `view_position == "AP"` |
| 影像上可見的裝置／管線 | 讀片 sidecar 列出裝置時（`device_names()`：去重，且**側別標記、技師縮寫、去識別遮蔽框等「在片上不在病人身上」的東西不列**）|
| 🔴 三位讀片員無法確認本片確有此所見（引述仲裁者疑慮）| 三讀仲裁 `target.visible == "uncertain"`（`"no"` 的片根本不會被選中）|
| 讀片備註 | 仲裁者留下 `label_concern` 但所見看得到（例如「結節密度高、較可能為鈣化肉芽腫」）|
| 本片已有治療痕跡（胸管等），是治療後的追蹤影像 | 三讀仲裁 `treatment_evidence`（NIH 氣胸片幾乎都是）|
| 原始判讀與標註代碼有出入／分期欄位與判讀文字矛盾 | PTB-XL 翻譯核對 `report_consistent_with_codes == false`（31 張）；`stadium_zh` 為急性而判讀寫陳舊（記錄 4166）|
| 影像上可能還有別的所見 | 每一張（有 `co_findings` 就逐一列出並註明未經確認）|
| 嚴重程度與量測值不保證一致 | 每一張（依 modality 列出對應的量測項目）|

- 影像一律標示為**該所見的真實範例影像**（附其自身 ground-truth 所見＋來源＋授權），並註明
  「非本虛擬病人本人」，屬誠實的判讀練習，而非宣稱是這位虛擬病人的片子。
- 顯示前會**以當下的 manifest 重跑一次上述安全關卡**（`render_result`）：即使日後你在原地修改、
  重新標註某筆影像，或載入舊的存檔，也不會把報告配到已不相符的片子。
- App 在影像庫為空、或 manifest 指到的檔案不存在時，**自動降級為純文字**，不會出錯。

### 每張影像的完整標註（`annotation`）與「影像優先」的報告

只記一個 statement 的 manifest 夠**檢索**、不夠**寫報告**：PTB-XL 有 **71%（354/496）** 的心電圖
帶有心臟科醫師驗證過的其他 statement（一張「下壁梗塞」片同時標了前間壁梗塞與左前分支阻滯），
電軸、梗塞分期（急性／亞急性／陳舊性）、原始判讀文字都沒進來；胸片則沒有側別、沒有裝置（胸管、port-A、
中心靜脈導管）的記錄。`tools/enrich_image_bank.py` 把這些寫進每筆的 `annotation`：

| 來源 | 內容 | 可信度 |
|---|---|---|
| PTB-XL（程式化）| 全部 SCP statement＋likelihood＋種類、`heart_axis`、`infarction_stadium`、節律器、期外收縮、訊號品質、原始判讀 | 心臟科醫師標註 |
| 審閱 workflow（`image_bank/annotations/ecg/`）| 原始判讀的 zh-TW 翻譯，逐筆與 statement 核對 | LLM 翻譯＋核對 |
| 審閱 workflow（`image_bank/annotations/cxr/`）| 三位獨立視覺讀片員（知標籤者、盲讀者、仲裁者）：側別、肺野、大小、裝置、其他所見、是否適合初診案例、**標籤能否在片上看到**（`target.visible`）與**標籤疑慮**（`label_concern`） | AI 讀片，**非放射科醫師** |

其他驗證過的 statement 成為 `co_findings` 並標 `co_findings_validated: true`：檢索把它們視為影像**主張的一部分**
（定位聯集），排序偏好「報告沒提到的驗證所見最少」的片，風險提示會逐一列出。胸片的側別由定位框＋讀片員
一致時寫入 `attributes.laterality`（標 `laterality_source`，**不**標 `laterality_reviewed`），頁面會註明「未經放射科醫師確認」。

**三讀仲裁的結論會回饋到檢索與提示**（149 張胸片全部完成，`reviewed_by: ai-vision-3reader`）：
仲裁者對「標籤上的所見在這張片看得到嗎」的答案是 **105 張 yes、40 張 uncertain、4 張 no**。

- `visible == "no"`（4 張：兩張「肺氣腫」其實是 18 歲女性正常片與皮下氣腫、一張橫膈疝氣、一張引流後氣胸）→
  `find_image` **不再顯示**，等同 `verified:false`；manifest 仍保留供審閱工作表。
- `visible == "uncertain"`（40 張，多為 NLP 標籤的肺水腫／實變／纖維化／肋膜增厚與細微氣胸）→ 排序**落在所有
  看得清楚的片之後**（`_VISIBILITY_RANK`，緊接定位精確度、先於人口學），只在沒有更好的片時才出現，且圖下方
  以 🔴 引述仲裁者的疑慮全文。
- `label_concern` 非空但看得到（例如「結節密度高、較可能為良性鈣化肉芽腫」）→ 以「讀片備註」列出。
- `treatment_evidence`（胸管、引流後）→ 提示「這是治療後的追蹤影像，嚴重度會比報告輕」。
- 臨床教師的 `clinician_reviewed` 一律壓過 AI 讀片員（`target_visibility()` 回傳 yes）。

**寫死的報告（`Problem.檢查預期所見`）**：病例 JSON 可直接帶每項影像檢查的完整報告（examiner 的 markdown 格式，
結尾 `[ABNORMAL]`/`[NORMAL]`）。學生開立該檢查時 `page/examination.py` **逐字送出、不呼叫模型**，其餘檢查才走 LLM。
`problem_setter` 出題時也會產生這兩份報告（schema 已加欄位），所以隨機出題同樣確定性。

**影像優先綁定（`util/case_imaging.py`）**：出題完成後，系統先用固定報告挑出真實影像，再把該影像的完整標註交給
改寫步驟，改寫後的報告只有在「仍檢索到同一張、且影像上每一項驗證所見都已提到」時才採用，否則保留原文。
報告因此是**從影像寫出來的**，不是事後配對。

**心電圖影像標題不再印診斷**：第一版渲染把「IMI (inferior myocardial infarction)」印在圖上，等於把答案給學生。
`tools/rerender_ecg_images.py` 已全數重渲染為只含編號與刻度。

### 影像示範模板（`data/template_problem_set/影像示範 *.json`）

十份案例（ECG 5、CXR 5），每一份都**針對影像庫裡的一張特定影像撰寫**，由
`tools/make_image_bank_templates.py` 產生、`tests/test_image_bank_templates.py` 釘住：

| 檔名 | 目標影像 | 病人 | 影像實際所見 |
|---|---|---|---|
| ECG-1 陳舊性下壁心肌梗塞 | `ptbxl-IMI-4166` | 男 31 | 陳舊性下壁梗塞、電軸極度右偏、R 波遞增不良（原判讀「wahrscheinlich alt」）|
| ECG-2 無症狀前間壁心肌梗塞 | `ptbxl-ASMI-1440` | 女 48 | 前間壁梗塞、年代不明、電軸右偏 |
| ECG-3 心房顫動 | `ptbxl-AFIB-8215` | 女 71 | **心室速率正常**的心房顫動、電軸左偏（原判讀「normokard」）|
| ECG-4 完全性左束支傳導阻滯 | `ptbxl-CLBBB-2135` | 女 54 | CLBBB、電軸左偏；附胸片 `nih-cardiomegaly-00005827_000`：心影擴大（CTR≈0.55）、**肺野清晰**、左肺尖手術夾（病史已寫入）|
| ECG-5 完全性房室傳導阻滯 | `ptbxl-3AVB-959` | 女 88 | 心房 98／心室 40、不完全性右束支阻滯型態（原判讀即建議永久節律器）|
| CXR-1 惡性肋膜積水 | `nih-effusion-00013337_000` | 男 65 | 右側大量積水、氣管居中無偏移、右肺門旁圓形緻密影（腫塊或包裹性積水）；左前胸 port-A、導管走向無法追蹤（病史已寫入）|
| CXR-2 院內肺炎 | `nih-pneumonia-00007444_003` | 男 38 | 左中下肺野 7 公分斑片狀浸潤（淡，兩位讀片員曾誤指右側——右橫膈上抬的陷阱已寫進報告）；右頸**隧道式雙腔透析導管**（病人改為血液透析者，病史已寫入）|
| CXR-3 心臟衰竭 | `nih-cardiomegaly-00012288_000` | 女 65 | 心影球狀擴大（CTR≈0.6）、肺門血管充血、吸氣不足、右肋膈角被軟組織遮蔽（少量積水不排除），無管線 |
| CXR-4 肺癌 | `nih-mass-00022192_003` | 男 73 | 右上肺野 8×11 公分邊緣清楚腫塊、右氣管旁條紋增厚、鈣化肺門結節；中線三枚衣物暗扣影 |
| CXR-5 孤立性肺結節 | `nih-nodule-00012374_000` | 男 34 | 左上肺野 2.2 公分邊緣清楚結節；右下頸殘留針灸針斷片（病史已寫入）。原選 `nih-nodule-00002578_000` 經三讀仲裁判為「框內僅隱約 1 公分淡影、能見度不確定」而換片 |

**為什麼沒有「急性 STEMI」案例**：影像庫 41 張 MI 類心電圖沒有一張編有 ST 上升（STE_）statement，
心臟科醫師的原判讀寫的是「可能為陳舊性」「年代無法判定」——PTB-XL 的 MI 標籤是已建立的 Q 波梗塞型態。
第一版把它們當急性 STEMI 案例是錯配，已依影像實際所見改寫。

讓報告「必然」對到那張影像的三個機制：

1. **病人年齡／性別 = 影像的年齡／性別**，人口學 tiebreak 因此落在那一張，且可重現。
2. **`Problem.檢查預期所見`** 寫明報告必須出現的措辭（導程、心肌部位、左右側、肺野、完全性、大小），
   用的是檢索閘認得的詞彙；`examiner_instruction_text.txt` 規定這段措辭為準、不得改寫。
3. **`_template_meta`** 記錄目標 id 與預期報告，`page/config.py` 載入時會把它剝掉（不進任何 prompt），
   測試則用它證明「預期報告 + 病人人口學 → 正是那張影像」。

胸片側別依放射科醫師定位框座標推得（正面片影像左＝病人右），**並逐張開圖檢視** —— NIH 的氣胸片
幾乎都是插胸管後的追蹤片，因此沒有氣胸案例。影像上的管線（port-A、中心靜脈導管）都寫進病史，
影像與病例才不會互相矛盾。五份胸片報告的每一句都改依 `image_bank/annotations/cxr/<id>.json` 的
**三讀仲裁讀片**（`ai-vision-3reader`：知標籤讀片員、盲讀片員、仲裁者）措辭——心胸比、病灶大小、
縱膈位置、裝置與附帶所見都以讀片為準，報告不再寫影像上沒有的東西（例如第一版 ECG-4 胸片的
「Kerley B 線」與 CXR-1 的「縱膈左移」都已刪除）。這仍不是放射科醫師的臨床審閱，頁面上的標籤警示照常顯示。

## 快速開始：抓開放授權起始集（需網路，你來執行）

```bash
# 先預覽會抓到哪些（不下載）
python tools/fetch_image_bank.py --dry-run

# 實際下載（每個查詢最多 2 張；預設涵蓋 ECG/CXR/XR/CT/US）
python tools/fetch_image_bank.py --max 2

# 只抓某些 modality
python tools/fetch_image_bank.py --only ECG CXR
```

- 來源：**Wikimedia Commons**，只保留 Public Domain / CC0 / CC-BY / CC-BY-SA 授權檔
  （**明確排除 CC BY-NC / CC BY-ND 等不可再散布授權**），並自動把來源網址、授權、作者寫入 manifest。
- 自動抓取的項目一律標記 `"verified": false`，**在你確認前完全不會顯示給學生**。
- **啟用步驟**：打開 `image_bank/manifest.json`，逐張確認影像確實符合其 `findings` 標註，
  把該筆的 `"verified"` 改成 `true`（正常與異常影像皆須確認）。改完後重整 App，到檢查區開
  「Chest X-ray」「Electrocardiography」等即可看到影像；未確認的項目維持純文字。
  （若走下方「進階：本機資料集」路線，`ingest_local_images.py --verified` 會直接標為已確認。）

## 進階：以標註可靠的大型開放資料集擴充

Commons 起始集適合正常影像與少量示範；**異常影像要「標註可靠、規模夠」，建議用下列
開放資料集**，下載後用 `tools/ingest_local_images.py` 批次註冊。

### PTB-XL（心電圖，PhysioNet，**開放存取 CC BY 4.0，免帳號／免 DUA**）

PTB-XL 是**波形資料（WFDB），不是影像**，要先渲染成 12 導程 PNG。共有兩支工具：

**（A）`tools/build_ptbxl_bank.py` — 一次建立廣覆蓋、跨人口學分層的 ECG 影像庫（推薦）**

讀 `ptbxl_database.csv`＋`scp_statements.csv` → 對 **全部 71 個 SCP statements**（含 23 個診斷子類別）
在資料允許範圍內，於 **性別 × 年齡帶** 各 cell 各挑一張**人工複核**的最佳紀錄 → 渲染成臨床 12 導程 ECG
→ 直接寫入 `image_bank/manifest.json`（含 `age`/`sex`/`statement_code`/`diagnostic_subclass`）→
產出 **覆蓋報告** `image_bank/ptbxl_coverage.md`。可**只串流所需紀錄**，不必下載整包 1.7 GB。

```bash
pip install wfdb                       # 其餘（pandas/numpy/matplotlib）多半已裝
curl -L -o ptbxl_database.csv https://physionet.org/files/ptb-xl/1.0.3/ptbxl_database.csv
curl -L -o scp_statements.csv https://physionet.org/files/ptb-xl/1.0.3/scp_statements.csv
python tools/build_ptbxl_bank.py --db ptbxl_database.csv --scp scp_statements.csv --list-only   # 先看覆蓋（不下載/不渲染）
python tools/build_ptbxl_bank.py --db ptbxl_database.csv --scp scp_statements.csv               # 正式建（預設每 cell 1 張，約 496 張）
```

- 分層：預設性別(2) × 年齡帶(`--age-bands 0,19,36,51,66`) = **10 個 cell/statement**；`--per-cell` 調每 cell 張數。
  五個年齡帶依**心電圖生理轉折**切分，讓同一帶內的正常變異最小化：
  `0-18` 成長發育期（幼年型 T 波倒置、胸壁薄致電壓偏高；PTB-XL 僅 265 筆人工複核紀錄）／
  `19-35` 基線最穩定的黃金區間／`36-50` LVH 初步跡象浮現／
  `51-65` CAD 與缺血性 ST-T 盛行率顯著上升／`66+` AFib、束支阻滯、纖維化與多重病理疊加。
  （PTB-XL 依去識別化規範把 >89 歲記為 `age=300`，共 293 筆，會正確落在 `66+` 帶。）
- 稀有 statement（如 PMI、2AVB 全庫僅十餘筆）會標為 `partial/none`——這是 PTB-XL 資料本身的上限，非工具缺陷；
  覆蓋報告會誠實列出哪些覆蓋、哪些資料不足。
- 條目寫 `verified: true` 與 `label_verification: cardiologist-validated`（PTB-XL 為心臟科醫師標註且
  經人工複核）；異常影像仍須「報告文字提到該所見」才會顯示。
  想自行複核再啟用，加 `--unverified`（寫 `verified:false`，你確認後再改 `true`）。
- App 端會用病人的年齡/性別，在同一 finding 的多張候選中**優先挑人口學相近者**（僅作排序，先過安全關卡）。

**（B）`tools/render_ptbxl_ecg.py` — 快速產少量特定類別（NORM/AFIB/MI）**

只想先看幾張效果時用它：挑各類別標註品質最高者渲染，並印出 `ingest_local_images.py` 指令。

```bash
python tools/render_ptbxl_ecg.py --db ptbxl_database.csv --out ./ptbxl_png --classes NORM AFIB MI --per-class 4
```

兩支都可用 `--source-dir /path/to/ptb-xl` 改從本機解壓的資料集讀取（不串流）。

### NIH ChestX-ray14（胸部 X 光，NIH Clinical Center，**開放存取／免帳號**）

**`tools/build_nih_cxr_bank.py` — 一次建立廣覆蓋、跨人口學分層的胸部 X 光影像庫（推薦）**

與 ECG 建庫工具同一套邏輯（共用 `tools/bank_common.py`）：讀 `Data_Entry_2017.csv` →
對 **14 個 NIH 所見＋乾淨的 `No Finding` 正常片**，在 **性別 × 年齡帶** 各 cell 各挑一張最佳影像 →
從本機解壓的影像複製進 `image_bank/cxr/` → 寫入 `image_bank/manifest.json` →
產出覆蓋報告 `image_bank/nih_cxr_coverage.md`。

```bash
# 到 https://nihcc.app.box.com/v/ChestXray-NIHCC 下載（免登入，但沒有單檔 URL，無法串流）：
#   Data_Entry_2017.csv、BBox_List_2017.csv 兩個 CSV 放專案根目錄（已在 .gitignore）
#   images_001.tar.gz … images_012.tar.gz（約 42 GB）放專案外的目錄
#
# 影像用 tools/download_nih_cxr.py 抓，不要用 NIH 官方的 batch_download_zips.py：
# 官方腳本是單次 urlretrieve()，無 timeout / 無重試 / 無續傳，單包 2-4 GB 只要斷一次
# 就整包重來，而且會留下一個看起來正常的截斷檔。本工具用 HTTP Range 續傳＋自動重試，
# 中斷後重跑會從斷點接續（Box 對這些連結回 206 Partial Content，但 HEAD 回 404，
# 所以檔案大小是用 Range: bytes=0-0 的 Content-Range 取得）。
python tools/download_nih_cxr.py --dest /path/to/nih-cxr --extract --delete-archives
# 解壓後工具會遞迴搜尋整個目錄樹，不必攤平或分類

# 先看資料能覆蓋到什麼（只讀 CSV，不需要影像）
python tools/build_nih_cxr_bank.py --meta Data_Entry_2017.csv --bbox BBox_List_2017.csv --list-only

# 正式建（預設每 cell 1 張，最多 15 × 10 = 150 張）
python tools/build_nih_cxr_bank.py --meta Data_Entry_2017.csv --bbox BBox_List_2017.csv \
    --images-dir /path/to/nih-cxr
```

- **2020 修正版的欄名不同**：官方後來釋出的 `Data_Entry_2017_v2020.csv` 把性別欄從
  `Patient Gender` 改名為 `Patient Sex`。兩種都接受；**欄位缺失會直接報錯**，不會退化成
  「所有所見都是 0」的假覆蓋報告。
- **分層**：預設性別(2) × 年齡帶(`--age-bands 0,19,40,60,80`) = **10 個 cell/finding**。
  五個年齡帶依**胸部影像的判讀基準轉折**切分：
  `0-18` 胸廓仍在發育（心胸比與縱膈影標準和成人不同、胸腺可能仍可見）／
  `19-39` 成人基線、異常率最低／`40-59` 吸菸與職業暴露開始累積，結節/腫塊出現／
  `60-79` COPD、心衰竭、惡性腫瘤盛行率大幅上升／
  `80+` 脊椎後凸與攝影體位限制（常為 AP／臥姿）改變整張片的判讀基準。
- **每張片只帶「被選中的那一個所見」**：NIH 一張影像常有多個標籤，但其餘標籤同樣只是 NLP 探勘、
  未經確認，所以它們寫進 `co_findings` **僅供追溯，不會**成為顯示這張片的理由。
- **Cardiomegaly 只收 PA 片**：AP／臥姿攝影會放大心影，心胸比不能用 PA 的標準判讀。
- **同一個所見下，一位病人只出一張**，所以十個 cell 是十個不同的人，不是同一人的追蹤系列。
- **年齡欄有轉錄錯誤**（148–155、411–414），>120 歲一律視為資料錯誤剔除；這與 PTB-XL 的
  `age=300`（>89 歲的去識別化代碼，要併入最高帶）性質不同。
- **驗證預設與 ECG 相反**：`--verify bbox`（預設）只讓 `BBox_List_2017.csv` 裡放射科醫師畫過框的
  影像拿到 `verified:true`，其餘寫 `verified:false`。但請注意**畫框不等於驗證標籤**（見前面專節），
  所以兩者都會被 `review_status()` 標為未經覆核。`--verify all` 全部上線、`--verify none` 全部待審。

### Open-i / Indiana University CXR（胸部 X 光，多為 CC-BY）

- <https://openi.nlm.nih.gov/>。逐案授權不一，下載後**逐張確認授權**再 `--license` 標註。

> **MIMIC-CXR / CheXpert** 標註最佳但需 credentialing（資料使用協議）。取得授權後同樣用
> `ingest_local_images.py` 註冊；請遵守各自的 DUA，且注意這些通常**不可再散布**
> （只在本機影像庫使用，別 commit 進公開 repo）。

## manifest.json 格式

```jsonc
{
  "schema_version": 1,
  "entries": [
    {
      "id": "cxr-abnormal-ab12cd34",   // 唯一
      "file": "cxr/cxr-abnormal-ab12cd34.png", // 相對 image_bank/ 的路徑
      "modality": "CXR",               // ECG|CXR|XR|US|ECHO|CT|MRI|ENDO|NM
      "normality": "abnormal",         // normal | abnormal
      "findings": ["pneumonia"],       // 影像本身的 ground-truth 所見（caption 顯示的就是它）
      "umbrella_terms": [],            // 較廣的同義上位詞，只用來擴大召回
      "attributes": {                  // 互斥屬性；由建庫工具算出，laterality 由審閱者填
        "territory": [], "completeness": null, "lesion": null, "laterality": "right"
      },
      "disease_keywords": ["pneumonia"], // 適合對應到哪些病名
      "source": "NIH ChestX-ray14",
      "source_url": "https://...",
      "license": "…",
      "attribution": "…",
      "deidentified": true,
      "synthetic": false,               // 診斷影像務必 false
      "verified": true,                 // **必填為 true 才會顯示**（顯示閘，非「醫師確認過」）
      "label_verification": "nlp-mined", // 標籤來源，決定頁面警示強度
      "clinician_reviewed": false,      // 本課程教師是否實際看過並認可
      "caption": "PA chest radiograph showing right lower lobe consolidation"
    }
  ]
}
```

也可**手動**編輯 manifest 新增條目（把影像放到 `image_bank/<modality>/`、填好上述欄位即可）。

**各欄位的用法**：

- `verified`：**只有 `true` 的條目會顯示**。這是顯示閘——寧可漏顯示，也不顯示未經確認的片子。
  它**不代表**有醫師看過；那是 `label_verification` / `clinician_reviewed` 的事。
- 對**異常**影像，`findings` 要放**該片實際的所見詞**（如 `pneumonia`、`intracranial hemorrhage`）；
  App 會拿這些詞去「本次報告文字」裡找，找到才顯示。放太籠統（如只放病名分類）會顯示不出來。
- 對**多部位 modality 的正常**影像（US/XR/CT/MRI/ENDO/NM），請在 `findings` 或 `caption` 放入
  **與檢查項目名稱相符的部位詞**（例如腹部超音波放 `abdominal`），App 才能把它對到正確的開單部位；
  單一部位 modality（ECG/CXR/ECHO）則不需要。
- `umbrella_terms`（選填）：較廣的同義詞（如 `myocardial infarction`），**只擴大召回，不足以單獨成立**。
  舊版 manifest 把它和具體所見一起塞在 `findings`；檢索會相容處理（`findings[0]` 視為具體所見、其餘視為上位詞），
  用 `tools/annotate_image_bank.py --backfill` 可就地升級。
- `attributes`（選填）：`territory`／`completeness`／`lesion` 由建庫工具用
  `util.image_bank.claim_attributes()` 算出（與檢索用的是同一份詞彙，不可能對不上）；
  `laterality` **一律留 null**，由審閱者填入。詞彙表改過後用 `--recompute-attributes` 重算。
- `age`（整數）／`sex`（`M`/`F`）：**選填**。填了之後，同一 finding 有多張候選影像時，App 會**優先挑
  年齡/性別最接近本虛擬病人者**（純排序 tiebreak，發生在所有安全關卡之後，不會讓不安全的影像出現）。
  `build_ptbxl_bank.py` 會自動寫入這兩欄；手動或其他來源的條目沒填也無妨（不影響選圖安全性）。
- `label_verification`／`clinician_reviewed`（選填）：標籤由誰背書，決定頁面警示強度（見前面專節）。
- `statement_code`／`diagnostic_subclass`／`statement_category`（選填）：PTB-XL 建庫時記錄，供覆蓋報告與追溯用。
- `nih_label`／`co_findings`／`view_position`（選填）：NIH ChestX-ray14 建庫時記錄。
  `co_findings` 是**未經確認**的共存標籤，僅供追溯，**不會**被拿來當作顯示這張片子的理由。

## 設定與停用

- `PATIENZ_IMAGE_BANK_DIR`：影像庫目錄（預設 `image_bank`）。
- `PATIENZ_DISABLE_IMAGE_BANK=1`：完全停用影像顯示（回到純文字報告）。

## 版控與散布注意

- 預設 `.gitignore` **不追蹤影像二進位**。若你的來源授權允許再散布（PD/CC0/CC-BY/CC-BY-SA），
  可自行決定是否連同影像 commit；**credentialed 資料集（MIMIC/CheXpert）切勿 commit**。
- `manifest.json` 會追蹤；clone 後若無影像，App 會自動純文字降級，跑一次 fetcher 即可補齊。
