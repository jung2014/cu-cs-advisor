# ============================================================
# system_prompt.py
# Builds the system prompt injected into every Claude API call
# ============================================================

import json


def _fmt_list(items: list, prefix: str = "  - ") -> str:
    if not items:
        return "  (none)"
    return "\n".join(f"{prefix}{item}" for item in items)


def build_system_prompt(context: dict) -> str:
    student = context["student"]
    major_reqs = context.get("major_requirements") or {}
    eligible = context.get("eligible_courses", [])
    progress = context.get("degree_progress") or {}
    current_semester = context.get("current_semester_label", "")
    schedule_available = context.get("schedule_available", False)

    name = student.get("name", "the student")
    major = student.get("major", "Unknown")
    gpa = student.get("cumulative_gpa", "N/A")
    credits_earned = student.get("total_credits_completed", 0)

    # Degree progress block
    credits_needed = progress.get("total_credits_needed")
    credits_remaining = progress.get("credits_remaining")
    remaining_required = progress.get("remaining_required", [])
    completed_required = progress.get("completed_required", [])

    progress_lines = []
    if credits_needed:
        progress_lines.append(
            f"Credits: {credits_earned}/{credits_needed} earned "
            f"({credits_remaining} remaining)"
        )
    else:
        progress_lines.append(f"Credits earned: {credits_earned}")

    progress_lines.append(
        f"Required courses completed ({len(completed_required)}): "
        + (", ".join(completed_required[:20]) if completed_required else "none on record")
    )
    progress_lines.append(
        f"Required courses still needed ({len(remaining_required)}): "
        + (", ".join(remaining_required[:30]) if remaining_required else "none — may be complete!")
    )

    pool_progress = progress.get("elective_pool_progress", [])
    for pool in pool_progress:
        req_str = ""
        if pool.get("required_credits"):
            req_str = f"{pool['required_credits']} credits"
        elif pool.get("required_count"):
            req_str = f"{pool['required_count']} courses"
        taken_str = ", ".join(pool.get("taken", [])) or "none yet"
        progress_lines.append(
            f"Elective pool '{pool['name']}' ({req_str}): taken — {taken_str}"
        )

    schedule_note = (
        f"Current semester: {current_semester}"
        if schedule_available
        else "Note: Live schedule data is not loaded. Cannot show section times or seat availability."
    )

    return f"""# ACADEMIC ADVISOR — CU BOULDER

You are an academic advisor chatbot for CU Boulder students. Your job is to help {name} with:
- Course planning and sequencing
- Understanding degree requirements
- Professor and section selection
- GPA impact and academic standing
- Transfer credit and prerequisite questions

## GROUND RULES
- For **specific CU facts** (requirements, professor scores, seat counts): ONLY use the data provided below.
- For **general academic guidance** (study strategies, career paths, explaining concepts): use your knowledge freely.
- When specific data is missing, say so clearly and offer what general guidance you can.
- Always be honest about uncertainty. Never fabricate course numbers, instructor names, or requirements.

## STUDENT PROFILE
Name: {name}
Major: {major}
Cumulative GPA: {gpa}
Credits Completed: {credits_earned}

### Completed Courses
{_fmt_list([f"{c['code']} - {c.get('title','?')} ({c.get('grade','?')})" for c in student.get("courses_completed", [])])}

### Currently Enrolled
{_fmt_list([f"{c['code']} - {c.get('title','?')}" for c in student.get("current_courses", [])])}

## DEGREE PROGRESS ({major})
{chr(10).join(progress_lines)}

## ELIGIBLE COURSES (prereqs satisfied, not yet taken)
{_fmt_list(eligible[:60])}
{"  ...(truncated)" if len(eligible) > 60 else ""}

## {schedule_note}

## RESPONSE STYLE
- Be direct and specific. Cite course codes.
- If asked to recommend courses, rank by prerequisite readiness and match to stated goals.
- If asked about a professor, use only the score data provided when a course section is retrieved.
- Keep responses focused — avoid walls of text unless the student asks for detail."""


def build_course_context(course_id: str, sections: list[dict]) -> str:
    """
    Format section data for injection when a student asks about a specific course.
    """
    if not sections:
        return f"\n=== {course_id} ===\nNo sections found in current schedule data.\n"

    lines = [f"\n=== SECTIONS FOR {course_id} ==="]
    for s in sections:
        score = s.get("professor_score")
        score_str = f"{score:.1f}/100" if score is not None else "No score data"
        meetings = ", ".join(
            f"{m['days']} {m['start_time']}-{m['end_time']}"
            for m in s.get("meetings", [])
        ) or "TBD"
        seats = s.get("seats_available")
        seats_total = s.get("seats_total")
        seats_str = f"{seats}/{seats_total}" if seats is not None else "N/A"

        lines.append(
            f"Section {s.get('section')} | {s.get('instructor','TBA')} | "
            f"Score: {score_str} | {meetings} | "
            f"Seats: {seats_str} | Waitlist: {s.get('waitlist', 0)}"
        )
        prereqs = s.get("prereqs", "")
        if prereqs:
            lines.append(f"  Prereqs: {prereqs}")
        metrics = s.get("professor_metrics", {})
        if metrics:
            top = sorted(metrics.items(), key=lambda x: x[1] or 0, reverse=True)[:5]
            lines.append(f"  Top metrics: {', '.join(f'{k}={v}' for k,v in top)}")

    return "\n".join(lines)
