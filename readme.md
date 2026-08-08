This is the repository for Virtual Patient project for YTP 2024-2025.

## Quick Start

1. Clone the repository
2. Run `source init.sh`
3. Begin development

Note: remember to add your `GEMINI_API_KEY` to your environment variables.

(You can do this by adding `export GEMINI_API_KEY="<your_key>"` to your `.bashrc` or `.zshrc` file)

Optional environment variables:

- `PATIENZ_DISABLE_CONTEXT_CACHE=1` — 停用 Gemini 顯式 context caching（預設啟用；任何快取失敗都會自動退回未快取路徑，功能不受影響）
- `PATIENZ_CACHE_TTL_SECONDS` — context cache 存活時間（預設 7200 秒）
- `PATIENZ_IMAGE_BANK_DIR` — 真實影像庫目錄（預設 `image_bank`）；`PATIENZ_DISABLE_IMAGE_BANK=1` 完全停用影像顯示

### 真實影像庫（檢查區 ECG／X 光…附真實去識別化影像練習判讀）

影像二進位不進版控，需你在本機填充：`python tools/fetch_image_bank.py`（開放授權起始集）或以
`tools/ingest_local_images.py` 匯入 PTB-XL / NIH ChestX-ray14 等資料集。影像**必須人工確認**
（manifest 內 `"verified": true`）才會顯示；診斷影像一律用**真實影像、非 AI 生成**。安全設計、
啟用步驟與資料集下載指引見 `docs/image_bank.md`。

## Testing the application

- Run `streamlit run home.py` to start the application (local)
