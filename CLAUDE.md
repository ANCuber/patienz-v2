# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Patienz-v2** is a medical education simulation platform for YTP 2024-2025. Medical students practice clinical diagnosis by interacting with an AI-powered virtual patient through a 7-phase OSCE-style workflow, then receive dual grading (OSCE mark scheme + ACGME core competencies).

**Stack**: Python 3, Streamlit (multi-page app), **google-genai SDK** (Gemini 2.5 Flash / Flash-Lite; the legacy `google-generativeai` package is no longer used), Google Speech-to-Text, Altair (charts), pandas. Selenium/googlesearch are now optional (lazy-imported by the offline `getPDF` tool only).

All Gemini access goes through `util/llm.py` (single client, `build_config()` with **thinking-budget control**, `start_chat()`, and `ModelHandle` for the legacy `model.start_chat()` shape). Interactive/examiner paths run with `thinking_budget=0`; graders use a small budget.

The four interactive chats (patient + value/text/PE examiners) serve their fixed instruction+case prefix from an **explicit Gemini context cache** via `util/context_cache.py` (`start_cached_chat()`): cache-creation failure falls back to the plain path, a mid-session cache expiry self-heals by rebuilding the chat uncached (history preserved) and retrying once. Opt out with `PATIENZ_DISABLE_CONTEXT_CACHE=1`; TTL via `PATIENZ_CACHE_TTL_SECONDS` (default 7200).

Imaging exam reports (ECG/CXR/CT/MRI/US…) can be paired with a **real de-identified image** for read-practice via `util/image_bank.py` (`find_image()`), catalogued by `image_bank/manifest.json` (image binaries are git-ignored, populated locally by `tools/fetch_image_bank.py` / `tools/ingest_local_images.py`). Diagnostic images are **real, never AI-generated**. Retrieval never shows a possibly-misleading image — an image displays only if it is human-`verified`, modality-matched, and (abnormal) its own `findings` are evidenced in *that exam's report text*, or (normal, multi-anatomy) its region matches the order; otherwise it returns `None` and the page shows text only, re-validating the gate at render time. Opt out with `PATIENZ_DISABLE_IMAGE_BANK=1`; dir via `PATIENZ_IMAGE_BANK_DIR`. See `docs/image_bank.md`.

## Setup & Running

```bash
# Initial setup (creates venv, installs dependencies)
source init.sh

# Set required environment variable
export GEMINI_API_KEY="<your_gemini_api_key>"

# Run the app
streamlit run home.py
# or
./run.sh
```

Tests (mock-based, no API key/network needed): `python -m pytest tests/ -q`.

## Architecture

### 7-Phase User Flow
```
Config (0) → Test/問診 (1) → Physical Exam (2) → Pre-DDx/初步鑑別 (3) → Examination (4) → Diagnosis (5) → Grade (6)
```
- Pages are gated by `util.tools.init(page_id)` + `check_progress()` — students cannot skip phases.
- `next_page()` in `util/tools.py` advances `ss.current_progress` and `st.switch_page`s.
- The sidebar (`util.tools.note()`) shows progress, allows backtracking to completed phases, exposes a notes textarea, and offers manual progress save.

### Layer Structure

**Pages** (`page/`): One Streamlit page per phase. Each calls `util.tools.init(page_id)` on load and `util.tools.next_page()` to advance.
- `config.py` — Patient case setup; supports random / specified disease / manual upload, builds prompt for `problem_setter`
- `test.py` — Interview chat with the virtual patient (text + speech-to-text)
- `physical_exam.py` — Body-system PE driven by `examiner` in PE mode (`pe_instruction.txt`)
- `pre_ddx.py` — Student locks a pre-DDx list (name / supporting reasons / likelihood); used downstream as scoring anchor
- `examination.py` — Sequential lab/imaging orders; each entry tracked in `ss.examination_history` with `target_ddx`, `interpretation`, and per-entry `ai_feedback` from `lab_advisor`
- `diagnosis.py` — Final diagnosis, kept/excluded pre-DDx items (`final_ddx_status`), comorbidities, treatment
- `grade.py` — Runs the full grading pipeline (see Grading Pipeline below), renders dual dashboards, hosts `advisor` Q&A

**AI Agents** (`model/`): Each agent wraps a Gemini call with a system instruction; most load from `instruction_file/*.txt`.
- `patient.py` — Virtual patient responses, primed with the case JSON + optional knowledge-base PDFs
- `examiner.py` — Returns structured JSON for labs (value mode), imaging/EKG (text mode), or physical exam findings
- `problem_setter.py` — Generates the full case JSON (demographics, MH, FH, SH, Problem block) from the config prompt
- `mark_scheme_setter.py` — Dynamically generates an OSCE mark scheme tailored to the case (categories, item_id, max_score, scoring_guide)
- `grader_v2.py` — Scores the student against the generated mark scheme (returns score/feedback per item)
- `acgme_grader.py` — Rates ACGME sub-competencies (Level 1–5) using a milestone JSON loaded by `acgme_selector`; respects learner role (PGY-1 default) and excludes OSCE-inapplicable competencies
- `lab_advisor.py` — Short (<80 字) per-examination feedback shown after each lab order; uses `gemini-2.5-flash-lite`
- `advisor.py` — Post-grading conversational mentor; primed with ACGME results on first user question

**Utilities** (`util/`):
- `tools.py` — Session init (`init_all()`, `init(page_id)`), sidebar `note()` with progress nav + save button, `show_patient_profile()`, legacy `getPDF()` (Selenium scrape — fallback path only)
- `constants.py` — All UI text, page names, icons, intro text (Traditional Chinese)
- `chat.py` — Chat message formatting / rendering helpers (`append`, `update`)
- `process.py` — Audio → text via Google Speech Recognition (zh-TW)
- `dialog.py` — Streamlit modal dialogs (intro per phase, errors, config saved, refresh)
- `save_load.py` — JSON-based session save/restore (`SAVE_DIR=data/save`) + auto-archive of grading results (`GRADING_DIR=data/grading_results`); pads stage-indexed arrays for backward compatibility with pre-`pre_ddx` saves
- `acgme_selector.py` — Disease/symptom → milestone JSON resolver with fallback to `internal_medicine`; filters OSCE-inapplicable sub-competencies (Digital Health, Interprofessional Team, Reflective Practice, Wellness, Physician Role in Systems) via `applicable_in_osce` flag or `name_en` heuristic
- `acgme_aggregator.py` — Aggregates per-sub-competency Levels to 6-domain summary (PC / MK / PBLI / ICS / PROF / SBP); marks `insufficient_data` when `assessed_count < 2`
- `reference_parser.py` — Parses lab reference ranges (`parse_reference`, `is_abnormal`, `is_critical`); supports sex-specific ranges

**Data**:
- `instruction_file/` — System prompts for each agent (`patient`, `examiner_text`, `examiner_val`, `pe`, `problem_setter`, `mark_scheme_setter`, `grader_v2`, `acgme_grader`, `advisor`)
- `examination_file/` — `pe_choice.json` (PE body systems UI), `examination_choice.json` (lab/imaging UI), `examination.csv` (test items + reference ranges + units), `config_options.json` (case-config form options)
- `config/acgme_milestones/` — 15 specialty milestone JSONs + `_disease_mapping.json` (disease/symptom → milestone routing)
- `knoledge_base/` — Authoritative PDFs (Harrison's, CMDT 2026, Talley & O'Connor, Oxford Handbook, OSCE Cases…) uploaded to Gemini for grounding
- `data/template_problem_set/` — Pre-made patient cases (JSON)
- `data/problem_set/` — User-generated cases (saved from grade.py "儲存本次病患設定")
- `data/save/` — In-progress session snapshots (manual save from sidebar)
- `data/grading_results/` — Auto-archived grading output per session (`{SID}_{name}_{disease}.json`); overwrites on re-grade
- `data/log/{SID}.txt` — Per-session conversation + performance log

### Session State Pattern
```python
ss = st.session_state          # used throughout
init_all()                     # creates SID, initializes all ss keys (called once from home.py)
init(page_id)                  # per-page guard; redirects to config if first_entry[0] still True
```
Key state arrays are indexed by `page_id` (length = `len(const.section_name)` = 7). `save_load.load_progress()` pads `first_entry` / `start_time` arrays so older saves (6-phase) still load.

### Grading Pipeline (page/grade.py)
Triggered on first entry; each step is cached in `ss` to avoid re-runs:

1. **Mark scheme generation** — `mark_scheme_setter` produces a case-specific OSCE rubric → `ss.mark_scheme_raw`
2. **V2 grading** — `grader_v2` scores student data (interview + PE + pre-DDx + exam history + final DDx + treatment) against that mark scheme → `ss.grader_v2_response`
3. **Advisor priming** — `advisor` model created, ready for Q&A
4. **ACGME assessment**:
   - `acgme_selector.select_milestone(disease, symptoms)` chooses a specialty milestone (disease → symptom → default fallback) and filters OSCE-inapplicable sub-competencies
   - `acgme_grader` rates each remaining sub-competency at Level 1–5, with `level_rationale`, `evidence`, `improvement`
   - `acgme_aggregator` reconciles missing entries and aggregates to 6-domain averages
5. **Auto-save** — `save_load.save_grading_result()` writes `data/grading_results/{SID}_…json` once both graders finish (or ACGME errored)
6. **Rendering** — V2 dashboard (total score + per-category tabs) + ACGME dashboard (radar/bar + per-domain tabs with sub-competency Level breakdowns) + Advisor chat (ACGME results injected as system context on first message)

`reset_grading()` clears all cached grading state to allow re-run after a student backtracks and edits earlier phases.

### AI Structured Output
Agents use Gemini's `response_schema` (via `google.genai.types.Schema` / `types.Type`, re-exported as `llm.Schema` / `llm.Type`) to enforce JSON shapes:
- `mark_scheme_setter` / `grader_v2` → ARRAY of `{category, item_id, description, max_score, score?, feedback?, scoring_guide?}`
- `acgme_grader` → ARRAY of `{subcompetency_id, subcompetency_name, domain, level, level_rationale, evidence, improvement}`
- `examiner` (val mode) → `{value_type_item: [...]}` matched against `examination.csv` for unit + reference range

### Knowledge-Base Context for AI
Patient/examiner/advisor agents are grounded on the **structured case JSON** (the single source of truth for the current patient), embedded in each agent's system instruction. The previous *runtime* Selenium scrape of UpToDate ("clinical features" → PDF → Gemini Files) was removed: it was the dominant first-turn latency ("爬蟲時間長"), failed on restricted networks ("公用網路無法使用"), and pushed the patient toward textbook over-disclosure. The legacy `getPDF()` in `tools.py` remains as an opt-in offline tool (selenium/googlesearch are lazy-imported inside it).

## Key Conventions

- UI is entirely in **Traditional Chinese (zh-TW)**; all `constants.py` strings are Chinese
- Page indices are integers 0–6; `ss.current_progress` tracks furthest unlocked phase, `ss.page_id` the currently viewed phase
- Each session gets a unique `SID` (timestamp-based `YYYYMMDDHHMMSS`) used for log and grading-result filenames
- Logging format: arbitrary text appended to `data/log/{SID}.txt`; performance markers use `[PERF] step=X.XXs`; ACGME markers use `[ACGME] …`
- Gemini models: `gemini-2.5-flash` for graders/examiners/patient; `gemini-2.5-flash-lite` for `lab_advisor` (high-throughput, short outputs)
- ACGME 6 domains: `PC` 病人照護 / `MK` 醫學知識 / `PBLI` 從工作中學習 / `ICS` 人際與溝通 / `PROF` 專業素養 / `SBP` 制度下臨床
- Level color scheme (used in `grade.py`): L1 gray → L2 orange → L3 blue → L4 green → L5 purple
- `*.tmp.*` files in `model/` and `util/` are editor swap files — do **not** edit; they should be cleaned up periodically

## Tools

- `tools/extract_milestone_pdf.py` — Pulls milestone text from ACGME PDFs into the `config/acgme_milestones/*.json` shape
- `tools/flag_osce_applicability.py` — Sets the `applicable_in_osce` flag on each sub-competency; keep in sync with `acgme_selector._is_osce_inapplicable_by_name`
- `tools/verify_selector.py` — Smoke-tests `acgme_selector.select_milestone` across representative diseases/symptoms

## Reference Docs

- `readme.md` — Feature overview (Chinese)
- `docs/pe_examiner_architecture.md` — PE examiner agent design notes
- `ACGME_對應審查表.md` — ACGME mapping audit
- `reference_system_materials.md` — Index of system instructions, knowledge-base PDFs, examination items
