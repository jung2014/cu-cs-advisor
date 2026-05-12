# ============================================================
# catalog_scraper.py
# Scrapes http://catalog.colorado.edu/programs-a-z/
# Extracts per-program:
#   - required courses (code + title)
#   - elective pools (name, required credits/count, course list)
#   - total credit requirement
#   - program description
# Saves to data/static/programs_comprehensive.json
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
from backend.config import CU_CATALOG_URL, DATA_BASE

BASE_URL = "https://catalog.colorado.edu"
REQUEST_DELAY = 0.5

COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,5})\s+(\d{4}[A-Z]?)\b")


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_all_program_links() -> list[dict]:
    """Scrape the A-Z index and return all program links."""
    print(f"Fetching {CU_CATALOG_URL}")
    resp = requests.get(CU_CATALOG_URL, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    programs = []
    seen_urls = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        name = a.get_text(strip=True)
        if not href or not name:
            continue
        if "/programs-study/" not in href:
            continue
        if name.lower() == "programs of study":
            continue
        url = BASE_URL + href if href.startswith("/") else href
        if url in seen_urls:
            continue
        seen_urls.add(url)
        programs.append({"name": name, "url": url})

    print(f"Total programs found: {len(programs)}")
    return programs


def extract_total_credits(soup: BeautifulSoup) -> Optional[int]:
    """Look for credit-hour patterns in page text."""
    text = soup.get_text()
    patterns = [
        r"minimum[^\d]*(\d+)[^\d]*credit",
        r"(\d+)\s+credit hours? required",
        r"total[^\d]*(\d+)[^\d]*credit",
        r"(\d+)[^\d]*semester hours?",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def extract_description(soup: BeautifulSoup) -> str:
    """First substantial paragraph in the content area."""
    for p in soup.select("div#content p, main p, .field-body p"):
        text = p.get_text(strip=True)
        if len(text) > 100:
            return text[:600]
    return ""


# ── Course extraction ─────────────────────────────────────────────────────────

def _inline_title(text: str, code: str) -> str:
    """
    Try to extract a course title from inline text like:
      "CSCI 1300 – Computer Science 1: Starting Computing"
      "CSCI 1300 Computer Science 1 (4)"
    Returns empty string if not found.
    """
    # After the course code, grab text up to a paren or newline
    escaped = re.escape(code)
    m = re.search(
        escaped + r"\s*[–\-]?\s*([A-Za-z][^(\n]{4,60}?)(?:\s*\(|\s*$|\n)",
        text,
    )
    if m:
        return m.group(1).strip().rstrip(",;.")
    return ""


def extract_required_courses(soup: BeautifulSoup) -> list[dict]:
    """
    Extract required (non-elective) courses with codes and inline titles.
    Skips sections that look like elective lists.
    """
    courses = []
    seen = set()

    SKIP_KEYWORDS = {"elective", "choose", "select from", "option", "or equivalent"}

    for el in soup.find_all(["h2", "h3", "h4", "p", "li", "td"]):
        block_text = el.get_text(" ", strip=True)
        block_lower = block_text.lower()

        # Skip elective-labelled sections
        if any(kw in block_lower for kw in SKIP_KEYWORDS):
            continue

        for m in COURSE_CODE_RE.finditer(block_text):
            code = f"{m.group(1)} {m.group(2)}"
            if code in seen:
                continue
            seen.add(code)

            title = _inline_title(block_text, code)
            courses.append({"code": code, "title": title})

    return courses


# ── Elective pool extraction ──────────────────────────────────────────────────

def _parse_pool_size(heading_text: str) -> dict:
    """
    Parse credit/count requirements from a heading like:
      "Choose 3 courses from the following"
      "12 credits from the following"
      "Select at least 2 of the following"
    """
    result = {"required_credits": None, "required_count": None}

    # "X credit" pattern
    credit_m = re.search(r"(\d+)\s+credit", heading_text, re.IGNORECASE)
    if credit_m:
        result["required_credits"] = int(credit_m.group(1))

    # "choose/select X courses/of the following" pattern
    count_m = re.search(
        r"(?:choose|select|pick)\s+(?:at\s+least\s+)?(\d+)\s+(?:course|of)",
        heading_text,
        re.IGNORECASE,
    )
    if count_m:
        result["required_count"] = int(count_m.group(1))

    return result


ELECTIVE_TRIGGER_RE = re.compile(
    r"elective|choose|select from|option|technical elective|approved course",
    re.IGNORECASE,
)


def extract_elective_pools(soup: BeautifulSoup) -> dict:
    """
    Find elective sections in the page and extract course lists per pool.
    Returns the standard electives dict with populated pools.
    """
    electives = {"pools": [], "total_elective_credits": None}

    # Walk through headings; when we hit an elective-sounding one,
    # collect courses from the following siblings until the next heading.
    headings = soup.find_all(["h2", "h3", "h4"])

    for heading in headings:
        heading_text = heading.get_text(" ", strip=True)
        if not ELECTIVE_TRIGGER_RE.search(heading_text):
            continue

        pool = {
            "name": heading_text,
            **_parse_pool_size(heading_text),
            "courses": [],
        }

        # Walk forward siblings until next heading
        for sibling in heading.find_next_siblings():
            if sibling.name in ("h2", "h3", "h4"):
                break  # new section

            sib_text = sibling.get_text(" ", strip=True)
            for m in COURSE_CODE_RE.finditer(sib_text):
                code = f"{m.group(1)} {m.group(2)}"
                title = _inline_title(sib_text, code)
                entry = {"code": code, "title": title}
                if entry not in pool["courses"]:
                    pool["courses"].append(entry)

        if pool["courses"]:
            electives["pools"].append(pool)

    # Also try to find a total elective credit count on the page
    text = soup.get_text()
    credit_m = re.search(r"(\d+)\s+elective\s+credits?", text, re.IGNORECASE)
    if credit_m:
        electives["total_elective_credits"] = int(credit_m.group(1))

    return electives


# ── Per-program scraper ───────────────────────────────────────────────────────

def scrape_program_details(program: dict) -> dict:
    """Scrape a single program page and return structured data."""
    try:
        time.sleep(REQUEST_DELAY)
        resp = requests.get(program["url"], timeout=15)
        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code}: {program['url']}")
            return {}
        soup = BeautifulSoup(resp.text, "html.parser")

        return {
            "name": program["name"],
            "url": program["url"],
            "total_credits": extract_total_credits(soup),
            "required_courses": extract_required_courses(soup),
            "electives": extract_elective_pools(soup),
            "description": extract_description(soup),
        }
    except Exception as e:
        print(f"  Error scraping {program['name']}: {e}")
        return {}


# ── Bulk scraper ──────────────────────────────────────────────────────────────

def scrape_all_programs(
    filter_keyword: Optional[str] = None,
    limit: Optional[int] = None,
) -> dict:
    programs = get_all_program_links()

    if filter_keyword:
        programs = [p for p in programs if filter_keyword.lower() in p["name"].lower()]
    if limit:
        programs = programs[:limit]

    all_data = {}
    for i, program in enumerate(programs, 1):
        print(f"[{i}/{len(programs)}] {program['name']}")
        data = scrape_program_details(program)
        if data:
            all_data[program["name"]] = data

    return all_data


def save_comprehensive_catalog():
    """Scrape all programs and save to JSON."""
    out_path = f"{DATA_BASE}/static/programs_comprehensive.json"
    print("\nScraping all programs...")
    data = scrape_all_programs()

    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✓ Saved {len(data)} programs to {out_path}")

    return data


if __name__ == "__main__":
    save_comprehensive_catalog()
