# ============================================================
# config.py — ONLY FILE YOU TOUCH EACH SEMESTER
# ============================================================

import os

CURRENT_SEMESTER = "fall2026"
CURRENT_TERM_CODE = "2267"

# Absolute path to the repo root so imports work regardless of CWD
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_BASE = os.path.join(_ROOT, "data")

STATIC = {
    "requirements": f"{DATA_BASE}/static/programs_comprehensive.json",
    "catalog":      f"{DATA_BASE}/static/course_catalog.json",
}

SEMESTER = {
    "schedule":   f"{DATA_BASE}/semester/{CURRENT_SEMESTER}/schedule.json",
    "professors": f"{DATA_BASE}/semester/{CURRENT_SEMESTER}/professors.json",
}

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.3-70b-versatile"
MAX_TOKENS   = 2048

CU_API_URL = "https://classes.colorado.edu/api/"
CU_API_HEADERS = {
    'sec-ch-ua-platform': '"macOS"',
    'Referer': 'https://classes.colorado.edu/',
    'sec-ch-ua': '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
    'sec-ch-ua-mobile': '?0',
    'X-Requested-With': 'XMLHttpRequest',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Content-Type': 'application/json',
}
CU_API_PARAMS = {"page": "fose", "route": "details"}

CU_CATALOG_URL = "https://catalog.colorado.edu/programs-a-z/"
