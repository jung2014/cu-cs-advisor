# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.


## Running the App

**Backend** (requires `GROQ_API_KEY`):
```bash
GROQ_API_KEY=your-key uvicorn backend.chat:app --port 8001 --reload
```

**Frontend** (static file server, separate terminal):
```bash
python3 -m http.server 3000 --directory frontend
```

Open `http://localhost:3000`. Get a free key at console.groq.com.

**Run a scraper manually:**
```bash
python3 scrape/schedule_scraper.py    # fetch this semester's schedule
python3 scrape/professor_score.py     # recompute Justin Scores from FCq_raw.xlsx
```

**Test the CU Classes API connection:**
```bash
python3 test/test_api.py
```

## Critical Rules
  - Commits: suggest 3 options before every commit, commit after each small change, only push at the end after asking                                  
  - No Co-Authored-By in commit messages                                                                                                               
  - No em dashes in any written text                                                                                                                   
  - No comments or docstrings in code files 

## Architecture

This is a FastAPI backend + single-file vanilla JS frontend. There is no framework build step; `frontend/index.html` is served directly via Python's HTTP server.

### Data flow

1. Student provides either a **transcript PDF** or **degree audit text** via the UI.
2. The frontend POSTs to `/upload-transcript` or `/upload-audit`. The backend returns a `student_profile` JSON dict (name, major, completed courses, GPA, credits).
3. The frontend stores `student_profile` in memory and passes it on every subsequent request.
4. Three pure-data endpoints (`/requirements`, `/build-schedule`, `/course-detail`) run entirely in Python with no LLM — they join `student_profile` against the loaded JSON files.
5. `/chat` is the only LLM endpoint. It sends the message + student profile to Groq (Llama 3.3 70B) with two tools: `search_course` and `get_degree_requirements`. Tool results are resolved in `run_tool()` (also pure Python) and fed back into the agentic loop until a final text answer is produced.

### Key backend files

| File | Responsibility |
|------|----------------|
| `backend/config.py` | **Only file changed each semester** — `CURRENT_SEMESTER`, `CURRENT_TERM_CODE`, file paths, Groq settings |
| `backend/chat.py` | FastAPI app, all endpoints, schedule-builder, agentic loop, tool definitions/execution |
| `backend/data_loader.py` | Loads the four JSON data files; `compute_degree_progress`, `get_sections_for_course`, `_prereq_satisfied`, `_inject_dynamic_pools` |
| `backend/transcript.py` | `pdfplumber`-based PDF parser → `student_profile` |
| `backend/audit_parser.py` | Regex-based degree audit text parser → `student_profile` (preferred over transcript because it captures requirement matches like `MATH 1300 → APPM 1350` via `matched_completions`) |

### Data files (not tracked by LLM — loaded once at startup into `DATA`)

| File | Refresh cadence |
|------|----------------|
| `data/static/course_catalog.json` | Annually |
| `data/static/programs_comprehensive.json` | Annually (BSCS manually curated) |
| `data/semester/<semester>/schedule.json` | Each semester via `schedule_scraper.py` |
| `data/semester/<semester>/professors.json` | Each semester via `professor_score.py` |

`load_all_data()` in `data_loader.py` runs once at import time and is stored in the module-level `DATA` dict. All endpoints read from `DATA`.

### Prerequisite structure

Prereqs in `course_catalog.json` use `{"all_of": [...], "any_of": [...]}`. `_prereq_satisfied()` in `data_loader.py` is the single source of truth for prereq checking — used by the schedule builder, requirements endpoint, and course detail endpoint.

### Professor scores (Justin Score)

Stored in `data/semester/<semester>/professors.json` keyed as `{"Last, First": {"CSCI3104": {"score": 82.1, "metrics": {...}, "avg_resp_rate": 0.72}}}`. `get_sections_for_course()` in `data_loader.py` injects scores into section dicts at query time, with a fallback chain: exact name match → "Last, First" inversion → last-name-only match.

### Schedule builder

`_build_schedule()` in `chat.py` is a greedy algorithm: for each requested course, it picks the highest-scoring (or time-preference-filtered) conflict-free lecture section, then links its lab/recitation via `linked_crns`. Returns three variants: `any`, `morning`, `afternoon`.

## Each New Semester

1. Update `CURRENT_SEMESTER` and `CURRENT_TERM_CODE` in `backend/config.py`.
2. Create `data/semester/<new>/` and run the scrapers.
3. Drop updated `fcq_raw.xlsx` into `data/` and run `professor_score.py`.
