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

Users can register their own accounts from the `註冊帳號` tab on the login screen.
Self-registered accounts are stored in the configured database and are created as
regular `user` accounts.

The database is the source of truth for users. On a new database, the service
creates the admin account from `PATIENZ_ADMIN_USERNAME` and
`PATIENZ_ADMIN_PASSWORD` (default: `admin` / `admin123`). Manage all other
accounts from the admin page; `config/users.json` is no longer read at startup.
