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

The app uses SQLite only, with one database per group:

| File | Contents |
|---|---|
| `data/app.db` (`PATIENZ_DB_PATH`) | System database: `users`, `user_groups`, `app_meta`. No user data. |
| `data/groups/g<group_id>/data.db` | That group's progress saves and grading results. |
| `data/groups/g<group_id>/log_db/session_logs_YYYYMM.db` | That group's session logs, one file per month. |
| `data/groups/root/...` | The root admin's own saves, results and logs. |

`PATIENZ_GROUP_DB_DIR` moves the `groups` directory (default: next to the
system database). Every data function in `util/db_store.py` takes the group id
it works on and opens only that group's files. Every connection runs in WAL mode with a busy timeout
(`PATIENZ_DB_BUSY_TIMEOUT`, default 30 s), so many concurrent Streamlit
sessions can read and write at the same time. Keep the database on a local
disk: SQLite WAL does not work reliably on network file systems.

Back up `data/app.db` and the whole `data/groups/` directory. Copy each `.db`
together with its `-wal` and `-shm` files, or use
`sqlite3 <file> ".backup backup.db"`.

**Upgrading from the single-database layout** happens automatically on the
first start: the existing admin becomes the root admin, every other account
moves to the `default` group, saves and grading results are copied from
`data/app.db` into the group databases (the old tables stay as
`*_pre_groups`), and rows from the old shared log shards in `data/log_db/`
(`PATIENZ_LOG_DB_DIR`) are copied into the owning group's log shards (the old
files are left untouched).

**Moving to PostgreSQL later.** The SQL is kept to what PostgreSQL also
accepts (`ON CONFLICT` upserts, ISO-8601 text timestamps, 0/1 integers), and
user and group ids are globally unique because they come from the one system
database. So the group databases can be loaded either into one schema per
group, or into shared tables with an added `group_id` column plus row-level
security. The SQLite-specific parts are all in `util/db_store.py`: `_connect`
(PRAGMAs), `?` placeholders, `AUTOINCREMENT` in the DDL and the `PRAGMA`
helpers used for schema checks.

## Users and authentication

There are three roles (policy in `util/access.py`):

| Role | Scope |
|---|---|
| `root` | Exactly one, in no group. Creates, renames and deletes groups; creates and edits every account, including appointing group admins and moving users between groups (their data moves with them); reads and deletes data in every group database. Cannot be deleted, deactivated or demoted. |
| `group_admin` | At most one per group. Creates, edits and deletes the ordinary users of its own group and reads and deletes that group's data. Cannot see or change anything in another group, other admins, or roles and group membership. |
| `user` | Its own saves, grading results and logs only. |

A group and its admin are created separately: add the group on the admin
page's 群組 tab, then create (or promote) its admin on the 使用者 tab. Only
empty groups can be deleted, and the `default` group never; a deleted group's
database directory is renamed to `g<id>.deleted-<timestamp>`, not removed.

The database is the source of truth for users. On a new database the service
creates the root admin, named `PATIENZ_ADMIN_USERNAME` (default `admin`).
Its password is `PATIENZ_ADMIN_PASSWORD` if that is set; otherwise a random
password is generated and printed once in the server log at first start. There
is no built-in default password. Change it from the admin page after the first
login. Both variables are read only when the database is empty.

- Self-registration (`註冊帳號` tab) is on by default; set
  `PATIENZ_ALLOW_REGISTRATION=0` to make the admin page the only way to create accounts.
  Self-registered accounts are ordinary users in the `default` group.
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
