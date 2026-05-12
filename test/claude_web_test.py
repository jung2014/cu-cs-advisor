#!/usr/bin/env python3
"""
claude_web_test.py
Generates a full advisor prompt you can paste into claude.ai to test the chatbot
without needing the API or a frontend.

Usage:
  python3 claude_web_test.py                  # uses built-in mock student
  python3 claude_web_test.py transcript.pdf   # uses your real transcript
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.data_loader import load_all_data, build_advisor_context
from backend.system_prompt import build_system_prompt, build_course_context
from backend.transcript import parse_transcript


# ── Mock student (used when no transcript is provided) ────────────────────────

MOCK_STUDENT = {
    "name": "Alex Johnson",
    "student_id": "123456789",
    "cumulative_gpa": 3.2,
    "total_credits_completed": 60,
    "transfer_credits": [],
    "semesters": [],
    "courses_completed": [
        {"code": "CSCI 1300", "title": "CS 1: Starting Computing",       "grade": "A",  "credits": 4},
        {"code": "CSCI 2270", "title": "Data Structures",                 "grade": "B+", "credits": 4},
        {"code": "MATH 1300", "title": "Calculus 1",                      "grade": "A-", "credits": 4},
        {"code": "MATH 2300", "title": "Calculus 2",                      "grade": "B",  "credits": 4},
        {"code": "CSCI 2400", "title": "Computer Systems",                "grade": "B+", "credits": 4},
        {"code": "CSCI 3104", "title": "Algorithms",                      "grade": "A",  "credits": 4},
    ],
    "current_courses": [
        {"code": "CSCI 3155", "title": "Principles of Programming Languages", "credits": 4},
        {"code": "MATH 2400", "title": "Calculus 3",                          "credits": 4},
    ],
    "major": "Computer Science - Bachelor of Science (BSCS)",
}


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_test_prompt(student: dict, question: str, course_id: str = None, inject_schedule: bool = False) -> str:
    DATA = load_all_data()
    context = build_advisor_context(student, DATA)
    system = build_system_prompt(context)

    from backend.data_loader import get_sections_for_course

    course_block = ""

    if course_id:
        # Single course lookup
        sections = get_sections_for_course(course_id, DATA)
        course_block = build_course_context(course_id, sections)

    elif inject_schedule:
        # Inject sections for all remaining required courses that have schedule data
        remaining = context.get("degree_progress", {}).get("remaining_required", [])
        blocks = []
        injected = 0
        for code in remaining:
            sections = get_sections_for_course(code, DATA)
            if sections:
                blocks.append(build_course_context(code, sections))
                injected += 1
            if injected >= 20:   # cap to avoid massive prompts
                break
        course_block = "\n".join(blocks)
        if not course_block:
            course_block = "\n(No schedule sections found for remaining required courses)"

    user_message = question + course_block

    return f"""{system}

---
STUDENT QUESTION: {user_message}
"""


def print_prompt(label: str, student: dict, question: str, course_id: str = None, inject_schedule: bool = False):
    print(f"\n{'='*70}")
    print(f"SCENARIO: {label}")
    print('='*70)
    print(build_test_prompt(student, question, course_id, inject_schedule))


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Load student from transcript PDF if provided, else use mock
    if len(sys.argv) > 1 and sys.argv[1].endswith(".pdf"):
        print(f"Parsing transcript: {sys.argv[1]}")
        student = parse_transcript(sys.argv[1])
        print(f"Loaded: {student['name']} — {student['major']}\n")
    else:
        student = MOCK_STUDENT
        print("Using mock student: Alex Johnson (CS major, 60 credits)\n")

    # Pick a scenario to test — comment/uncomment as needed
    print_prompt(
        "Build my Fall 2026 schedule with best professors",
        student,
        "Look at the class schedules and build out my Fall 2026 schedule based on the best teachers.",
        inject_schedule=True,
    )

    # print_prompt(
    #     "Degree progress check",
    #     student,
    #     "How many credits do I have left and what required courses am I still missing?",
    # )

    # print_prompt(
    #     "Course section lookup",
    #     student,
    #     "Who teaches CSCI 3308 this semester and when does it meet?",
    #     course_id="CSCI 3308",
    # )

    # print_prompt(
    #     "Professor comparison",
    #     student,
    #     "Which section of CSCI 3104 has the best professor?",
    #     course_id="CSCI 3104",
    # )

    print("\n--- Copy everything above the dashes into claude.ai ---")
