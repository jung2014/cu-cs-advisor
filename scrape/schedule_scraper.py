# ============================================================
# schedule_scraper.py
# Pulls full semester schedule from CU Classes API
# Run once per semester, saves to data/semester/{term}/schedule.json
# ============================================================

import json
import re
import sys
import os
import requests
from typing import Optional
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from backend.config import (
    CU_API_URL, CU_API_HEADERS, CU_API_PARAMS,
    CURRENT_SEMESTER, CURRENT_TERM_CODE, DATA_BASE
)

# All known CU Boulder subject codes
SUBJECTS = [
    "ACCT", "AERO", "AFSC", "ANTH", "APPM", "AREN", "ARSC", "ARTS",
    "ASEN", "ATLS", "BCOR", "BIOL", "BMEN", "BSLW", "BUS",  "CBEN",
    "CHEM", "CLAS", "CMCI", "COLS", "COME", "CSCI", "CVEN", "DANC",
    "DSGN", "DTSA", "EBIO", "ECEN", "ECON", "EDUC", "EMGT", "EMEN",
    "ENGL", "ENVD", "ENVS", "EPID", "ETHN", "EVEN", "FILM", "FNCE",
    "FREN", "GEOG", "GEOL", "GERM", "GEEN", "HIST", "HNRS", "HUEN",
    "IPHY", "ITEC", "JWST", "JOUR", "KNES", "LAWS", "LING", "LISC",
    "MATH", "MBAX", "MCDB", "MCEN", "MKTG", "MUEL", "MUEN", "MUGN",
    "MUHI", "MUSC", "NEUR", "PHIL", "PHYS", "POLS", "PORT", "PSCI",
    "PSYC", "SCAN", "SLHS", "SOCY", "SPAN", "SPED", "STAT", "THEA",
    "WMST", "WRIT",
]


# ── HTML parsers for API response fields ─────────────────────────────────────

def parse_instructor_html(html: str) -> str:
    """Extract instructor name from instructor_info_html."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    a = soup.find("a")
    return a.get_text(strip=True) if a else soup.get_text(strip=True)


def _norm_time(t: str) -> str:
    """Normalize '8am' → '8:00am', '10:10am' stays as-is."""
    t = t.strip().lower()
    if ":" not in t:
        t = re.sub(r"(\d+)(am|pm)", r"\1:00\2", t)
    return t


def parse_meeting_html(html: str) -> list[dict]:
    """
    Parse meeting_html into structured meeting dicts.
    Example HTML: <div class="meet">MWF 9:00am-9:50am <a href="...">ECCR 265</a></div>
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    meetings = []

    for meet_div in soup.find_all("div", class_="meet"):
        location_a = meet_div.find("a")
        location = location_a.get_text(strip=True) if location_a else ""

        # Get just the time/days text (remove location link text)
        raw_text = meet_div.get_text(" ", strip=True)
        time_text = raw_text.replace(location, "").strip()

        # Pattern: "MWF 9:00am-9:50am", "TTh 8am-9:15am", "M 4:40pm-5:30pm"
        m = re.match(
            r"([A-Za-z]+)\s+(\d+(?::\d+)?(?:am|pm)?)\s*[-–]\s*(\d+(?::\d+)?(?:am|pm)?)",
            time_text,
            re.IGNORECASE,
        )
        if m:
            days_raw = m.group(1)
            days_norm = re.sub(r"[Tt][Hh]", "Th", days_raw)
            days_norm = re.sub(r"[^A-Za-z]", "", days_norm)
            meetings.append({
                "days": days_norm,
                "start_time": _norm_time(m.group(2)),
                "end_time": _norm_time(m.group(3)),
                "location": location,
            })
        else:
            # TBA, online, or unusual format — store as-is
            meetings.append({
                "days": time_text,
                "start_time": "",
                "end_time": "",
                "location": location,
            })

    return meetings


def parse_seats_html(html: str) -> tuple[int, int, int]:
    """
    Parse seats HTML into (total, available, waitlist).
    Example: "<strong>Maximum Enrollment</strong>: 30 / <strong>Seats Avail</strong>: 12
              <strong>Waitlist Total</strong>: 3"
    """
    total_m  = re.search(r"Maximum Enrollment[^:]*:\s*(\d+)", html or "", re.IGNORECASE)
    avail_m  = re.search(r"Seats Avail[^:]*:\s*(\d+)",         html or "", re.IGNORECASE)
    wait_m   = re.search(r"Waitlist Total[^:]*:\s*(\d+)",       html or "", re.IGNORECASE)
    return (
        int(total_m.group(1)) if total_m else 0,
        int(avail_m.group(1)) if avail_m else 0,
        int(wait_m.group(1))  if wait_m  else 0,
    )


def parse_credits(raw: str) -> Optional[float]:
    """Parse credit hours — returns float or None."""
    if not raw:
        return None
    m = re.search(r"[\d.]+", raw)
    return float(m.group()) if m else None


# ── API calls ─────────────────────────────────────────────────────────────────

def search_courses(subject: str, term_code: str) -> list[dict]:
    """Search all course sections for a subject in a given term."""
    payload = {
        "other": {"srcdb": term_code},
        "criteria": [{"field": "subject", "value": subject}],
    }
    resp = requests.post(
        CU_API_URL,
        params={"page": "fose", "route": "search"},
        headers=CU_API_HEADERS,
        data=json.dumps(payload),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def fetch_course_details(crn: str, subject: str, term_code: str) -> Optional[dict]:
    """Fetch full details for a single CRN and return a clean structured dict."""
    payload = {
        "group":   f"code:{subject} {crn}",
        "key":     f"crn:{crn}",
        "srcdb":   term_code,
        "matched": f"crn:{crn}",
    }
    resp = requests.post(
        CU_API_URL,
        params=CU_API_PARAMS,
        headers=CU_API_HEADERS,
        data=json.dumps(payload),
        timeout=30,
    )
    if resp.status_code != 200:
        return None

    raw = resp.json()

    # Parse HTML fields
    seats_total, seats_available, waitlist = parse_seats_html(raw.get("seats", ""))

    # Extract subject + number from "code" field (e.g. "CSCI 1300")
    code = raw.get("code", "")
    code_parts = code.split()
    subj = code_parts[0] if len(code_parts) > 0 else subject
    number = code_parts[1] if len(code_parts) > 1 else ""

    return {
        "crn":             crn,
        "course_id":       code,
        "title":           raw.get("title", ""),
        "subject":         subj,
        "course_number":   number,
        "section":         raw.get("section", ""),
        "credits":         parse_credits(raw.get("hours_text", raw.get("hours", ""))),
        "section_type":    raw.get("schd", ""),           # "LEC", "REC", "LAB", "SEM", etc.
        "linked_crns":     raw.get("linked_crns", ""),    # CRNs of required co-sections
        "instructor":      parse_instructor_html(raw.get("instructor_info_html", "")),
        "meetings":        parse_meeting_html(raw.get("meeting_html", "")),
        "seats_available": seats_available,
        "seats_total":     seats_total,
        "waitlist":        waitlist,
        "description":     BeautifulSoup(raw.get("description", ""), "html.parser").get_text(" ", strip=True),
        "prereqs":         raw.get("restrict_info", "") or raw.get("clssnotes", ""),
        "attributes":      raw.get("attributes", ""),
        "campus":          raw.get("campus", ""),
        "instruction_mode": BeautifulSoup(raw.get("instmode_html", ""), "html.parser").get_text(strip=True),
        "dates":           raw.get("dates_html", ""),
        "term":            raw.get("srcdb", term_code),
    }


# ── Bulk scraper ──────────────────────────────────────────────────────────────

def scrape_full_schedule(subjects: list[str], term_code: str) -> list[dict]:
    """Scrape all sections for all subjects and return as flat list."""
    all_sections = []
    for i, subject in enumerate(subjects, 1):
        print(f"[{i}/{len(subjects)}] {subject}...", end=" ", flush=True)
        try:
            results = search_courses(subject, term_code)
            count = 0
            for r in results:
                crn = r.get("crn")
                if not crn:
                    continue
                details = fetch_course_details(crn, subject, term_code)
                if details:
                    all_sections.append(details)
                    count += 1
            print(f"{count} sections")
        except Exception as e:
            print(f"ERROR: {e}")
            continue

    print(f"\nTotal sections collected: {len(all_sections)}")
    return all_sections


def save_schedule():
    out_path = f"{DATA_BASE}/semester/{CURRENT_SEMESTER}/schedule.json"
    print(f"Scraping schedule for term {CURRENT_TERM_CODE}...")
    sections = scrape_full_schedule(SUBJECTS, CURRENT_TERM_CODE)
    with open(out_path, "w") as f:
        json.dump(sections, f, indent=2)
    print(f"✓ Saved {len(sections)} sections to {out_path}")


if __name__ == "__main__":
    save_schedule()
