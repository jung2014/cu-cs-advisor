# ============================================================
# transcript.py
# Parses CU Boulder unofficial transcript PDFs
# Extracts: courses taken, grades, GPA, current courses
# ============================================================

import re
from typing import Optional
import pdfplumber


def parse_transcript(pdf_path: str) -> dict:
    """
    Parse a CU Boulder unofficial transcript PDF.
    Returns structured student profile dict.
    """
    with pdfplumber.open(pdf_path) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    semesters = extract_semesters(full_text)
    completed = []
    current = []
    for sem in semesters:
        for c in sem["courses"]:
            entry = {
                "code": c["code"],
                "title": c["title"],
                "credits": c["credits"],
                "grade": c["grade"],
                "term": sem["term"],
            }
            if c["in_progress"]:
                current.append({"code": c["code"], "title": c["title"], "credits": c["credits"]})
            else:
                completed.append(entry)

    return {
        "name":                    extract_name(full_text),
        "student_id":              extract_student_id(full_text),
        "cumulative_gpa":          extract_cumulative_gpa(full_text),
        "total_credits_completed": extract_cu_credits(full_text),
        "transfer_credits":        [],
        "semesters":               semesters,
        "courses_completed":       completed,
        "current_courses":         current,
        "major":                   extract_major(full_text),
    }


# ── Field extractors ──────────────────────────────────────────────────────────

def extract_name(text: str) -> str:
    m = re.search(r"NAME:\s+(.+)", text)
    if not m:
        return ""
    # "Nguyen, Justin" → "Justin Nguyen"
    raw = m.group(1).strip()
    if "," in raw:
        last, first = raw.split(",", 1)
        return f"{first.strip()} {last.strip()}"
    return raw


def extract_student_id(text: str) -> str:
    m = re.search(r"STUDENT NR:\s+[\w-]+/(\d+)", text)
    return m.group(1).strip() if m else ""


def extract_cumulative_gpa(text: str) -> Optional[float]:
    # Cumulative line: "UGRD  13.0  16.0  29.0  16.0  58.80  3.675"
    m = re.search(r"UGRD\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+([\d.]+)", text)
    return float(m.group(1)) if m else None


def extract_cu_credits(text: str) -> float:
    # CU units = second number after UGRD on the cumulative line
    m = re.search(r"UGRD\s+[\d.]+\s+([\d.]+)\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+", text)
    return float(m.group(1)) if m else 0.0


def extract_major(text: str) -> str:
    """
    Look for the college/program line in each semester block.
    E.g. "College of Engineering & Applied Science UGRD Engineering" or
         "College Arts & Sciences UGRD A&S - Open Option"
    If the declared major is generic (Open Option), infer from courses taken.
    """
    GENERIC = {"open option", "undeclared", "a&s", "arts & sciences", "arts and sciences"}

    # Try to find an explicit program declaration with degree type
    for pattern in [
        r"UGRD\s+(.*?(?:Bachelor|Master|Doctor|BS|BA|MS|PhD).*?)\n",
        r"College\s+.*?UGRD\s+([^\n]+)",
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val and not re.match(r"^[\d.]+", val):
                # If it's a real declared major, return it
                if not any(g in val.lower() for g in GENERIC):
                    return val

    # Infer from course subjects taken
    cs_count   = len(re.findall(r"\bCSCI\s+\d{4}", text))
    aero_count = len(re.findall(r"\bAERO\s+\d{4}", text))
    mech_count = len(re.findall(r"\bMCEN\s+\d{4}", text))

    if cs_count >= 2:
        return "Computer Science - Bachelor of Science (BSCS)"
    if aero_count >= 3:
        return "Aerospace Engineering Sciences - Bachelor of Science"
    if mech_count >= 3:
        return "Mechanical Engineering - Bachelor of Science"

    return ""


# ── Semester / course extraction ──────────────────────────────────────────────

# Course line: "Title Words  SUBJ 1234  3.0  A  12.00"     (completed)
#              "Title Words  SUBJ 1234  (3.0)  ***  0.00"   (in-progress)
_COURSE_RE = re.compile(
    r"^(.+?)\s+"                  # title (non-greedy)
    r"([A-Z]{2,5})\s+(\d{4}[A-Z]?)\s+"  # subject + number
    r"\(?([\d.]+)\)?\s+"          # units (with or without parens)
    r"([\w*/-]+)\s+"              # grade or ***
    r"([\d.]+)\s*$",              # grade points
    re.MULTILINE,
)


def extract_courses_from_block(block: str) -> list[dict]:
    courses = []
    for m in _COURSE_RE.finditer(block):
        title, subj, num, units, grade, pts = m.groups()
        in_progress = "***" in grade or grade.strip() == "***"
        # Skip lines that are clearly label rows (e.g. "COURSE TITLE CRSE NR UNITS GRADE PNTS")
        if subj in ("NR", "GT") or num == "PNTS":
            continue
        # Strip city/state prefixes that pdfplumber merges onto the title
        # e.g. "Mountain View CA Comp Sci 2: Data Struct"
        clean_title = re.sub(r"^[A-Z][a-z]+ [A-Z][a-z]+ [A-Z]{2}\s+", "", title.strip())
        clean_title = re.sub(r"^[A-Z][a-z]+ [A-Z]{2}\s+", "", clean_title)
        courses.append({
            "title":        clean_title.strip(),
            "code":         f"{subj} {num}",
            "credits":      float(units),
            "grade":        None if in_progress else grade.strip(),
            "grade_points": 0.0  if in_progress else float(pts),
            "in_progress":  in_progress,
        })
    return courses


def extract_semesters(text: str) -> list[dict]:
    semesters = []
    # Split on semester headers: "----- Fall 2024 CU Boulder -----"
    parts = re.split(r"-{5,}\s*([\w]+ \d{4} CU Boulder)\s*-{5,}", text)
    i = 1
    while i < len(parts) - 1:
        label   = parts[i].strip()
        content = parts[i + 1]
        courses = extract_courses_from_block(content)
        gpa_m   = re.search(r"GPA\s+([\d.]+)", content)
        att_m   = re.search(r"ATT\s+([\d.]+)", content)
        semesters.append({
            "term":             label,
            "courses":          courses,
            "semester_gpa":     float(gpa_m.group(1)) if gpa_m else None,
            "credits_attempted": float(att_m.group(1)) if att_m else None,
        })
        i += 2
    return semesters


if __name__ == "__main__":
    import json, sys
    profile = parse_transcript(sys.argv[1])
    print(json.dumps(profile, indent=2))
