from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from data_cleaning import clean_job_text, normalize_match_key


ROLE_FAMILY_RULES = (
    ("Database", ("database", "sql", "oracle")),
    ("Data and AI", ("data", "artificial intelligence", "ai", "machine learning", "business intelligence", "analytics")),
    ("Cybersecurity", ("security", "cyber", "forensic", "safety")),
    ("Web and UX", ("web", "ux", "graphic", "designer", "e-commerce")),
    ("Networking and Infrastructure", ("network", "infrastructure", "cloud", "support", "system", "technical specialist")),
    ("Software Development", ("software", "developer", "programmer", "programming", "java", ".net", "php", "application")),
    ("ICT Management", ("manager", "coordinator", "consultant", "business process", "business development")),
    ("Computer Operations", ("operator", "technician", "assistant", "fitter", "machinist", "printer", "clerk")),
)
DEFAULT_TOKEN_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "candidate",
    "computer",
    "for",
    "from",
    "in",
    "information",
    "is",
    "it",
    "masco",
    "of",
    "on",
    "or",
    "role",
    "roles",
    "skill",
    "skills",
    "system",
    "systems",
    "technology",
    "the",
    "to",
    "using",
    "with",
}


def safe_float(value: float, digits: int = 2) -> float:
    return round(float(value or 0), digits)


def percentage(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return safe_float((part / total) * 100)


def extract_text_tokens(text: str, stopwords: set[str] | None = None) -> list[str]:
    active_stopwords = DEFAULT_TOKEN_STOPWORDS | {word.lower() for word in (stopwords or set())}
    tokens = re.findall(r"[a-zA-Z][a-zA-Z+.#/-]{1,}", clean_job_text(text).lower())
    cleaned_tokens = []
    for token in tokens:
        token = token.strip(".,;:!?()[]{}\"'")
        token = token.replace("node.js", "node").replace("react.js", "react")
        if token == "net":
            token = "dotnet"
        if len(token) <= 2 or token in active_stopwords:
            continue
        if token.endswith("ies") and len(token) > 5:
            token = token[:-3] + "y"
        elif token.endswith("s") and len(token) > 4 and not token.endswith(("ss", "sis")):
            token = token[:-1]
        cleaned_tokens.append(token)
    return cleaned_tokens


def phrase_exists(text_key: str, phrase: str) -> bool:
    phrase_key = normalize_match_key(phrase)
    if not phrase_key:
        return False
    return bool(re.search(rf"(?<![a-z0-9+#.]){re.escape(phrase_key)}(?![a-z0-9+#.])", text_key))


def extract_skill_hits(text: str, skill_vocabulary: list[str]) -> list[str]:
    text_key = normalize_match_key(text)
    hits = [skill for skill in skill_vocabulary if phrase_exists(text_key, skill)]
    return sorted(set(hits), key=lambda item: (item.lower(), item))


def classify_role_family(role_name: str) -> str:
    role_key = normalize_match_key(role_name)
    for family, keywords in ROLE_FAMILY_RULES:
        if any(normalize_match_key(keyword) in role_key for keyword in keywords):
            return family
    return "Other ICT"


def count_requirements_by_type(requirements: list[dict[str, Any]]) -> Counter[str]:
    return Counter(str(requirement.get("requirement_type") or "other") for requirement in requirements)


def build_chart_items(counter: Counter[str], limit: int | None = None) -> list[dict[str, Any]]:
    items = [{"label": label, "value": count} for label, count in counter.most_common(limit)]
    return items


def build_job_data_insights(
    roles: list[dict[str, Any]],
    requirements_by_role: dict[int, list[dict[str, Any]]],
    skill_vocabulary: list[str] | None = None,
    stopwords: set[str] | None = None,
) -> dict[str, Any]:
    skill_vocabulary = skill_vocabulary or []
    total_roles = len(roles)
    all_requirements = [
        requirement
        for role in roles
        for requirement in requirements_by_role.get(int(role.get("id") or 0), [])
    ]
    total_requirements = len(all_requirements)
    requirement_type_counts = count_requirements_by_type(all_requirements)
    role_family_counts: Counter[str] = Counter()
    global_skill_counts: Counter[str] = Counter()
    global_keyword_counts: Counter[str] = Counter()
    role_summaries: list[dict[str, Any]] = []

    for role in roles:
        role_id = int(role.get("id") or 0)
        role_name = clean_job_text(role.get("role_name"))
        family = classify_role_family(role_name)
        role_requirements = requirements_by_role.get(role_id, [])
        role_family_counts[family] += 1
        role_type_counts = count_requirements_by_type(role_requirements)
        role_text = " ".join(
            [
                role_name,
                clean_job_text(role.get("masco_description")),
                " ".join(clean_job_text(requirement.get("requirement_text")) for requirement in role_requirements),
            ]
        )
        skill_hits = extract_skill_hits(role_text, skill_vocabulary)
        keyword_counts = Counter(extract_text_tokens(role_text, stopwords))
        global_skill_counts.update(skill_hits)
        global_keyword_counts.update(keyword_counts)

        role_summaries.append(
            {
                "role_id": role_id,
                "role_name": role_name,
                "role_family": family,
                "requirement_count": len(role_requirements),
                "requirement_type_counts": dict(role_type_counts),
                "top_skills": skill_hits[:8],
                "top_keywords": [keyword for keyword, _ in keyword_counts.most_common(8)],
            }
        )

    requirement_counts = [item["requirement_count"] for item in role_summaries]
    average_requirements = safe_float(total_requirements / max(1, total_roles))
    roles_without_requirements = [item for item in role_summaries if item["requirement_count"] == 0]
    top_roles_by_requirements = sorted(
        role_summaries,
        key=lambda item: (item["requirement_count"], item["role_name"]),
        reverse=True,
    )[:12]
    roles_by_skill_density = sorted(
        role_summaries,
        key=lambda item: (len(item["top_skills"]), item["requirement_count"], item["role_name"]),
        reverse=True,
    )[:12]

    requirement_type_percentages = {
        requirement_type: percentage(count, total_requirements)
        for requirement_type, count in requirement_type_counts.items()
    }

    return {
        "stage": "Part 6 - Exploratory Data Analysis",
        "status": "ready_for_ml_nlp",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_roles": total_roles,
            "total_requirements": total_requirements,
            "average_requirements_per_role": average_requirements,
            "min_requirements_per_role": min(requirement_counts) if requirement_counts else 0,
            "max_requirements_per_role": max(requirement_counts) if requirement_counts else 0,
            "roles_without_requirements": len(roles_without_requirements),
            "distinct_requirement_types": len(requirement_type_counts),
            "distinct_detected_skill_keywords": len(global_skill_counts),
            "distinct_text_keywords": len(global_keyword_counts),
        },
        "requirement_type_counts": dict(requirement_type_counts.most_common()),
        "requirement_type_percentages": requirement_type_percentages,
        "role_family_counts": dict(role_family_counts.most_common()),
        "top_skill_keywords": [
            {"skill": skill, "role_count": count}
            for skill, count in global_skill_counts.most_common(25)
        ],
        "top_text_keywords": [
            {"keyword": keyword, "count": count}
            for keyword, count in global_keyword_counts.most_common(30)
        ],
        "top_roles_by_requirements": top_roles_by_requirements,
        "roles_by_skill_density": roles_by_skill_density,
        "roles_without_requirements": roles_without_requirements[:20],
        "chart_data": {
            "requirement_type_bar": build_chart_items(requirement_type_counts),
            "role_family_bar": build_chart_items(role_family_counts),
            "top_skill_bar": [
                {"label": skill, "value": count}
                for skill, count in global_skill_counts.most_common(15)
            ],
            "top_keyword_bar": [
                {"label": keyword, "value": count}
                for keyword, count in global_keyword_counts.most_common(15)
            ],
            "requirements_per_role_top": [
                {"label": item["role_name"], "value": item["requirement_count"]}
                for item in top_roles_by_requirements[:10]
            ],
        },
        "eda_questions_answered": [
            "How many EMASCO/MASCO job roles and requirements are available?",
            "Which requirement categories are most common?",
            "Which technical skills and keywords appear most often?",
            "Which role families dominate the collected job data?",
            "Which roles have the highest requirement density?",
        ],
    }
