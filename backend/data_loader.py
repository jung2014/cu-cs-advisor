# ============================================================
# data_loader.py
# Loads all data sources and builds advisor context
# ============================================================

import json
import re
import sys
import os
from typing import Optional, Union

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import STATIC, SEMESTER


# ── File loading ──────────────────────────────────────────────────────────────

def _load_json_safe(path: str, default) -> Union[dict, list]:
    """Load JSON, returning default if file is missing or empty."""
    try:
        with open(path) as f:
            content = f.read().strip()
            if not content:
                return default
            return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_all_data() -> dict:
    """Load all static and semester data. Missing/empty files return safe defaults."""
    return {
        "requirements": _load_json_safe(STATIC["requirements"], {}),
        "catalog":      _load_json_safe(STATIC["catalog"], {}),
        "schedule":     _load_json_safe(SEMESTER["schedule"], []),
        "professors":   _load_json_safe(SEMESTER["professors"], {}),
    }


# ── Course schedule helpers ───────────────────────────────────────────────────

def get_sections_for_course(course_id: str, data: dict) -> list[dict]:
    """
    Return all sections of a course with professor scores injected.
    course_id example: "CSCI 3104"
    """
    course_id_clean = course_id.replace(" ", "").upper()
    sections = [
        s for s in data["schedule"]
        if s.get("course_id", "").replace(" ", "").upper() == course_id_clean
    ]

    professors = data["professors"]
    for section in sections:
        instructor = section.get("instructor", "")
        parts = instructor.split()
        last_name = parts[-1] if parts else ""

        # Try "First Last" → "Last, First" conversion
        prof_data = professors.get(instructor)
        if prof_data is None and len(parts) >= 2:
            last_first = f"{last_name}, {' '.join(parts[:-1])}"
            prof_data = professors.get(last_first)

        # If found but doesn't have this course's data, the schedule may use
        # initials (e.g. "CJ Herman") while FCQ uses full name ("Carey Jay Herman").
        # Fall back: find any entry with the same last name that has this course.
        if (prof_data is None or course_id_clean not in prof_data) and last_name:
            for name, data_entry in professors.items():
                name_parts = name.split(",")
                entry_last = name_parts[0].strip() if name_parts else ""
                if entry_last.lower() == last_name.lower() and course_id_clean in data_entry:
                    prof_data = data_entry
                    break

        prof_data = prof_data or {}
        course_score_data = prof_data.get(course_id_clean, {})
        section["professor_score"] = course_score_data.get("score")
        section["professor_metrics"] = course_score_data.get("metrics", {})
        section["professor_avg_resp_rate"] = course_score_data.get("avg_resp_rate")

        # If no score for this specific course, compute average across their CS courses
        if section["professor_score"] is None and prof_data:
            cs_scores = [
                v["score"] for k, v in prof_data.items()
                if k.startswith("CSCI") and isinstance(v.get("score"), (int, float))
            ]
            if cs_scores:
                section["professor_cs_avg_score"] = round(sum(cs_scores) / len(cs_scores), 1)
                section["professor_cs_avg_count"] = len(cs_scores)
            else:
                section["professor_cs_avg_score"] = None
                section["professor_cs_avg_count"] = 0
        else:
            section["professor_cs_avg_score"] = None
            section["professor_cs_avg_count"] = 0

    sections.sort(key=lambda s: s.get("professor_score") or 0, reverse=True)
    return sections


# ── Major requirements helpers ────────────────────────────────────────────────

def get_requirements_for_major(major_name: str, data: dict) -> Optional[dict]:
    """
    Fuzzy-match a major name against the requirements database.
    Prefers the most specific (shortest) matching key to avoid catch-all entries.
    """
    reqs = data["requirements"]
    if not reqs or not major_name:
        return None

    # Normalize: strip zero-width spaces and extra whitespace
    def normalize(s):
        return re.sub(r"[\u200b\u00a0\s]+", " ", s).strip().lower()

    major_norm = normalize(major_name)
    candidates = []

    for name, info in reqs.items():
        name_norm = normalize(name)
        # Check if all words in the query appear in the key
        if all(token in name_norm for token in major_norm.split()):
            candidates.append((name, info))

    if not candidates:
        return None

    # Prefer the shortest matching name (most specific without extras)
    candidates.sort(key=lambda x: len(x[0]))
    return candidates[0][1]


# ── Prerequisite checking ─────────────────────────────────────────────────────

def _prereq_satisfied(prereq_structure: dict, all_taken: set) -> bool:
    """
    Check whether a structured prereq is satisfied by all_taken course codes.
    Uses the any_of / all_of structure built by course_catalog_scraper.
    Falls back to True when structure is missing (avoid false negatives).
    """
    if not prereq_structure:
        return True

    any_of = prereq_structure.get("any_of", [])
    all_of = prereq_structure.get("all_of", [])

    # No explicit requirements → satisfied
    if not any_of and not all_of:
        return True

    # All "all_of" courses must be taken
    for code in all_of:
        if code not in all_taken:
            return False

    # At least one "any_of" course must be taken (if any specified)
    if any_of and not any(c in all_taken for c in any_of):
        return False

    return True


def get_courses_student_can_take(student: dict, data: dict) -> list[str]:
    """
    Return course IDs the student is eligible to take based on completed prereqs.
    Uses structured prereqs from catalog when available; falls back to text-based check.
    """
    completed = {c["code"] for c in student.get("courses_completed", [])}
    in_progress = {c["code"] for c in student.get("current_courses", [])}
    all_taken = completed | in_progress

    eligible = []
    for code, course in data["catalog"].items():
        if code in all_taken:
            continue

        prereq_struct = course.get("prereqs_structured", {})
        if _prereq_satisfied(prereq_struct, all_taken):
            eligible.append(code)

    return eligible


# ── Degree progress ───────────────────────────────────────────────────────────

def compute_degree_progress(student: dict, major_reqs: Optional[dict]) -> dict:
    """
    Compare completed courses against major requirements.
    Returns a progress summary the advisor can present to the student.
    """
    if not major_reqs:
        return {}

    completed_codes = {c["code"] for c in student.get("courses_completed", [])}
    in_progress_codes = {c["code"] for c in student.get("current_courses", [])}

    # Degree audit matched completions: e.g. MATH1300 → APPM1350 means
    # APPM1350 is also satisfied even though it wasn't literally taken.
    for actual, canonical in student.get("matched_completions", {}).items():
        if actual in completed_codes:
            completed_codes.add(canonical)
        elif actual in in_progress_codes:
            in_progress_codes.add(canonical)

    all_taken = completed_codes | in_progress_codes

    required = major_reqs.get("required_courses", [])
    total_credits_needed = major_reqs.get("total_credits")
    total_credits_earned = student.get("total_credits_completed", 0)

    completed_required = []
    remaining_required = []

    for course in required:
        code = course.get("code", "")
        alternatives = course.get("alternatives", [])
        all_options = [code] + alternatives

        taken = next((c for c in all_options if c in completed_codes), None)
        in_prog = next((c for c in all_options if c in in_progress_codes), None)

        if taken:
            completed_required.append(taken)
        elif in_prog:
            completed_required.append(f"{in_prog} (in progress)")
        else:
            remaining_required.append({
                "code": code,
                "title": course.get("title", ""),
                "credits": course.get("credits"),
                "alternatives": alternatives,
            })

    elective_pools = major_reqs.get("electives", {}).get("pools", [])
    pool_progress = []
    for pool in elective_pools:
        pool_courses = [c["code"] for c in pool.get("courses", [])]
        taken_from_pool = [c for c in pool_courses if c in all_taken]
        pool_progress.append({
            "name": pool.get("name", "Elective Pool"),
            "required_credits": pool.get("required_credits"),
            "required_count": pool.get("required_count"),
            "taken": taken_from_pool,
            "remaining": [c for c in pool_courses if c not in all_taken],
        })

    return {
        "completed_required": completed_required,
        "remaining_required": remaining_required,
        "elective_pool_progress": pool_progress,
        "total_credits_earned": total_credits_earned,
        "total_credits_needed": total_credits_needed,
        "credits_remaining": (
            total_credits_needed - total_credits_earned
            if total_credits_needed
            else None
        ),
    }


# ── Dynamic pool injection ────────────────────────────────────────────────────

def _inject_dynamic_pools(major_reqs: Optional[dict], data: dict) -> Optional[dict]:
    """
    Synthesize elective pools from schedule data rather than maintaining static lists.
    Currently injects the CSCI Upper Division Electives pool (3000-4999) based on
    what is actually offered this semester.
    """
    if not major_reqs:
        return major_reqs

    # Collect all codes already tracked by required_courses + existing static pools
    tracked: set = set()
    for rc in major_reqs.get("required_courses", []):
        tracked.add(rc["code"].replace(" ", "").upper())
        for alt in rc.get("alternatives", []):
            tracked.add(alt.replace(" ", "").upper())
    for pool in major_reqs.get("electives", {}).get("pools", []):
        for pc in pool.get("courses", []):
            tracked.add(pc["code"].replace(" ", "").upper())

    # Build CSCI 3000-4999 pool from offered schedule sections.
    # Special topics courses (different title per section) get one entry per unique topic.
    seen: set = set()
    upper_courses = []
    for s in data.get("schedule", []):
        cid = s.get("course_id", "")
        num_str = s.get("course_number", "")
        if not cid.startswith("CSCI ") or not num_str.isdigit():
            continue
        if not (3000 <= int(num_str) <= 4999):
            continue
        if s.get("section_type") in ("LAB", "REC"):
            continue
        clean = cid.replace(" ", "").upper()
        if clean in tracked:
            continue
        title = s.get("title", "")
        is_special = "special topics" in title.lower() or "selected topics" in title.lower()
        dedup_key = (clean, title) if is_special else clean
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        upper_courses.append({"code": cid, "title": title})

    if not upper_courses:
        return major_reqs

    upper_courses.sort(key=lambda c: c["code"])
    upper_pool = {
        "name": "CSCI Upper Division Electives",
        "required_count": None,
        "required_credits": None,
        "note": (
            "Courses from the CS Core list or this list to bring total CS credit hours "
            "to 58 or more. Any CSCI 3000–4999 or 5000–5999 may apply with advisor approval."
        ),
        "courses": upper_courses,
    }
    return {
        **major_reqs,
        "electives": {
            **major_reqs.get("electives", {}),
            "pools": major_reqs.get("electives", {}).get("pools", []) + [upper_pool],
        },
    }


# ── Context builder ───────────────────────────────────────────────────────────

def build_advisor_context(student: dict, data: dict) -> dict:
    """
    Build the full context dict passed to the system prompt.
    """
    major = student.get("major", "")
    major_reqs = get_requirements_for_major(major, data)
    major_reqs = _inject_dynamic_pools(major_reqs, data)
    eligible = get_courses_student_can_take(student, data)
    progress = compute_degree_progress(student, major_reqs)

    schedule = data.get("schedule", [])
    current_semester_label = (
        schedule[0].get("term") if schedule else ""
    )

    return {
        "student": student,
        "major_requirements": major_reqs,
        "eligible_courses": eligible,
        "degree_progress": progress,
        "current_semester_label": current_semester_label,
        "schedule_available": bool(schedule),
    }
