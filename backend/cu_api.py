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
