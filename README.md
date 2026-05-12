# CU Boulder CS Advisor

A web-based academic advising tool for CU Boulder Computer Science students. Upload your transcript PDF and/or paste your degree audit to get a full breakdown of degree progress, professor scores, and conflict-free schedule options.

---

## Project Structure

```
chatbot/
├── backend/
│   ├── chat.py          ← FastAPI app — all endpoints
│   ├── config.py        ← ONLY FILE YOU TOUCH EACH SEMESTER
│   ├── data_loader.py   ← loads + merges all data, degree progress logic
│   ├── transcript.py    ← PDF → structured student profile
│   ├── audit_parser.py  ← degree audit text → structured student profile
│   └── system_prompt.py ← (legacy, unused)
│
├── scrape/
│   ├── schedule_scraper.py      ← hits CU Classes API, writes schedule.json
│   ├── catalog_scraper.py       ← scrapes catalog.colorado.edu/programs-a-z/
│   ├── course_catalog_scraper.py← scrapes catalog.colorado.edu/courses-a-z/
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
| **Degree Requirements** | All requirements grouped by section (CS Foundation, Math, Ethics, CS Core, Capstone, Science). Each course shows status (✓ done / ⏳ in progress / ○ ready / 🔒 prereqs missing), credits, alternatives, and linked labs/recitations. |
| **Build Schedule** | Given a selection of remaining courses, generates 3 conflict-free schedules (Best Professors, Morning, Afternoon) using a greedy algorithm ranked by Justin Score. Each schedule shows an M–F calendar grid + course list. |
| **Course Lookup** | Full detail for any course: description, prerequisites with ✓/✗ per req, all Fall 2026 sections with Justin Score, FCQ metrics breakdown, linked labs, seat counts. |

### Course Search
Searches by exact code (`CSCI 3753`) or keyword (`operating systems`) via Groq tool calling (Llama 3.3 70B). This is the only part that uses the LLM.

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

**Response rate confidence:** Score is scaled by `min(resp_rate / 0.60, 1.0)`. Sections below 30% response rate are excluded entirely. Low response rates are the main reason a professor with decent raw scores can still end up with a low Justin Score.

One professor has one score per unique course they've taught — so the same person can have different scores for CSCI 3104 vs CSCI 4413.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/upload-transcript` | Upload PDF, returns student profile JSON |
| POST | `/upload-audit` | Paste degree audit text, returns student profile JSON |
| POST | `/requirements` | Full degree requirements breakdown with status per course |
| POST | `/build-schedule` | Generate 3 conflict-free schedules from selected courses |
| POST | `/course-detail` | Full info for one course (sections, scores, prereqs, labs) |
| POST | `/chat` | LLM-backed course search (Groq / Llama 3.3 70B) |

---

## Data Sources

| Data | Source | Refresh |
|------|--------|---------|
| Course catalog (prereqs, descriptions) | `catalog.colorado.edu/courses-a-z/` | Annually |
| Degree requirements | `catalog.colorado.edu/programs-a-z/` | Annually (BSCS manually curated) |
| Semester schedule | CU Classes API (`classes.colorado.edu/api/`) | Each semester |
| Professor scores | CU FCQ data (xlsx from registrar) | Each semester |

**Note:** Seat counts in the schedule are a static snapshot from the last time `schedule_scraper.py` was run. They do not update live.
