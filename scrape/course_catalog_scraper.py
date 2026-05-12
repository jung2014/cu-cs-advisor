# ============================================================
# course_catalog_scraper.py
# Scrapes https://catalog.colorado.edu/courses-a-z/
# Extracts per-course: code, title, credits, description,
#   prerequisites (text + parsed course codes), corequisites
# Saves to data/static/course_catalog.json
#
# Run once (or once per year when catalog updates):
#   python course_catalog_scraper.py
# ============================================================

import json
import re
import sys
import os
import time
import requests
from typing import Optional
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from backend.config import DATA_BASE

BASE_URL = "https://catalog.colorado.edu"
COURSES_AZ_URL = f"{BASE_URL}/courses-a-z/"
REQUEST_DELAY = 0.4
OUT_PATH = f"{DATA_BASE}/static/course_catalog.json"


# ── Prerequisite parser ──────────────────────────────────────────────────────

COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,5})\s+(\d{4}[A-Z]?)\b")


def normalize_text(text: str) -> str:
    """Replace non-breaking spaces and other unicode whitespace with regular spaces."""
    return text.replace("\xa0", " ").replace("\u2009", " ").replace("\u00a0", " ")


def parse_prereq_text(text: str) -> list[str]:
    """
    Extract all course codes mentioned in a prerequisite/corequisite string.
    Returns a flat list — callers that need AND/OR logic should use the text.
    Example: "CSCI 1300 or CSCI 1200, and MATH 1300" → ["CSCI 1300","CSCI 1200","MATH 1300"]
    """
    text = normalize_text(text)
    return [f"{m.group(1)} {m.group(2)}" for m in COURSE_CODE_RE.finditer(text)]


def build_prereq_structure(text: str) -> dict:
    """
    Build a minimal machine-readable prereq structure from plain text.
    Handles "A and B", "A or B", and mixed cases.
    Returns:  {"text": "...", "any_of": [...], "all_of": [...]}
      any_of  — student needs at least one of these
      all_of  — student needs all of these
    For simple cases we approximate; complex logic stays in the text field.
    """
    text = normalize_text(text)
    result = {"text": text, "any_of": [], "all_of": []}
    if not text or "none" in text.lower():
        return result

    # Split on " and " first, then handle "or" within each group
    and_groups = re.split(r"\band\b", text, flags=re.IGNORECASE)
    for group in and_groups:
        codes = parse_prereq_text(group)
        if not codes:
            continue
        if len(codes) == 1:
            result["all_of"].append(codes[0])
        else:
            # Multiple codes within one and-group → any of them satisfies this requirement
            result["any_of"].extend(codes)

    return result


# ── HTML parsing ─────────────────────────────────────────────────────────────

def parse_credits(raw: str) -> dict:
    """
    Parse credit strings like "4", "1-3", "3-6".
    Returns {"min": int, "max": int, "fixed": int|None}
    """
    raw = raw.strip()
    range_match = re.match(r"(\d+)\s*[-–]\s*(\d+)", raw)
    if range_match:
        lo, hi = int(range_match.group(1)), int(range_match.group(2))
        return {"min": lo, "max": hi, "fixed": None}
    single = re.search(r"\d+", raw)
    if single:
        v = int(single.group())
        return {"min": v, "max": v, "fixed": v}
    return {"min": None, "max": None, "fixed": None}


def parse_courseblock(block) -> Optional[dict]:
    """
    Parse a single <div class="courseblock"> element.
    Works with the standard Kuali/Coursedog HTML CU Boulder uses.
    """
    # ── Title line ────────────────────────────────────────────────────────────
    title_el = (
        block.find(class_="courseblocktitle")
        or block.find("dt")
        or block.find(["h2", "h3"])
    )
    if not title_el:
        return None

    title_text = normalize_text(title_el.get_text(" ", strip=True))

    # "CSCI 1300 (4). Computer Science 1: Starting Computing."
    # or "CSCI 1300 (1-3). Title."
    title_match = re.match(
        r"([A-Z]{2,5})\s+(\d{4}[A-Z]?)\s*\(([\d\s\-–]+)\)[.\s]*(.+?)\.?\s*$",
        title_text,
    )
    if not title_match:
        return None

    subject = title_match.group(1).strip()
    number = title_match.group(2).strip()
    credits_raw = title_match.group(3).strip()
    course_title = title_match.group(4).strip().rstrip(".")
    code = f"{subject} {number}"
    credits = parse_credits(credits_raw)

    # ── Description ───────────────────────────────────────────────────────────
    desc_el = block.find(class_="courseblockdesc") or block.find("dd")
    description = desc_el.get_text(" ", strip=True) if desc_el else ""

    # ── Prereqs / Coreqs (may appear in courseblockextra or inline) ───────────
    prereqs_text = ""
    coreqs_text = ""

    # Check dedicated extra elements
    for extra in block.find_all(class_=re.compile(r"courseblock(extra|req)")):
        t = extra.get_text(" ", strip=True)
        tl = t.lower()
        if "prerequisite" in tl or "prereq" in tl:
            prereqs_text = t
        elif "corequisite" in tl or "coreq" in tl:
            coreqs_text = t

    # Fallback: search description for prereq sentences
    if not prereqs_text:
        for sent in re.split(r"(?<=[.!?])\s+", description):
            sl = sent.lower()
            if "prerequisite" in sl or "prereq" in sl:
                prereqs_text = sent
                break

    prereqs = parse_prereq_text(prereqs_text)
    prereq_structure = build_prereq_structure(prereqs_text)
    coreqs = parse_prereq_text(coreqs_text)

    return {
        "code": code,
        "subject": subject,
        "number": number,
        "title": course_title,
        "credits": credits["fixed"],
        "credits_min": credits["min"],
        "credits_max": credits["max"],
        "description": description,
        "prereqs_text": prereqs_text,
        "prereqs": prereqs,
        "prereqs_structured": prereq_structure,
        "coreqs_text": coreqs_text,
        "coreqs": coreqs,
    }


# ── Subject discovery ─────────────────────────────────────────────────────────

def get_subject_urls() -> list[dict]:
    """
    Scrape the courses A-Z index to discover all subject links.
    Returns list of {"subject": "CSCI", "url": "https://..."}
    """
    print(f"Fetching subject list from {COURSES_AZ_URL}")
    resp = requests.get(COURSES_AZ_URL, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    subjects = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # CU's courses-a-z links look like /courses-a-z/csci/
        if "/courses-a-z/" in href and href != "/courses-a-z/":
            full_url = BASE_URL + href if href.startswith("/") else href
            subject_code = href.strip("/").split("/")[-1].upper()
            if subject_code and subject_code not in seen:
                seen.add(subject_code)
                subjects.append({"subject": subject_code, "url": full_url})

    print(f"Found {len(subjects)} subjects")
    return subjects


# ── Per-subject scraper ───────────────────────────────────────────────────────

def scrape_subject_courses(subject: str, url: str) -> dict:
    """
    Scrape all courses for one subject.
    Returns dict keyed by course code, e.g. {"CSCI 1300": {...}, ...}
    """
    time.sleep(REQUEST_DELAY)
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code != 200:
            print(f"  [{subject}] HTTP {resp.status_code}")
            return {}
    except Exception as e:
        print(f"  [{subject}] Request failed: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find all course blocks
    blocks = soup.find_all("div", class_=re.compile(r"\bcourseblock\b"))

    # Fallback: some pages use <li> or <article> per course
    if not blocks:
        blocks = soup.find_all("li", class_=re.compile(r"\bcourse\b"))

    courses = {}
    for block in blocks:
        parsed = parse_courseblock(block)
        if parsed:
            courses[parsed["code"]] = parsed

    print(f"  [{subject}] {len(courses)} courses scraped")
    return courses


# ── Main ──────────────────────────────────────────────────────────────────────

def build_course_catalog(limit: Optional[int] = None) -> dict:
    """
    Scrape all subjects and merge into one catalog dict.
    limit: stop after N subjects (for testing)
    """
    subjects = get_subject_urls()
    if limit:
        subjects = subjects[:limit]

    catalog: dict = {}
    for i, s in enumerate(subjects, 1):
        print(f"[{i}/{len(subjects)}] {s['subject']}")
        courses = scrape_subject_courses(s["subject"], s["url"])
        catalog.update(courses)

    return catalog


def save_course_catalog():
    """Scrape and save the full course catalog."""
    print("\nBuilding course catalog...")
    catalog = build_course_catalog()

    with open(OUT_PATH, "w") as f:
        json.dump(catalog, f, indent=2)

    print(f"\n✓ Saved {len(catalog)} courses to {OUT_PATH}")
    return catalog


if __name__ == "__main__":
    save_course_catalog()
