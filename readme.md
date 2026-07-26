This is the repository for Virtual Patient project for YTP 2024-2025.

## Quick Start

1. Clone the repository
2. Run `source init.sh`
3. Begin development

Note: remember to add your `GEMINI_API_KEY` to your environment variables.

(You can do this by adding `export GEMINI_API_KEY="<your_key>"` to your `.bashrc` or `.zshrc` file)

## Testing the application

- Run `streamlit run home.py` to start the application (local)

## Database configuration (Phase A)

By default, the app uses SQLite at `data/app.db`.

To run with PostgreSQL, set `PATIENZ_DB_URL`:

```bash
export PATIENZ_DB_URL="postgresql://<user>:<password>@<host>:<port>/<database>"
```

Notes:

- If `PATIENZ_DB_URL` is set to a PostgreSQL URL, the app stores auth, progress, grading, and logs in PostgreSQL.
- If `PATIENZ_DB_URL` is not set, the app falls back to SQLite.
- Log data is maintained in monthly shards in both backends:
	- SQLite: separate files under `data/log_db/session_logs_YYYYMM.db`
	- PostgreSQL: separate tables named `session_logs_YYYYMM`
- Install dependencies with `source init.sh` (includes `psycopg[binary]`).

## Users configuration

The service can bootstrap users from `config/users.json`.

- On startup, users in that file are synced into the database.
- If the file is missing or invalid, default admin bootstrap still applies.
