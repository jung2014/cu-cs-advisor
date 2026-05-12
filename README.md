# CU Boulder CS Advisor

A web-based academic advising tool for CU Boulder Computer Science students. Upload your transcript PDF and/or paste your degree audit to get a full breakdown of degree progress, professor scores, and conflict-free schedule options.

---

## Project Structure

```
chatbot/
├── backend/
│   ├── chat.py          ← FastAPI app — all endpoints
│   ├── config.py        ← ONLY FILE YOU TOUCH EACH SEMESTER
│   ├── cu_api.py        ← live CU Classes API calls (seats, H&SS courses)
│   ├── data_loader.py   ← loads + merges all data, degree progress logic
│   ├── transcript.py    ← PDF → structured student profile
│   ├── audit_parser.py  ← degree audit text → structured student profile
│   └── system_prompt.py ← (legacy, unused)
│
├── scrape/
│   ├── schedule_scraper.py      ← hits CU Classes API, writes schedule.json
│   ├── catalog_scraper.py       ← scrapes catalog.colorado.edu/programs-a-z/
│   ├── course_catalog_scraper.py← scrapes catalog.colorado.edu/courses-a-z/
│   ├── fix_schedule_times.py    ← normalizes time formats in schedule.json
│   └── professor_score.py       ← FCQ xlsx → Justin Score per (prof, course)
│
├── test/
│   ├── test_api.py       ← sanity-checks the CU Classes API connection
│   └── claude_web_test.py← generates prompt for manual testing in claude.ai
│
├── frontend/
│   └── index.html        ← single-file vanilla JS app
│
└── data/
    ├── fcq_raw.xlsx                        ← raw FCQ data (update each semester)
    ├── static/
    │   ├── course_catalog.json             ← all CU courses, prereqs (annual)
    │   └── programs_comprehensive.json     ← degree requirements per program
    └── semester/
        └── fall2026/
            ├── schedule.json               ← all sections (scraped each semester)
            └── professors.json             ← Justin Scores (computed each semester)
```

---

## Running Locally

**Terminal 1 — backend:**
```bash
GROQ_API_KEY=your-key uvicorn backend.chat:app --port 8001 --reload
```

**Terminal 2 — frontend:**
```bash
python3 -m http.server 3000 --directory frontend
```

Open `http://localhost:3000` in your browser.

Get a free Groq API key at [console.groq.com](https://console.groq.com).

---

## Each New Semester

1. Update `backend/config.py`:
   ```python
   CURRENT_SEMESTER = "fall2027"
   CURRENT_TERM_CODE = "2287"
   ```

2. Create the new semester folder and run the scrapers:
   ```bash
   mkdir -p data/semester/fall2027

   python3 scrape/schedule_scraper.py          # pulls schedule from CU Classes API
   python3 scrape/catalog_scraper.py           # only if degree requirements changed
   python3 scrape/course_catalog_scraper.py    # only if course catalog changed
   ```

3. Drop the new FCQ xlsx into `data/fcq_raw.xlsx` and compute scores:
   ```bash
   python3 scrape/professor_score.py
   ```

---

## How It Works

### Student Input
Students can provide one or both of:
- **Transcript PDF** — unofficial transcript downloaded from the CU student portal
- **Degree audit text** — copy-pasted from the CU degree audit page

The degree audit is the preferred source because it includes requirement matching (e.g. `MATH 1300 → APPM 1350`) that the transcript alone can't determine.

### Tools (no chatbot — pure structured data)
| Tool | What it returns |
|------|----------------|
| **Degree Requirements** | All requirements grouped by section (CS Foundation, Math, Ethics, CS Core, Capstone, Science). Each course shows status (✓ done / ⏳ in progress / ○ ready / 🔒 prereqs missing), credits, alternatives, and linked labs/recitations. Section badges show `done/total classes` or `done/total credits`. |
| **Build Schedule** | Given a selection of remaining courses, generates 3 conflict-free schedules (Best Professors, Morning, Afternoon) using a greedy algorithm ranked by Justin Score. Each schedule shows an M–F calendar grid + course list. Warns if two chosen courses overlap. |
| **Course Lookup** | Full detail for any course: description, prerequisites with ✓/✗ per req, all Fall 2026 sections with Justin Score, FCQ metrics breakdown, linked labs, seat counts (live from CU API with 5-min cache). |

### Course Search
Searches by exact code (`CSCI 3753`) or keyword (`operating systems`) via Groq tool calling (Llama 3.3 70B). This is the only part that uses the LLM.

### H&SS Course Browser
The Humanities & Social Sciences requirement pools include a "Browse engineering-approved courses this semester" button that fetches live from the CU Classes API (`engrgened_HSS=Y`). Covers both lower-division (1000–2000 level) and upper-division (3000–4000 level) options.

---

## Justin Score

Each professor is scored **per course** (0–100) from CU's Faculty Course Questionnaire (FCQ) data.

**Weights (student grade-outcome focused):**
| Metric    | Weight | Rationale |
|-----------|--------|-----------|
| Feedback  | 18%    | Helps you improve = better grades |
| Grading   | 16%    | Clear criteria = no surprise deductions |
| Questions | 14%    | Accessible = you can get help |
| Challenge | 12%    | Pushes learning = exam readiness |
| Reflect   | 10%    | Deeper understanding = retention |
| Connect   | 8%     | Real-world context = understanding over memorization |
| Discuss   | 7%     | Can ask questions = clarification |
| Tech      | 5%     | Effective tools = clearer material |
| Others    | 10%    | Engagement metrics (lower grade impact) |

**Response rate confidence:** Score is scaled by `min(resp_rate / 0.60, 1.0)`. Sections below 30% response rate are excluded entirely.

One professor has one score per unique course they've taught — so the same person can have different scores for CSCI 3104 vs CSCI 4413.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/upload-transcript` | Upload PDF, returns student profile JSON |
| POST | `/upload-audit` | Paste degree audit text, returns student profile JSON |
| POST | `/requirements` | Full degree requirements breakdown with status per course |
| POST | `/build-schedule` | Generate 3 conflict-free schedules from selected courses |
| POST | `/course-detail` | Full info for one course (sections, live seats, scores, prereqs, labs) |
| POST | `/chat` | LLM-backed course search (Groq / Llama 3.3 70B) |
| GET  | `/hss-courses?level=lower\|upper\|all` | Engineering-approved H&SS courses offered this semester (live from CU API) |

---

## Data Sources

| Data | Source | Refresh |
|------|--------|---------|
| Course catalog (prereqs, descriptions) | `catalog.colorado.edu/courses-a-z/` | Annually |
| Degree requirements | `catalog.colorado.edu/programs-a-z/` | Annually (BSCS manually curated) |
| Semester schedule | CU Classes API (`classes.colorado.edu/api/`) | Each semester |
| Professor scores | CU FCQ data (xlsx from registrar) | Each semester |
| Live seat counts | CU Classes API (per-section, 5-min TTL cache) | On demand |
| Engineering H&SS courses | CU Classes API (`engrgened_HSS=Y`) | On demand |

**Note:** Seat counts in the schedule snapshot are from the last time `schedule_scraper.py` was run. The `/course-detail` endpoint fetches live seat counts with a 5-minute cache and falls back to the snapshot if the CU API is unreachable.
