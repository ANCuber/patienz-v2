# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Patienz-v2** is a medical education simulation platform for YTP 2024-2025. Medical students practice clinical diagnosis by interacting with an AI-powered virtual patient through a 6-phase workflow.

**Stack**: Python 3, Streamlit (multi-page app), Google Generative AI (Gemini 2.5 Flash), Selenium, Google Speech-to-Text.

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

No test suite exists in this project.

## Architecture

### 6-Phase User Flow
```
Config (0) → Test/Interview (1) → Physical Exam (2) → Examination (3) → Diagnosis (4) → Grade (5)
```
Pages are gated — students cannot skip phases. `check_progress()` enforces sequential navigation.

### Layer Structure

**Pages** (`page/`): One Streamlit page per phase. Each calls `util.tools.init(page_id)` on load and `util.tools.next_page()` to advance.

**AI Agents** (`model/`): Each agent wraps a Gemini call with a system instruction loaded from `instruction_file/*.txt`:
- `patient.py` — Responds as the virtual patient based on the disease profile + scraped PDF
- `examiner.py` — Returns structured JSON lab/exam results (three modes: text-based, value-based, and physical exam)
- `problem_setter.py` — Generates full patient case JSON (demographics, MH, FH, SH, disease)
- `grader.py` — Scores student across 5 rubrics (A–E) using `response_schema` for structured output
- `advisor.py` — Provides narrative feedback on diagnosis/treatment

**Utilities** (`util/`):
- `tools.py` — Session init (`init_all()`, `init(page_id)`), PDF generation via Selenium, logging to `data/log/{SID}.txt`
- `constants.py` — All UI text and icons (Traditional Chinese)
- `chat.py` — Chat message formatting
- `process.py` — Audio → text via Google Speech Recognition (zh-TW)
- `dialog.py` — Streamlit modal dialogs

**Data**:
- `instruction_file/` — System prompts for each AI agent
- `examination_file/pe_choice.json` — Physical exam body system categories for the UI
- `examination_file/examination_choice.json` — Lab/imaging exam categories for the UI
- `examination_file/examination.csv` — Test items with reference values and units
- `data/template_problem_set/` — Pre-made patient cases (JSON)
- `data/problem_set/` — User-generated cases
- `data/log/` — Per-session conversation logs

### Session State Pattern
```python
ss = st.session_state  # used throughout the codebase
init_all()             # creates SID, timestamps, initializes all ss keys
init(page_id)          # per-page guard; redirects if prerequisites unmet
```

### AI Structured Output
Agents use Gemini's `response_schema` with `typing.TypedDict` to enforce JSON shapes. The grader returns `[{id, item, full_score, real_score, feedback}]` for each of 5 rubric dimensions.

### PDF Context for AI
`getPDF()` in `tools.py` uses Selenium to search the web and convert pages to PDFs, which are then uploaded to Gemini to give the patient/examiner agents real medical context. Falls back to `error.pdf` on failure.

## Key Conventions

- UI is entirely in **Traditional Chinese (zh-TW)**; all `constants.py` strings are Chinese
- Page indices are integers 0–5; `ss.current_progress` tracks current progress
- Each session gets a unique `SID` (timestamp-based) used for log filenames
- Logging format: `"Role: Message"` appended to `data/log/{SID}.txt`
- Gemini model used: `gemini-2.5-flash` (configured in each model file)
