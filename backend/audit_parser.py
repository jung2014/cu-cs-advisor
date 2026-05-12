import re
from typing import Optional


# ── Normalization helpers ──────────────────────────────────────────────────────

def _norm_code(raw: str) -> str:
    """'CSCI1300' or 'CSCI 1300' → 'CSCI 1300'. Strips TC suffix on transfer codes."""
    raw = raw.strip().upper()
    raw = re.sub(r'TC$', '', raw)               # ARSC1999TC → ARSC1999
    m = re.match(r'^([A-Z]{2,6})(\d{4}[A-Z]?)$', raw)
    return f"{m.group(1)} {m.group(2)}" if m else raw


def _norm_term(t: str) -> str:
    """'FA24' or 'FA2024' → 'Fall 2024'"""
    seasons = {"FA": "Fall", "SP": "Spring", "SU": "Summer"}
    m = re.match(r'^(FA|SP|SU)(\d{2,4})$', t.strip().upper())
    if not m:
        return t
    season = seasons[m.group(1)]
    year = m.group(2)
    if len(year) == 2:
        year = "20" + year
    return f"{season} {year}"


# ── Regexes ────────────────────────────────────────────────────────────────────

_COURSE_RE = re.compile(
    r'^\s*((?:FA|SP|SU)\d{2,4})\s+'           # term
    r'([A-Z]{2,6}\d{4}[A-Z0-9]?(?:TC)?)\s+'  # code (optional TC suffix)
    r'([\d.]+)\s+'                             # credits
    r'(\*\*\*|[A-Z]{1,2}[+\-]?|TC[+]?|TA|NR|W|I)\s*'  # grade
    r'(.*?)$',                                 # flags
    re.IGNORECASE,
)
_MATCHED_RE  = re.compile(r'>>\s*MATCHED\s+AS\s*:\s*([A-Z]{2,6}\s*\d{4}[A-Z]?)', re.IGNORECASE)
_GPA_RE      = re.compile(r'([\d.]+)[ \t]*GPA', re.IGNORECASE)
_EARNED_RE   = re.compile(r'EARNED\s*:\s*([\d.]+)\s*HOURS', re.IGNORECASE)
_NAME_RE     = re.compile(r'^([A-Z][a-z\'-]+),\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*$')

_NO_CREDIT_FLAGS = {">N", ">X", ">K", ">E"}
_SKIP_SUBJ       = {"ARSC", "AP"}   # generic AP/transfer placeholders


# ── Main parser ────────────────────────────────────────────────────────────────

def parse_audit(text: str) -> dict:
    """
    Parse a CU Boulder degree audit (plain-text copy-paste from the web portal).
    Returns a student profile dict compatible with the rest of the backend.
    """
    lines = text.splitlines()

    # ── Student name (first non-blank line, "Last, First" format) ──────────────
    name = ""
    for line in lines[:15]:
        m = _NAME_RE.match(line.strip())
        if m:
            name = f"{m.group(2)} {m.group(1)}"
            break

    # ── Major from program code ────────────────────────────────────────────────
    major = ""
    if re.search(r'CSEN-BSCS|ENBSCS-CSEN|BSCS', text):
        major = "Computer Science - Bachelor of Science (BSCS)"

    # ── Cumulative GPA and credits ─────────────────────────────────────────────
    gpa_hits = _GPA_RE.findall(text)
    cum_gpa: Optional[float] = float(gpa_hits[0]) if gpa_hits else None

    earned_hits = _EARNED_RE.findall(text)
    credits_earned: float = float(earned_hits[0]) if earned_hits else 0.0

    # ── Scan every line for course entries ─────────────────────────────────────
    # We may see the same code multiple times (in different requirement sections).
    # Resolution priority: completed > in-progress; higher credits > lower.

    seen: dict[str, dict] = {}          # code → course info
    matched_completions: dict[str, str] = {}  # actual_code → canonical_code

    i = 0
    while i < len(lines):
        line = lines[i]
        m = _COURSE_RE.match(line)

        if m:
            term_raw   = m.group(1).upper()
            code_raw   = m.group(2).upper()
            credits    = float(m.group(3))
            grade      = m.group(4).upper()
            flags      = m.group(5).upper()

            # Skip no-credit repeats
            if any(f in flags for f in _NO_CREDIT_FLAGS):
                i += 1
                continue

            # Skip generic AP/transfer placeholders with no real code
            code = _norm_code(code_raw)
            if any(code.startswith(s) for s in _SKIP_SUBJ):
                i += 1
                continue

            in_progress = (grade == "***")

            # Peek ahead for title (next non-blank line that isn't another course
            # line or a directive)
            title = ""
            for j in range(i + 1, min(i + 3, len(lines))):
                peek = lines[j].strip()
                if (peek
                        and not _COURSE_RE.match(peek)
                        and not peek.startswith(">>")
                        and not re.match(r'^\d+\)', peek)
                        and not peek.startswith("SELECT FROM")
                        and not peek.startswith("Grade")):
                    title = peek
                    break

            # Peek ahead for >>MATCHED AS — stop if another course line appears
            for j in range(i + 1, min(i + 5, len(lines))):
                peek = lines[j].strip()
                if _COURSE_RE.match(peek):
                    break
                mm = _MATCHED_RE.match(peek)
                if mm:
                    canonical = _norm_code(mm.group(1))
                    if canonical != code:
                        matched_completions[code] = canonical
                    break

            # Deduplicate: prefer completed, then higher credits
            existing = seen.get(code)
            replace = (
                existing is None
                or (not in_progress and existing["in_progress"])
                or (in_progress == existing["in_progress"] and credits > existing["credits"])
            )
            if replace:
                seen[code] = {
                    "code":        code,
                    "title":       title,
                    "credits":     credits,
                    "grade":       None if in_progress else grade,
                    "term":        _norm_term(term_raw),
                    "in_progress": in_progress,
                }

        i += 1

    # ── Split into completed / current ─────────────────────────────────────────
    courses_completed = []
    current_courses   = []

    for info in seen.values():
        if info["in_progress"]:
            current_courses.append({
                "code":    info["code"],
                "title":   info["title"],
                "credits": info["credits"],
                "term":    info["term"],
            })
        else:
            courses_completed.append({
                "code":    info["code"],
                "title":   info["title"],
                "credits": info["credits"],
                "grade":   info["grade"],
                "term":    info["term"],
            })

    return {
        "name":                    name,
        "major":                   major,
        "cumulative_gpa":          cum_gpa,
        "total_credits_completed": credits_earned,
        "courses_completed":       courses_completed,
        "current_courses":         current_courses,
        "matched_completions":     matched_completions,
        "source":                  "audit",
    }
