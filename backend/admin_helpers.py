"""Pure transformations used to build the admin dashboard."""

from __future__ import annotations

import json
from typing import Any


def parse_stored_analysis_json(raw_value: Any) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return raw_value
    if not raw_value:
        return {}
    try:
        parsed = json.loads(str(raw_value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def clamp_admin_score(value: Any) -> float:
    try:
        return round(max(0.0, min(100.0, float(value or 0))), 2)
    except (TypeError, ValueError):
        return 0.0


def estimate_after_recommendation_score(base_score: Any, analysis: dict[str, Any]) -> float:
    score = clamp_admin_score(base_score or analysis.get("matching_percentage"))
    recommendation_module = (
        analysis.get("recommendation_module")
        if isinstance(analysis.get("recommendation_module"), dict)
        else {}
    )
    actions = [
        action
        for action in list(recommendation_module.get("priority_actions") or [])
        if isinstance(action, dict)
    ]
    action_gains = {
        "role_fit": 3.0,
        "keyword_gap": 8.0,
        "evidence_gap": 7.0,
        "experience": 6.0,
        "ats_quality": 5.0,
        "education": 4.0,
        "impact": 4.0,
    }
    gain = 0.0
    seen_categories: set[str] = set()
    for action in actions:
        category = str(action.get("category") or "").strip().lower()
        if category and category not in seen_categories:
            gain += action_gains.get(category, 3.0)
            seen_categories.add(category)

    job_analysis = analysis.get("job_description_analysis") if isinstance(analysis.get("job_description_analysis"), dict) else {}
    missing_keywords = list(job_analysis.get("missing_keywords") or [])
    compatibility = analysis.get("compatibility") if isinstance(analysis.get("compatibility"), dict) else {}

    if missing_keywords:
        gain += min(8.0, len(missing_keywords) * 1.1)
    if clamp_admin_score(compatibility.get("keyword_match")) < 45:
        gain += 4.0
    if clamp_admin_score(compatibility.get("role_evidence")) < 60:
        gain += 4.0
    if clamp_admin_score(compatibility.get("resume_quality")) < 75:
        gain += 3.0

    if actions and gain < 6.0:
        gain = 6.0
    gain = min(24.0, gain)
    return round(min(100.0, score + gain), 2)


def isoformat_datetime(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None
