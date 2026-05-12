# ============================================================
# professor_score.py
# Produces one score per (professor, course) pair from FCQ data
#
# WEIGHTS — designed from a student's perspective:
#   Goal = maximize chance of getting a good grade
#
# Response Rate: heavily weighted — low response rate means the
#   score is unreliable. We penalize low-confidence data.
#
# Feedback:     how much the prof helps you improve your work
# Grading:      explains grading criteria clearly = fewer surprises
# Questions:    available to help = you can get unstuck
# Challenge:    pushes you to actually learn = better retention
# Reflect:      encourages deep thinking = better exam prep
# Connect:      real-world connection = understanding, not memorization
# Discuss:      can ask questions in class = clarification
# Tech:         effective use of tools = better learning experience
# Interact/Collab/Contrib/Eval/Synth/Diverse/Respect/Creative:
#   lower weight — less directly tied to grade outcomes
# ============================================================

import json
from typing import Optional
import openpyxl
from collections import defaultdict

WEIGHTS = {
    # High impact on grade outcomes
    "Feedback":  0.18,   # helps you improve = better grades
    "Grading":   0.16,   # clear criteria = no surprise deductions
    "Questions": 0.14,   # accessible = you can get help
    "Challenge": 0.12,   # pushes learning = exam readiness
    "Reflect":   0.10,   # deeper understanding = retention

    # Medium impact
    "Connect":   0.08,   # real world = understanding over memorization
    "Discuss":   0.07,   # ask questions in class = clarification
    "Tech":      0.05,   # effective tools = clearer material

    # Lower impact (engagement-oriented, not grade-oriented)
    "Interact":  0.02,
    "Collab":    0.02,
    "Contrib":   0.02,
    "Eval":      0.01,
    "Synth":     0.01,
    "Diverse":   0.01,
    "Respect":   0.01,
    "Creative":  0.00,   # creative thinking less tied to grade outcomes
}

# Minimum response rate to trust a score (0.0 - 1.0)
MIN_RESPONSE_RATE = 0.30

# Response rate confidence multiplier
# Score is scaled by: min(resp_rate / CONFIDENCE_THRESHOLD, 1.0)
CONFIDENCE_THRESHOLD = 0.60


def compute_score(row: dict) -> Optional[float]:
    """
    Compute a single 0-100 score for a (professor, course) FCQ row.
    Returns None if response rate is too low to be meaningful.
    """
    resp_rate = row.get("Resp Rate")
    if resp_rate is None:
        return None

    # Convert "=O8/N8" formula entries to None
    if isinstance(resp_rate, str):
        return None

    if resp_rate < MIN_RESPONSE_RATE:
        return None

    # Confidence multiplier — penalize low response rates
    confidence = min(resp_rate / CONFIDENCE_THRESHOLD, 1.0)

    weighted_sum = 0.0
    total_weight = 0.0

    for metric, weight in WEIGHTS.items():
        val = row.get(metric)
        if val is not None and isinstance(val, (int, float)):
            # FCQ scale is 1-5, normalize to 0-1
            normalized = (val - 1) / 4.0
            weighted_sum += normalized * weight
            total_weight += weight

    if total_weight == 0:
        return None

    raw_score = weighted_sum / total_weight
    final_score = raw_score * confidence * 100  # 0-100

    return round(final_score, 2)


def load_fcq_from_xlsx(path: str) -> list[dict]:
    """Parse FCQ Results sheet into list of row dicts."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["FCQ Results"]

    rows = list(ws.iter_rows(values_only=True))

    # Find header row (row with "Term" in first cell)
    header_row = None
    data_start = None
    for i, row in enumerate(rows):
        if row[0] == "Term":
            header_row = list(row)
            data_start = i + 1
            break

    if not header_row:
        raise ValueError("Could not find header row in FCQ Results sheet")

    result = []
    for row in rows[data_start:]:
        if not any(v is not None for v in row):
            continue
        result.append(dict(zip(header_row, row)))

    return result


def build_professor_scores(path: str) -> dict:
    """
    Returns a nested dict:
    {
      "professor_name": {
        "CSCI1300": {
          "score": 82.4,
          "num_sections": 3,
          "avg_resp_rate": 0.71,
          "metrics": { "Feedback": 4.2, "Grading": 3.9, ... },
          "course_title": "CS 1: Starting Computing"
        },
        ...
      },
      ...
    }
    """
    rows = load_fcq_from_xlsx(path)

    # Group by (instructor, subject+course)
    grouped = defaultdict(list)
    for row in rows:
        instructor = row.get("Instructor Name")
        subject = row.get("Sbjct")
        course = row.get("Crse")
        if not instructor or not subject or not course:
            continue
        key = (instructor, f"{subject}{course}")
        grouped[key].append(row)

    result = defaultdict(dict)

    for (instructor, course_id), sections in grouped.items():
        scores = []
        resp_rates = []
        metric_totals = defaultdict(list)
        course_title = None

        for row in sections:
            s = compute_score(row)
            if s is not None:
                scores.append(s)

            rr = row.get("Resp Rate")
            if isinstance(rr, (int, float)):
                resp_rates.append(rr)

            if not course_title:
                course_title = row.get("Crse Title")

            for metric in WEIGHTS:
                val = row.get(metric)
                if isinstance(val, (int, float)):
                    metric_totals[metric].append(val)

        if not scores:
            continue

        result[instructor][course_id] = {
            "score": round(sum(scores) / len(scores), 2),
            "num_sections": len(sections),
            "avg_resp_rate": round(sum(resp_rates) / len(resp_rates), 3) if resp_rates else None,
            "course_title": course_title,
            "metrics": {
                m: round(sum(v) / len(v), 2)
                for m, v in metric_totals.items() if v
            }
        }

    return dict(result)


def save_professor_scores(xlsx_path: str, output_path: str):
    """Parse FCQ xlsx and write professor scores JSON for a semester folder."""
    scores = build_professor_scores(xlsx_path)
    with open(output_path, "w") as f:
        json.dump(scores, f, indent=2)
    print(f"Saved {len(scores)} professor entries to {output_path}")


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from backend.config import DATA_BASE, CURRENT_SEMESTER
    save_professor_scores(
        os.path.join(DATA_BASE, "fcq_raw.xlsx"),
        os.path.join(DATA_BASE, "semester", CURRENT_SEMESTER, "professors.json"),
    )
