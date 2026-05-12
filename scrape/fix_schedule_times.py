"""
One-time fix: parse embedded times out of days strings in schedule.json.
Converts e.g. days="TTh 8am-9:15am" start_time="" end_time=""
         into days="TTh"             start_time="8:00am" end_time="9:15am"
"""
import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import SEMESTER

PATTERN = re.compile(
    r"([A-Za-z]+)\s+(\d+(?::\d+)?(?:am|pm)?)\s*[-–]\s*(\d+(?::\d+)?(?:am|pm)?)",
    re.IGNORECASE,
)


def norm_time(t: str) -> str:
    t = t.strip().lower()
    if ":" not in t:
        t = re.sub(r"(\d+)(am|pm)", r"\1:00\2", t)
    return t


def norm_days(raw: str) -> str:
    return re.sub(r"[Tt][Hh]", "Th", raw)


path = SEMESTER["schedule"]
with open(path) as f:
    data = json.load(f)

fixed = 0
for section in data:
    for mtg in section.get("meetings", []):
        if mtg.get("start_time"):
            continue
        m = PATTERN.match(mtg.get("days", ""))
        if m:
            mtg["days"] = norm_days(m.group(1))
            mtg["start_time"] = norm_time(m.group(2))
            mtg["end_time"] = norm_time(m.group(3))
            fixed += 1

with open(path, "w") as f:
    json.dump(data, f)

print(f"Fixed {fixed} meetings in {path}")
