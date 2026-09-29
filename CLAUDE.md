# PaTiENZ (patienz-v2)

Streamlit virtual-patient simulator for medical students. Gemini (google-genai)
plays the patient, examiners, advisors and graders. UI text is Traditional
Chinese; code and comments are English.

## Run / test

```bash
source init.sh                      # uv venv + deps, then starts the app
./run.sh                            # streamlit run home.py on port 4090
uv run --with pytest -m pytest -q   # unit tests (no network, fake streamlit)
uv run --with pyflakes pyflakes home.py page util model tools tests
```

`GEMINI_API_KEY` must be set. See README for DB / admin env vars.

## Layout

| Path | Role |
|---|---|
| `home.py` | Entry point: init session, login gate, builds `st.navigation` from `util/constants.py`. |
| `page/*.py` | One Streamlit page per stage, in the order of `const.section_name`: config → history → physical_exam → pre_ddx → examination → diagnosis → grade. `admin.py` is admin-only. |
| `model/*.py` | One factory per LLM role (patient, examiner, advisor, lab_advisor, problem_setter, mark_scheme_setter, grader_v2, acgme_grader). Each loads its prompt from `instruction_file/`. |
| `util/llm.py` | Gemini client wrapper (`RetryChat`, model fallback chain, `DEFAULT_GEMINI_MODEL`). All model calls go through here. |
| `util/tools.py` | Session bootstrap (`init_all`, `init`), sidebar (`note`), timers, `record` (log line to file + DB). |
| `util/chat.py` | Chat transcript rendering plus the shared `send_to_patient` / voice-input helpers used by the history and diagnosis pages. |
| `util/auth.py` | Users, password hashing, revocable URL token login, login throttling, `safe_user_segment` (per-user folder names). |
| `util/db_store.py` | SQLite persistence (WAL, busy timeout, one short-lived connection per call): users, progress saves, grading results, monthly log shards. `init_db()` is idempotent and thread-safe. |
| `util/save_load.py` | Progress save/load (DB write-through with JSON file fallback under `data/save/<user>/`). |
| `util/acgme_*.py`, `config/acgme_milestones/` | ACGME milestone selection and aggregation for grading. |
| `examination_file/` | Static exam catalogues and UI option lists (JSON/CSV). |
| `instruction_file/` | System prompts, one per model role. |
| `tools/` | Standalone scripts (PDF extraction, log import, offline web-to-PDF). Never imported by the app. |
| `tests/` | pytest suite; `conftest.py` installs a fake `streamlit` module. |
| `data/` | Runtime data. Only `problem_set/`, `template_problem_set/` and `problem_setter_example/` are tracked; logs, saves, DBs and caches are gitignored. |

## Conventions

- Pages are scripts, not functions: they run top to bottom on every rerun and
  must start with `util.init(<page_id>)` then `util.note()`.
- Page order and file names are defined once in `util/constants.py`
  (`section_name`); navigate with `st.switch_page(f"page/{const.section_name[i]}.py")`.
- Persist new session-state keys by adding them to `SAVE_KEYS` in `util/save_load.py`.
- Log anything grading-relevant with `util.record(ss.log, ...)`; it goes to the
  per-user log file and the DB shard.
- Per-user directory names must come from `auth.safe_user_segment`.
- Every Streamlit session is a thread in one process: never share mutable module
  state without a lock, never touch `st.*` or session state from worker threads,
  and go through `db_store` for all persistence (it handles locking and retries).
- SQLite only. Do not reintroduce a second backend; there is no ORM and no
  migration tool, schema changes go in `db_store._init_main_schema` guarded by
  `_ensure_column` / `_ensure_unique_index`.
- Keep heavy optional deps (selenium etc.) out of `requirements.txt`; they live in
  `requirements-optional.txt` and are imported lazily by `tools/` scripts only.
- Add new dependencies to `requirements.txt` and dev-only ones to `requirements-dev.txt`.
