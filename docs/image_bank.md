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
  - **人工審核（`verified: true`）**：只顯示經人確認的影像。自動抓取的項目一律 `verified:false`，
    在你逐張確認並把該項改成 `true` 之前，對學生**完全不顯示**——一張被誤標的 Commons 圖不可能外洩。
  - **modality 硬性比對**：開心電圖不會回傳 X 光。
  - **異常影像**：該片**自身的 ground-truth 所見必須「出現在本次報告文字」中**（比對片子的
    `findings`，而非本案的主診斷）。所以「缺血性中風」的報告不會叫出「腦出血」片；ECG 報「心房顫動」
    不會叫出「STEMI」片。用 `util/grading_normalize.surface_forms` 做中英/同義的「受控詞彙→敘述」比對，
    只把片子自己的所見詞去報告裡找，不會把兩個不相干的概念用子字串硬湊在一起。
  - **正常影像**：單一部位的 modality（ECG、CXR、ECHO）直接視為安全示範；多部位的 modality
    （US、XR、CT、MRI、ENDO、NM）**必須與本次開單的部位相符**，所以「正常腹部超音波」不會被拿去
    配「頸動脈超音波」的開單。
  - 報告未附 `[NORMAL]`/`[ABNORMAL]` 標記時（正異常未知），**不顯示影像**（`page/examination.py`
    的 `normality_known` 關卡）——寧可純文字，也不猜。
- 影像一律標示為**該所見的真實範例影像**（附其自身 ground-truth 所見＋來源＋授權），並註明
  「非本虛擬病人本人」，屬誠實的判讀練習，而非宣稱是這位虛擬病人的片子。
- 顯示前會**以當下的 manifest 重跑一次上述安全關卡**（`render_result`）：即使日後你在原地修改、
  重新標註某筆影像，或載入舊的存檔，也不會把報告配到已不相符的片子。
- App 在影像庫為空、或 manifest 指到的檔案不存在時，**自動降級為純文字**，不會出錯。

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

PTB-XL 是**波形資料（WFDB），不是影像**，要先渲染成 12 導程 PNG。已提供
`tools/render_ptbxl_ecg.py` 自動處理：讀 `ptbxl_database.csv` → 挑選各類別**標註品質最高**
（優先人工驗證的 fold 9/10、高信心 likelihood）的紀錄 → 渲染成臨床 3×4＋節律條的 ECG
（25 mm/s、10 mm/mV）→ 印出對應的 `ingest_local_images.py` 指令。可**只串流所需的少數幾筆**，
不必下載整包 1.7 GB。

```bash
pip install wfdb                       # 其餘（pandas/numpy/matplotlib）多半已裝
# 只抓標註 CSV（開放存取，免登入）
curl -L -o ptbxl_database.csv https://physionet.org/files/ptb-xl/1.0.3/ptbxl_database.csv
# 各類別渲染 4 張（直接向 PhysioNet 串流所選紀錄）
python tools/render_ptbxl_ecg.py --db ptbxl_database.csv \
    --out ./ptbxl_png --classes NORM AFIB MI --per-class 4
```

渲染完先**逐張看過** `./ptbxl_png/<CLASS>/*.png`（這就是「人工確認」那一步），再執行工具**印出的**
`ingest_local_images.py` 指令（已帶 `--verified`，會複製進 `image_bank/ecg/` 並寫入 manifest）。
若你已把整包資料集解壓在本機，改用 `--source-dir /path/to/ptb-xl` 從本機讀取即可。

### NIH ChestX-ray14（胸部 X 光，開放）

1. 下載：<https://nihcc.app.box.com/v/ChestXray-NIHCC>（NIH 釋出，可自由使用；請確認你的使用情境）。
2. 依 `Data_Entry_2017.csv` 的 `Finding Labels` 把影像分類到各資料夾（如 `Pneumonia`、
   `Pneumothorax`、`Effusion`、`No Finding`）。
3. 註冊，例如肺炎：
   ```bash
   python tools/ingest_local_images.py ./nih/Pneumonia \
     --modality CXR --normality abnormal \
     --findings pneumonia consolidation --disease-keywords pneumonia \
     --source "NIH ChestX-ray14" \
     --source-url https://nihcc.app.box.com/v/ChestXray-NIHCC \
     --license "NIH open access" --attribution "NIH Clinical Center" --verified
   ```

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
      "findings": ["pneumonia"],       // 影像本身的 ground-truth 所見
      "disease_keywords": ["pneumonia"], // 適合對應到哪些病名
      "source": "NIH ChestX-ray14",
      "source_url": "https://...",
      "license": "…",
      "attribution": "…",
      "deidentified": true,
      "synthetic": false,               // 診斷影像務必 false
      "verified": true,                 // **必填為 true 才會顯示**；人工確認過所見
      "caption": "PA chest radiograph showing right lower lobe consolidation"
    }
  ]
}
```

也可**手動**編輯 manifest 新增條目（把影像放到 `image_bank/<modality>/`、填好上述欄位即可）。

**兩個關鍵欄位的用法**：

- `verified`：**只有 `true` 的條目會顯示**。這是最後一道防線——寧可漏顯示，也不顯示未經確認的片子。
- 對**異常**影像，`findings` 要放**該片實際的所見詞**（如 `pneumonia`、`intracranial hemorrhage`）；
  App 會拿這些詞去「本次報告文字」裡找，找到才顯示。放太籠統（如只放病名分類）會顯示不出來。
- 對**多部位 modality 的正常**影像（US/XR/CT/MRI/ENDO/NM），請在 `findings` 或 `caption` 放入
  **與檢查項目名稱相符的部位詞**（例如腹部超音波放 `abdominal`），App 才能把它對到正確的開單部位；
  單一部位 modality（ECG/CXR/ECHO）則不需要。

## 設定與停用

- `PATIENZ_IMAGE_BANK_DIR`：影像庫目錄（預設 `image_bank`）。
- `PATIENZ_DISABLE_IMAGE_BANK=1`：完全停用影像顯示（回到純文字報告）。

## 版控與散布注意

- 預設 `.gitignore` **不追蹤影像二進位**。若你的來源授權允許再散布（PD/CC0/CC-BY/CC-BY-SA），
  可自行決定是否連同影像 commit；**credentialed 資料集（MIMIC/CheXpert）切勿 commit**。
- `manifest.json` 會追蹤；clone 後若無影像，App 會自動純文字降級，跑一次 fetcher 即可補齊。
