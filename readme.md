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

## Testing the application

- Run `streamlit run home.py` to start the application (local)
