import re
import json
import requests

from backend.config import CU_API_URL, CU_API_HEADERS, CU_API_PARAMS, CURRENT_TERM_CODE


def _parse_seats_html(html: str) -> tuple[int, int, int]:
    total_m = re.search(r"Maximum Enrollment[^:]*:\s*(\d+)", html or "", re.IGNORECASE)
    avail_m = re.search(r"Seats Avail[^:]*:\s*(\d+)",         html or "", re.IGNORECASE)
    wait_m  = re.search(r"Waitlist Total[^:]*:\s*(\d+)",       html or "", re.IGNORECASE)
    return (
        int(total_m.group(1)) if total_m else 0,
        int(avail_m.group(1)) if avail_m else 0,
        int(wait_m.group(1))  if wait_m  else 0,
    )


_LEVEL_MAP = {"lower": "UGRD_LOWER", "upper": "UGRD_UPPER"}

def fetch_hss_courses(level: str = "all") -> list[dict]:
    levels = ["lower", "upper"] if level == "all" else [level]
    seen: set = set()
    courses: list = []
    for lv in levels:
        criteria = [
            {"field": "career_or_level", "value": _LEVEL_MAP[lv]},
            {"field": "engrgened_HSS",    "value": "Y"},
        ]
        payload = {"other": {"srcdb": CURRENT_TERM_CODE}, "criteria": criteria}
        try:
            resp = requests.post(
                CU_API_URL,
                params={"page": "fose", "route": "search"},
                headers=CU_API_HEADERS,
                data=json.dumps(payload),
                timeout=15,
            )
            if resp.status_code != 200:
                continue
            for r in resp.json().get("results", []):
                code = r.get("code", "")
                if not code or code in seen:
                    continue
                seen.add(code)
                courses.append({
                    "code":       code,
                    "title":      r.get("title", ""),
                    "meets":      r.get("meets", ""),
                    "instructor": r.get("instr", ""),
                    "level":      lv,
                })
        except Exception:
            continue
    courses.sort(key=lambda x: x["code"])
    return courses


def fetch_live_seats(crn: str, subject: str) -> dict | None:
    payload = {
        "group":   f"code:{subject} {crn}",
        "key":     f"crn:{crn}",
        "srcdb":   CURRENT_TERM_CODE,
        "matched": f"crn:{crn}",
    }
    try:
        resp = requests.post(
            CU_API_URL,
            params=CU_API_PARAMS,
            headers=CU_API_HEADERS,
            data=json.dumps(payload),
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        raw = resp.json()
        seats_total, seats_available, waitlist = _parse_seats_html(raw.get("seats", ""))
        return {"seats_available": seats_available, "seats_total": seats_total, "waitlist": waitlist}
    except Exception:
        return None
