# ============================================================
# chat.py — FastAPI backend with Groq tool calling
#
# Endpoints:
#   POST /upload-transcript  → parse PDF, return student profile
#   POST /chat               → send message, get advisor response
# ============================================================

import os
import re
import json
import time
import tempfile
import requests

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import GROQ_API_URL, GROQ_MODEL, MAX_TOKENS
from backend.transcript import parse_transcript
from backend.audit_parser import parse_audit
from backend.cu_api import fetch_live_seats
from backend.data_loader import (
    load_all_data,
    get_sections_for_course,
    get_requirements_for_major,
    compute_degree_progress,
    _inject_dynamic_pools,
    _prereq_satisfied,
)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA = load_all_data()

_seat_cache: dict[str, tuple[float, dict]] = {}
_SEAT_TTL = 300

def _get_seats(crn: str, subject: str, snapshot: dict) -> tuple[dict, bool]:
    now = time.time()
    cached = _seat_cache.get(crn)
    if cached and now - cached[0] < _SEAT_TTL:
        return cached[1], True
    live = fetch_live_seats(crn, subject)
    if live:
        _seat_cache[crn] = (now, live)
        return live, True
    return snapshot, False


# ── Request / Response models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    student_profile: dict
    message: str
    history: list[dict] = []


class ChatResponse(BaseModel):
    reply: str
    updated_history: list[dict]


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_meeting(m: dict) -> str:
    """Format a meeting dict → 'MWF 10:00am-10:50am | ECCR 265'"""
    days_raw = m.get("days", "")
    start_t = m.get("start_time", "")
    end_t = m.get("end_time", "")
    location = m.get("location", "")

    if not start_t:
        # Combined format like "MWF 10:00am-10:50am" already in days_raw
        pm = re.match(
            r"([A-Za-z]+)\s+([\d:]+(?:am|pm)?)\s*[-–]\s*([\d:]+(?:am|pm)?)",
            days_raw, re.IGNORECASE,
        )
        if pm:
            days_raw, start_t, end_t = pm.group(1), pm.group(2), pm.group(3)
        else:
            time_part = days_raw
            loc_part = f" | {location}" if location else ""
            return f"{time_part}{loc_part}"

    time_str = f"{start_t}–{end_t}" if start_t else "TBA"
    parts = [f"{days_raw} {time_str}".strip()]
    if location:
        parts.append(location)
    return " | ".join(parts)


def _prereq_lines(prereq_struct: dict, all_taken: set, catalog: dict) -> list[str]:
    """Return formatted prereq lines with ✓/✗ per requirement."""
    if not prereq_struct:
        return ["  None"]

    any_of = prereq_struct.get("any_of", [])
    all_of = prereq_struct.get("all_of", [])

    if not any_of and not all_of:
        return ["  None"]

    lines = []
    for code in all_of:
        met = code in all_taken
        title = catalog.get(code, {}).get("title", "")
        label = f"{code} — {title}" if title else code
        lines.append(f"  {'✓' if met else '✗'} {label}")

    if any_of:
        met = any(c in all_taken for c in any_of)
        options = ", ".join(any_of)
        lines.append(f"  {'✓' if met else '✗'} One of: {options}")

    return lines


def _get_linked_sections(lec_section: dict, all_sections: list[dict]) -> list[dict]:
    """Return LAB/REC sections linked to a LEC section."""
    linked_raw = lec_section.get("linked_crns", "")
    if linked_raw:
        crn_set = {c.strip() for c in str(linked_raw).split(",") if c.strip()}
        return [s for s in all_sections if str(s.get("crn", "")) in crn_set]
    # Fall back: return all LAB/REC sections for same course
    return [
        s for s in all_sections
        if s.get("section_type") in ("LAB", "REC")
    ]


# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "search_course",
        "description": (
            "Search for a course by code (e.g. 'CSCI 3753') or by name/keyword "
            "(e.g. 'operating systems', 'intro to computing'). Returns course name, "
            "code, credits, prerequisites with whether the student meets them (✓/✗), "
            "all sections with professor scores and meeting times, and any linked "
            "labs or recitations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Course code like 'CSCI 3753', or a name/keyword like "
                        "'operating systems' or 'intro to computing'"
                    ),
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_degree_requirements",
        "description": (
            "Get the student's remaining degree requirements. For each remaining "
            "required course shows: name, code, credits, prerequisites with whether "
            "the student currently meets them (✓/✗), and whether the course has "
            "labs or recitations this semester."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


# ── Tool execution ────────────────────────────────────────────────────────────

def run_tool(tool_name: str, tool_input: dict, student: dict) -> str:
    catalog = DATA["catalog"]
    completed = {c["code"] for c in student.get("courses_completed", [])}
    in_progress = {c["code"] for c in student.get("current_courses", [])}
    all_taken = completed | in_progress

    # ── search_course ─────────────────────────────────────────────────────────
    if tool_name == "search_course":
        query = tool_input.get("query", "").strip()
        query_norm = query.replace(" ", "").upper()

        # 1. Exact code match (handles "CSCI3753" or "CSCI 3753")
        course = None
        for code, c in catalog.items():
            if code.replace(" ", "").upper() == query_norm:
                course = c
                break

        # 2. Title keyword search
        if not course:
            query_lower = query.lower()
            matches = []
            for code, c in catalog.items():
                if query_lower in (c.get("title") or "").lower():
                    matches.append(c)
            if not matches:
                # Broader: check description too
                for code, c in catalog.items():
                    desc = (c.get("description") or "").lower()
                    if query_lower in desc:
                        matches.append(c)
            if not matches:
                return f"No course found matching '{query}'."
            if len(matches) > 1:
                opts = "\n".join(
                    f"  {c['code']} — {c.get('title', '')}" for c in matches[:8]
                )
                return f"Multiple courses match '{query}'. Did you mean one of:\n{opts}"
            course = matches[0]

        course_code = course.get("code", "")
        title = course.get("title", "")
        credits = course.get("credits", "?")
        prereq_struct = course.get("prereqs_structured", {})

        lines = [
            f"{course_code} — {title}",
            f"Credits: {credits}",
            "",
            "Prerequisites:",
        ]
        lines += _prereq_lines(prereq_struct, all_taken, catalog)

        # Schedule sections
        all_sections = get_sections_for_course(course_code, DATA)
        lec_sections = [
            s for s in all_sections
            if s.get("section_type") in ("LEC", "SEM", "OTH", "IND")
            or not s.get("section_type")
        ]
        lab_recs = [
            s for s in all_sections
            if s.get("section_type") in ("LAB", "REC")
        ]

        if not all_sections:
            lines += ["", "Not offered Fall 2026."]
            return "\n".join(lines)

        lines += ["", "Sections — Fall 2026:"]

        for s in lec_sections:
            score = s.get("professor_score")
            instructor = s.get("instructor") or "TBA"
            stype = s.get("section_type", "LEC")
            seats_avail = s.get("seats_available", 0)
            seats_total = s.get("seats_total", 0)
            waitlist = s.get("waitlist", 0)
            metrics = s.get("professor_metrics", {})
            resp_rate = s.get("professor_avg_resp_rate")

            lines.append(f"\n  [{stype}] Section {s.get('section')} | {instructor}")
            for m in s.get("meetings", []):
                lines.append(f"    {_fmt_meeting(m)}")
            lines.append(
                f"    Seats: {seats_avail}/{seats_total}"
                + (f" | Waitlist: {waitlist}" if waitlist else "")
            )

            # Justin Score + full FCQ breakdown
            if score is not None:
                resp_str = f" (response rate: {resp_rate*100:.0f}%)" if resp_rate else ""
                lines.append(f"    Justin Score: {score:.1f}/100{resp_str}")
                if metrics:
                    lines.append("    FCQ Scores (1–5 scale):")
                    metric_order = [
                        "Feedback", "Grading", "Questions", "Challenge", "Reflect",
                        "Connect", "Discuss", "Tech", "Interact", "Collab",
                        "Contrib", "Eval", "Synth", "Diverse", "Respect", "Creative",
                    ]
                    metric_line = "    " + "  ".join(
                        f"{k}: {metrics[k]:.2f}"
                        for k in metric_order if k in metrics
                    )
                    lines.append(metric_line)
            else:
                lines.append("    Justin Score: No FCQ data")

            # Linked labs/recitations for this LEC
            linked = _get_linked_sections(s, lab_recs)
            if linked:
                lines.append("    Labs/Recitations (linked):")
                for lab in linked:
                    lab_meetings = " | ".join(_fmt_meeting(m) for m in lab.get("meetings", []))
                    lab_seats = lab.get("seats_available", 0)
                    lab_total = lab.get("seats_total", 0)
                    lines.append(
                        f"      [{lab.get('section_type')}] Section {lab.get('section')} | "
                        f"{lab_meetings} | Seats: {lab_seats}/{lab_total}"
                    )

        # Unlinked lab/rec sections (no linked_crns on any LEC)
        if lab_recs and not any(s.get("linked_crns") for s in lec_sections):
            lines.append("\n  Labs/Recitations:")
            for lab in lab_recs:
                lab_meetings = " | ".join(_fmt_meeting(m) for m in lab.get("meetings", []))
                lab_seats = lab.get("seats_available", 0)
                lab_total = lab.get("seats_total", 0)
                lines.append(
                    f"    [{lab.get('section_type')}] Section {lab.get('section')} | "
                    f"{lab_meetings} | Seats: {lab_seats}/{lab_total}"
                )

        return "\n".join(lines)

    # ── get_degree_requirements ───────────────────────────────────────────────
    elif tool_name == "get_degree_requirements":
        major = student.get("major", "")
        major_reqs = get_requirements_for_major(major, DATA)
        if not major_reqs:
            return (
                f"Could not find degree requirements for '{major}'. "
                "The student's major may not be recognized. Try being more specific."
            )
        major_reqs = _inject_dynamic_pools(major_reqs, DATA)

        progress = compute_degree_progress(student, major_reqs)
        remaining = progress.get("remaining_required", [])
        completed_req = progress.get("completed_required", [])

        credits_earned = progress.get("total_credits_earned", 0)
        credits_needed = progress.get("total_credits_needed")
        credits_remaining = progress.get("credits_remaining")

        lines = [f"Degree Requirements — {major}"]
        if credits_needed:
            lines.append(
                f"Credits: {credits_earned}/{credits_needed} earned "
                f"({credits_remaining} remaining)"
            )
        lines.append(
            f"Completed required: {len(completed_req)} | Still needed: {len(remaining)}"
        )

        if not remaining:
            lines.append("\nAll required courses completed!")
            return "\n".join(lines)

        lines.append("\n── Remaining Required Courses ──")

        schedule_by_code: dict[str, list] = {}
        for s in DATA.get("schedule", []):
            cid = s.get("course_id", "")
            schedule_by_code.setdefault(cid, []).append(s)

        for item in remaining:
            # remaining_required is now a list of dicts from data_loader
            code = item.get("code", "") if isinstance(item, dict) else item
            req_alts = item.get("alternatives", []) if isinstance(item, dict) else []
            req_credits = item.get("credits") if isinstance(item, dict) else None

            course = catalog.get(code, {})
            if not course:
                for k, v in catalog.items():
                    if k.replace(" ", "").upper() == code.replace(" ", "").upper():
                        course = v
                        break

            title = course.get("title", "") or (item.get("title", "") if isinstance(item, dict) else "")
            credits = req_credits or course.get("credits", "?")
            prereq_struct = course.get("prereqs_structured", {})

            lines.append(f"\n{code}{' — ' + title if title else ''}")
            lines.append(f"  Credits: {credits}")
            if req_alts:
                lines.append(f"  (or {', '.join(req_alts)})")

            prereq_display = _prereq_lines(prereq_struct, all_taken, catalog)
            lines.append("  Prerequisites:")
            lines += [f"  {l}" for l in prereq_display]

            # Check if offered this semester (check primary + alternatives)
            offered_sections = []
            for check_code in [code] + req_alts:
                offered_sections = schedule_by_code.get(check_code, [])
                if offered_sections:
                    break

            if not offered_sections:
                lines.append("  Offered Fall 2026: No")
            else:
                has_lab = any(
                    s.get("section_type") in ("LAB", "REC") for s in offered_sections
                )
                lines.append(
                    f"  Offered Fall 2026: Yes | "
                    f"Labs/Recitations: {'Yes' if has_lab else 'None'}"
                )

        # Elective pool summary
        pool_progress = progress.get("elective_pool_progress", [])
        for pool in pool_progress:
            req_str = (
                f"{pool['required_credits']} credits" if pool.get("required_credits")
                else f"{pool.get('required_count', '?')} courses"
            )
            taken_str = ", ".join(pool.get("taken", [])) or "none yet"
            remaining_pool = pool.get("remaining", [])
            lines.append(f"\n── Elective Pool: {pool['name']} (need {req_str}) ──")
            lines.append(f"  Taken: {taken_str}")
            if remaining_pool:
                lines.append(f"  Options: {', '.join(remaining_pool[:10])}")

        return "\n".join(lines)

    return f"Unknown tool: {tool_name}"


# ── System prompt ─────────────────────────────────────────────────────────────

def build_system(student: dict) -> str:
    name = student.get("name", "the student")
    major = student.get("major", "Unknown")
    gpa = student.get("cumulative_gpa", "N/A")
    credits = student.get("total_credits_completed", 0)

    completed = [
        f"{c['code']} ({c.get('grade', '?')})"
        for c in student.get("courses_completed", [])
    ]
    current = [c["code"] for c in student.get("current_courses", [])]

    return f"""You are an academic advisor for CU Boulder Computer Science students.

## STUDENT
Name: {name} | Major: {major} | GPA: {gpa} | Credits: {credits}
Completed: {", ".join(completed) or "none"}
Currently enrolled: {", ".join(current) or "none"}

## YOUR JOB
Help {name} with course planning, degree requirements, and professor/section selection.
Focus on Computer Science (CSCI) coursework and the CS degree path.

## TOOL USAGE
- Use `search_course` when the student asks about a specific course or searches by name/topic.
- Use `get_degree_requirements` when the student asks about remaining requirements, graduation, or what to take next.
- Always call a tool before answering questions about specific courses, professors, or degree status.

## RULES
- For CU-specific facts: use tools — never guess course numbers, professor names, or requirements.
- Seat counts are a snapshot, not live — note this if the student asks about availability.
- ✓ means the student currently meets that prerequisite. ✗ means they do not yet.
- Be direct and concise. Cite course codes."""


# ── Agentic loop ──────────────────────────────────────────────────────────────

# Groq uses OpenAI-compatible format: tools need "parameters" not "input_schema"
_GROQ_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        },
    }
    for t in TOOLS
]


_MAX_TOOL_CHARS = 6000   # cap tool results before sending to Groq
_MAX_HISTORY    = 10     # keep last N user/assistant turns to prevent 413


def _trim(result: str) -> str:
    if len(result) > _MAX_TOOL_CHARS:
        return result[:_MAX_TOOL_CHARS] + "\n...(truncated for length)"
    return result


def run_agent(student: dict, messages: list[dict]) -> str:
    """Run Groq with tool calling until a final text reply is produced."""
    system = build_system(student)
    # Trim old history to prevent payload bloat across long conversations
    trimmed = messages[-_MAX_HISTORY:] if len(messages) > _MAX_HISTORY else messages
    loop_messages = [{"role": "system", "content": system}] + trimmed
    headers = {
        "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
        "Content-Type": "application/json",
    }

    while True:
        resp = requests.post(
            GROQ_API_URL,
            headers=headers,
            json={
                "model": GROQ_MODEL,
                "messages": loop_messages,
                "tools": _GROQ_TOOLS,
                "tool_choice": "auto",
                "max_tokens": MAX_TOKENS,
            },
            timeout=60,
        )

        # Groq rate limit — back off and retry
        if resp.status_code == 429:
            try:
                wait = float(re.search(r"try again in ([\d.]+)s", resp.text).group(1)) + 1
            except Exception:
                wait = 15
            time.sleep(wait)
            continue

        if not resp.ok:
            print(f"Groq {resp.status_code}: {resp.text[:500]}")
            # Llama sometimes generates <function=NAME{args}> instead of JSON tool calls.
            # Parse it manually, run the tool, inject the result, and retry.
            if resp.status_code == 400:
                try:
                    failed = resp.json().get("error", {}).get("failed_generation", "")
                    m = re.search(r"<function=(\w+)(\{.*?\})", failed, re.DOTALL)
                    if m:
                        tool_name = m.group(1)
                        try:
                            tool_input = json.loads(m.group(2))
                        except json.JSONDecodeError:
                            tool_input = {}
                        result = _trim(run_tool(tool_name, tool_input, student))
                        loop_messages.append({
                            "role": "user",
                            "content": (
                                f"Here is the data for your answer:\n\n"
                                f"[{tool_name} result]\n{result}\n\n"
                                "Please answer my question using this data."
                            ),
                        })
                        continue
                except Exception as parse_err:
                    print(f"Could not parse failed_generation: {parse_err}")
            resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []

        if tool_calls:
            loop_messages.append(msg)
            for call in tool_calls:
                fn = call["function"]
                tool_input = fn.get("arguments", {})
                if isinstance(tool_input, str):
                    try:
                        tool_input = json.loads(tool_input)
                    except Exception:
                        tool_input = {}
                result = _trim(run_tool(fn["name"], tool_input, student))
                loop_messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result,
                })
        else:
            return msg.get("content") or "I couldn't generate a response."


# ── Schedule / time helpers ───────────────────────────────────────────────────

def _minutes(t: str) -> int:
    t = t.strip().lower()
    m = re.match(r"(\d+):(\d+)(am|pm)?", t)
    if not m:
        return -1
    h, mins, period = int(m.group(1)), int(m.group(2)), m.group(3) or ""
    if period == "pm" and h != 12:
        h += 12
    if period == "am" and h == 12:
        h = 0
    return h * 60 + mins


def _norm_days(raw: str) -> str:
    """Convert day string like 'TTh' or 'MWF' to single-char MTWRF (R=Thursday)."""
    result, i, s = [], 0, raw.upper()
    while i < len(s):
        if s[i:i+2] == "TH":
            result.append("R")
            i += 2
        else:
            if s[i] in "MTWRFS":
                result.append(s[i])
            i += 1
    return "".join(result)


def _days_overlap(d1: str, d2: str) -> bool:
    return bool(set(_norm_days(d1)) & set(_norm_days(d2)) & set("MTWRF"))


def _parse_mtg(m: dict):
    days, start, end = m.get("days", ""), m.get("start_time", ""), m.get("end_time", "")
    if not start:
        pm = re.match(r"([A-Za-z]+)\s+([\d:]+(?:am|pm)?)\s*[-–]\s*([\d:]+(?:am|pm)?)", days, re.IGNORECASE)
        if pm:
            days, start, end = pm.group(1), pm.group(2), pm.group(3)
    return _norm_days(days), _minutes(start), _minutes(end)


def _conflict(s1: dict, s2: dict) -> bool:
    for m1 in s1.get("meetings", []):
        for m2 in s2.get("meetings", []):
            d1, a, b = _parse_mtg(m1)
            d2, c, d = _parse_mtg(m2)
            if _days_overlap(d1, d2) and a >= 0 and c >= 0 and a < d and c < b:
                return True
    return False


def _section_start(s: dict) -> int:
    for m in s.get("meetings", []):
        _, start, _ = _parse_mtg(m)
        if start >= 0:
            return start
    return 9999


def _mtg_to_events(section: dict, course_id: str, color: str) -> list:
    events = []
    stype = section.get("section_type", "LEC")
    for m in section.get("meetings", []):
        days, start, end = _parse_mtg(m)
        if start < 0:
            continue
        for ch in _norm_days(days):
            if ch in "MTWRF":
                events.append({
                    "day": ch, "start_min": start, "end_min": end,
                    "course_id": course_id, "title": section.get("title", course_id),
                    "instructor": section.get("instructor", "TBA"),
                    "location": m.get("location", ""),
                    "color": color, "type": stype,
                    "justin_score": section.get("professor_score"),
                })
    return events


_COLORS = ["#6366f1","#0ea5e9","#10b981","#f59e0b","#ef4444","#8b5cf6","#ec4899","#14b8a6"]


def _build_schedule(selected: list, preference: str) -> dict:
    chosen, failed = [], []
    crn_index = {str(s.get("crn", "")): s for s in DATA.get("schedule", [])}

    for cid in selected:
        sections = get_sections_for_course(cid, DATA)
        lectures = [s for s in sections if s.get("section_type") in ("LEC", "SEM", None, "") and s.get("meetings")]
        if not lectures:
            lectures = [s for s in sections if s.get("meetings")]
        if not lectures:
            failed.append(cid)
            continue

        if preference == "morning":
            f = [s for s in lectures if _section_start(s) < 720]
            lectures = f or lectures
        elif preference == "afternoon":
            f = [s for s in lectures if 720 <= _section_start(s) < 1020]
            lectures = f or lectures

        lectures.sort(key=lambda s: s.get("professor_score") or 0, reverse=True)

        placed = False
        for sec in lectures:
            if any(_conflict(sec, c["section"]) for c in chosen):
                continue
            lab = None
            linked = sec.get("linked_crns", "")
            if linked:
                crns = {x.strip() for x in str(linked).split(",") if x.strip()}
                for crn in crns:
                    lab_sec = crn_index.get(crn)
                    if lab_sec and lab_sec.get("section_type") in ("LAB", "REC"):
                        if not any(_conflict(lab_sec, c["section"]) for c in chosen):
                            if not lab or True:
                                lab = lab_sec
                                break
            chosen.append({"course_id": cid, "section": sec, "lab": lab})
            placed = True
            break

        if not placed:
            failed.append(cid)

    events = []
    for i, item in enumerate(chosen):
        color = _COLORS[i % len(_COLORS)]
        events += _mtg_to_events(item["section"], item["course_id"], color)
        if item["lab"]:
            events += _mtg_to_events(item["lab"], item["course_id"], color)

    courses_out = []
    for i, item in enumerate(chosen):
        s = item["section"]
        lab = item["lab"]
        score = s.get("professor_score")
        courses_out.append({
            "course_id": item["course_id"],
            "title": s.get("title", ""),
            "section": s.get("section", ""),
            "type": s.get("section_type", "LEC"),
            "instructor": s.get("instructor", "TBA"),
            "justin_score": round(score, 1) if score else None,
            "credits": s.get("credits"),
            "meetings": s.get("meetings", []),
            "seats_available": s.get("seats_available", 0),
            "seats_total": s.get("seats_total", 0),
            "color": _COLORS[i % len(_COLORS)],
            "lab": {
                "section": lab.get("section"), "type": lab.get("section_type"),
                "meetings": lab.get("meetings", []),
            } if lab else None,
        })

    return {"preference": preference, "courses": courses_out, "failed": failed, "events": events}


# ── New request models ────────────────────────────────────────────────────────

class RequirementsRequest(BaseModel):
    student_profile: dict

class ScheduleRequest(BaseModel):
    student_profile: dict
    selected_courses: list

class CourseDetailRequest(BaseModel):
    course_id: str
    student_profile: dict


# ── Pure-code endpoints ───────────────────────────────────────────────────────

@app.post("/requirements")
async def requirements_endpoint(req: RequirementsRequest):
    student = req.student_profile
    major = student.get("major", "")
    major_reqs = get_requirements_for_major(major, DATA)
    if not major_reqs:
        raise HTTPException(404, f"Requirements not found for: {major}")
    major_reqs = _inject_dynamic_pools(major_reqs, DATA)

    progress = compute_degree_progress(student, major_reqs)
    completed_codes = {c["code"] for c in student.get("courses_completed", [])}
    in_progress_codes = {c["code"] for c in student.get("current_courses", [])}
    all_taken = completed_codes | in_progress_codes
    catalog = DATA["catalog"]

    sched_idx: dict = {}
    for s in DATA.get("schedule", []):
        sched_idx.setdefault(s.get("course_id", ""), []).append(s)

    # Build course → best Justin Score from professors data (key: no-space uppercase)
    course_best_score: dict = {}
    for prof_courses in DATA.get("professors", {}).values():
        for cid, info in prof_courses.items():
            score = info.get("score")
            if isinstance(score, (int, float)):
                key = cid.replace(" ", "").upper()
                if key not in course_best_score or score > course_best_score[key]:
                    course_best_score[key] = score

    def _best_score(code: str):
        return course_best_score.get(code.replace(" ", "").upper())

    def _sort_key(c):
        status = c.get("status", "remaining")
        prereqs_met = c.get("prereqs_met", True)
        score = c.get("best_score")
        group = 0 if status in ("completed", "in_progress") else (1 if prereqs_met else 2)
        return (group, -(score if score is not None else -1))

    def course_entry(code, title, credits, alternatives):
        all_opts = [code] + alternatives
        taken = next((c for c in all_opts if c in completed_codes), None)
        in_prog = next((c for c in all_opts if c in in_progress_codes), None)
        if taken:
            status, done_with = "completed", taken
        elif in_prog:
            status, done_with = "in_progress", in_prog
        else:
            status, done_with = "remaining", None

        cat = catalog.get(code, {})
        prereq_struct = cat.get("prereqs_structured", {})
        prereqs_met = _prereq_satisfied(prereq_struct, all_taken)

        sects = sched_idx.get(code, [])
        lab_sects = [s for s in sects if s.get("section_type") in ("LAB", "REC")]
        lab = None
        if lab_sects:
            lb = lab_sects[0]
            lb_cat = catalog.get(lb.get("course_id", ""), {})
            lab = {
                "code": lb.get("course_id", ""),
                "title": lb.get("title") or lb_cat.get("title", ""),
                "credits": lb.get("credits") or lb_cat.get("credits"),
                "type": lb.get("section_type", "LAB"),
            }

        return {
            "code": code, "title": title or cat.get("title", ""),
            "credits": credits or cat.get("credits") or (sects[0].get("credits") if sects else None),
            "status": status, "completed_with": done_with,
            "alternatives": alternatives, "prereqs_met": prereqs_met,
            "offered": bool(sects), "lab": lab,
            "best_score": _best_score(code),
        }

    # Split required_courses into display sections by subject
    cs_core, math, ethics, other = [], [], [], []
    for req_course in major_reqs.get("required_courses", []):
        code = req_course.get("code", "")
        alts = req_course.get("alternatives", [])
        title = req_course.get("title", "")
        credits = req_course.get("credits")
        entry = course_entry(code, title, credits, alts)
        entry["waiveable"] = req_course.get("waiveable", False)
        entry["waive_note"] = req_course.get("waive_note", "")
        subj = code.split()[0] if " " in code else ""
        if code in ("CSCI 2824", "CSCI 2820", "CSCI 3022") or subj in ("APPM", "MATH"):
            math.append(entry)
        elif subj == "PHIL":
            ethics.append(entry)
        elif subj == "CSCI":
            cs_core.append(entry)
        else:
            other.append(entry)

    sections_out = [
        {"name": "CS Foundation", "type": "required", "courses": sorted(cs_core, key=_sort_key)},
        {"name": "Mathematics",   "type": "required", "courses": sorted(math, key=_sort_key)},
        {"name": "Logic & Ethics","type": "required", "courses": sorted(ethics, key=_sort_key)},
    ]
    if other:
        sections_out.append({"name": "Other Requirements", "type": "required", "courses": sorted(other, key=_sort_key)})

    # Elective pools
    pool_progress = progress.get("elective_pool_progress", [])
    for i, pool in enumerate(major_reqs.get("electives", {}).get("pools", [])):
        pp = pool_progress[i] if i < len(pool_progress) else {}
        pool_courses = []
        for pc in pool.get("courses", []):
            code = pc.get("code", "")
            taken = code in all_taken
            cat = catalog.get(code, {})
            prereq_struct = cat.get("prereqs_structured", {})
            sects = sched_idx.get(code, [])
            lab_sects = [s for s in sects if s.get("section_type") in ("LAB", "REC")]
            lab = None
            if lab_sects:
                lb = lab_sects[0]
                lab = {"code": lb.get("course_id", ""), "title": lb.get("title", ""),
                       "credits": lb.get("credits"), "type": lb.get("section_type", "LAB")}
            # Best section: highest professor_score among offered lecture sections
            lec_sects = [s for s in sects if s.get("section_type") not in ("LAB", "REC")]
            best_sect = max(
                (s for s in lec_sects if s.get("professor_score") is not None),
                key=lambda s: s["professor_score"], default=None
            ) or (lec_sects[0] if lec_sects else None)
            pool_courses.append({
                "code": code,
                "title": pc.get("title") or cat.get("title", ""),
                "credits": cat.get("credits") or (sects[0].get("credits") if sects else None),
                "status": "completed" if taken else "remaining",
                "prereqs_met": True if taken else _prereq_satisfied(prereq_struct, all_taken),
                "offered": bool(sects), "lab": lab,
                "best_score": _best_score(code),
                "instructor": best_sect.get("instructor", "") if best_sect else "",
                "sequence": pc.get("sequence", ""),
            })
        is_compact = pool.get("compact", False)
        pool_courses.sort(key=_sort_key)
        # For compact pools (e.g. H&SS with 700+ courses), only return taken courses
        # to avoid sending massive payloads to the frontend
        display_courses = (
            [c for c in pool_courses if c["status"] == "completed"]
            if is_compact else pool_courses
        )
        taken_credits = sum(
            (c.get("credits") or catalog.get(c["code"], {}).get("credits") or 0)
            for c in pool_courses if c["status"] == "completed"
        )
        sections_out.append({
            "name": pool.get("name", "Elective Pool"), "type": "pool",
            "required_count": pool.get("required_count"),
            "required_credits": pool.get("required_credits"),
            "completed_count": len(pp.get("taken", [])),
            "completed_credits": taken_credits,
            "total_pool_size": len(pool_courses),
            "compact": is_compact,
            "note": pool.get("note", ""),
            "courses": display_courses,
        })

    return {
        "major": major,
        "credits_earned": progress.get("total_credits_earned", 0),
        "credits_needed": progress.get("total_credits_needed"),
        "credits_remaining": progress.get("credits_remaining"),
        "sections": sections_out,
    }


@app.post("/build-schedule")
async def build_schedule_endpoint(req: ScheduleRequest):
    selected = req.selected_courses
    if not selected:
        raise HTTPException(400, "No courses selected")
    return {
        "schedules": [
            _build_schedule(selected, "any"),
            _build_schedule(selected, "morning"),
            _build_schedule(selected, "afternoon"),
        ]
    }


@app.post("/course-detail")
async def course_detail_endpoint(req: CourseDetailRequest):
    student = req.student_profile
    completed_codes = {c["code"] for c in student.get("courses_completed", [])}
    in_progress_codes = {c["code"] for c in student.get("current_courses", [])}
    all_taken = completed_codes | in_progress_codes
    catalog = DATA["catalog"]

    # Normalize course_id
    query = req.course_id.strip().upper()
    course = catalog.get(req.course_id)
    if not course:
        for code, c in catalog.items():
            if code.replace(" ", "").upper() == query.replace(" ", ""):
                course = c
                break
    if not course:
        raise HTTPException(404, f"Course not found: {req.course_id}")

    code = course.get("code", req.course_id)
    prereq_struct = course.get("prereqs_structured", {})
    all_sections = get_sections_for_course(code, DATA)
    lec_sections = [s for s in all_sections if s.get("section_type") in ("LEC", "SEM", None, "")]
    lab_sections = [s for s in all_sections if s.get("section_type") in ("LAB", "REC")]
    # For courses with no LEC/SEM (e.g. standalone lab courses with MLS sections),
    # fall back to treating all non-LAB/REC sections as the "main" sections
    if not lec_sections:
        lec_sections = [s for s in all_sections if s.get("section_type") not in ("LAB", "REC")]

    # Build prereq display
    prereq_display = []
    any_of = prereq_struct.get("any_of", [])
    all_of = prereq_struct.get("all_of", [])
    for c in all_of:
        prereq_display.append({"code": c, "met": c in all_taken, "type": "required"})
    if any_of:
        prereq_display.append({
            "options": any_of, "met": any(c in all_taken for c in any_of), "type": "any_of"
        })

    subject = code.split()[0] if " " in code else code[:4]
    sections_out = []
    for s in lec_sections:
        score = s.get("professor_score")
        linked_crns = {x.strip() for x in str(s.get("linked_crns", "")).split(",") if x.strip()}
        linked_labs = [lb for lb in lab_sections if str(lb.get("crn", "")) in linked_crns] or lab_sections
        crn_str = str(s.get("crn", ""))
        snapshot_seats = {
            "seats_available": s.get("seats_available", 0),
            "seats_total": s.get("seats_total", 0),
            "waitlist": s.get("waitlist", 0),
        }
        seats, seats_live = _get_seats(crn_str, subject, snapshot_seats)
        sections_out.append({
            "crn": s.get("crn"), "section": s.get("section"),
            "type": s.get("section_type", "LEC"),
            "instructor": s.get("instructor", "TBA"),
            "justin_score": round(score, 1) if score else None,
            "cs_avg_score": s.get("professor_cs_avg_score"),
            "cs_avg_count": s.get("professor_cs_avg_count", 0),
            "fcq_metrics": s.get("professor_metrics", {}),
            "resp_rate": s.get("professor_avg_resp_rate"),
            "meetings": s.get("meetings", []),
            "seats_available": seats["seats_available"],
            "seats_total": seats["seats_total"],
            "waitlist": seats["waitlist"],
            "seats_live": seats_live,
            "labs": [{
                "crn": lb.get("crn"), "section": lb.get("section"),
                "type": lb.get("section_type"), "meetings": lb.get("meetings", []),
                "seats_available": lb.get("seats_available", 0),
                "seats_total": lb.get("seats_total", 0),
            } for lb in linked_labs],
        })

    return {
        "code": code, "title": course.get("title", ""),
        "credits": course.get("credits"),
        "description": course.get("description", ""),
        "prereqs_text": course.get("prereqs_text", ""),
        "prereqs_met": _prereq_satisfied(prereq_struct, all_taken),
        "prereqs": prereq_display,
        "sections": sections_out,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

class AuditRequest(BaseModel):
    text: str

@app.post("/upload-audit")
async def upload_audit(req: AuditRequest):
    if not req.text or len(req.text.strip()) < 100:
        raise HTTPException(400, "Audit text too short — paste the full degree audit")
    profile = parse_audit(req.text)
    return {"student_profile": profile}


@app.post("/upload-transcript")
async def upload_transcript(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(400, "Only PDF files accepted")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        profile = parse_transcript(tmp_path)
    finally:
        os.unlink(tmp_path)
    return {"student_profile": profile}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    messages = req.history + [{"role": "user", "content": req.message}]
    reply = run_agent(req.student_profile, messages)
    updated_history = messages + [{"role": "assistant", "content": reply}]
    return ChatResponse(reply=reply, updated_history=updated_history)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.chat:app", host="0.0.0.0", port=8001, reload=True)
