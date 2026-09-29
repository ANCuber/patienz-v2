This is the repository for Virtual Patient project for YTP 2024-2025.

## Quick Start

1. Clone the repository
2. Run `source init.sh`
3. Begin development

Note: remember to add your `GEMINI_API_KEY` to your environment variables.

(You can do this by adding `export GEMINI_API_KEY="<your_key>"` to your `.bashrc` or `.zshrc` file)

## Running and testing

- Run `./run.sh` (or `streamlit run home.py`) to start the application locally.
- Run the unit tests with `uv run --with pytest -m pytest -q` (or install
  `requirements-dev.txt` and run `pytest`). Tests use a fake `streamlit` and
  make no network calls.
- Lint with `uv run --with pyflakes pyflakes home.py page util model tools tests`.

Dependencies are split into three files:

- `requirements.txt` — what the app needs.
- `requirements-dev.txt` — adds pytest and pyflakes.
- `requirements-optional.txt` — selenium/googlesearch, only for `tools/get_pdf.py`.

See `CLAUDE.md` for a map of the codebase and its conventions.

## Database

The app uses SQLite only. Users, progress saves and grading results live in
`data/app.db` (override with `PATIENZ_DB_PATH`); session logs are sharded into
one file per month under `data/log_db/session_logs_YYYYMM.db`
(`PATIENZ_LOG_DB_DIR`). Every connection runs in WAL mode with a busy timeout
(`PATIENZ_DB_BUSY_TIMEOUT`, default 30 s), so many concurrent Streamlit
sessions can read and write at the same time. Keep the database on a local
disk: SQLite WAL does not work reliably on network file systems.

Back up by copying `data/app.db` together with `data/app.db-wal` and
`data/app.db-shm` (or run `sqlite3 data/app.db ".backup backup.db"`).

## Users and authentication

The database is the source of truth for users. On a new database the service
creates one admin account named `PATIENZ_ADMIN_USERNAME` (default `admin`).
Its password is `PATIENZ_ADMIN_PASSWORD` if that is set; otherwise a random
password is generated and printed once in the server log at first start. There
is no built-in default password. Change it from the admin page after the first
login. Both variables are read only when the database is empty.

- Self-registration (`註冊帳號` tab) is on by default; set
  `PATIENZ_ALLOW_REGISTRATION=0` to make the admin page the only way to create accounts.
- Logins are restored across page reloads with a signed token in the URL
  (`PATIENZ_AUTH_TOKEN_TTL`, default 30 days). Logging out, changing the
  password, deactivating or deleting a user invalidates that user's tokens.
- The signing secret is `PATIENZ_AUTH_SECRET`; if unset, a random one is
  generated once and stored in `data/.auth_secret`. Keep it out of git and
  back it up with the database, otherwise everyone is logged out after a move.
- After `PATIENZ_MAX_FAILED_LOGINS` (default 5) wrong passwords an account is
  locked for `PATIENZ_LOGIN_LOCKOUT` seconds (default 15 minutes).
- Grading runs at most `PATIENZ_MAX_CONCURRENT_GRADING` (default 2) sessions at
  once; others wait up to `PATIENZ_GRADING_QUEUE_TIMEOUT` seconds. Raise the
  limit if your Gemini quota allows.
