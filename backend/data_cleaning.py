from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any


KNOWN_REQUIREMENT_TYPES = {"task", "skill", "knowledge", "tool", "education", "experience"}
REQUIREMENT_TYPE_ALIASES = {
    "ability": "skill",
    "abilities": "skill",
    "competency": "skill",
    "competencies": "skill",
    "degree": "education",
    "diploma": "education",
    "duties": "task",
    "duty": "task",
    "education_background": "education",
    "educational_background": "education",
    "experience_required": "experience",
    "hard_skill": "skill",
    "hard_skills": "skill",
    "job_duties": "task",
    "job_responsibilities": "task",
    "key_responsibilities": "task",
    "key_responsibility": "task",
    "knowledge_area": "knowledge",
    "knowledge_areas": "knowledge",
    "qualification": "education",
    "qualifications": "education",
    "responsibilities": "task",
    "responsibility": "task",
    "skill_required": "skill",
    "skills": "skill",
    "soft_skill": "skill",
    "soft_skills": "skill",
    "technical_skill": "skill",
    "technical_skills": "skill",
    "technologies": "tool",
    "technology": "tool",
    "tools": "tool",
    "tools_and_technologies": "tool",
    "work_experience": "experience",
}
TECH_ACRONYMS = {
    ".net": ".NET",
    "api": "API",
    "apis": "APIs",
    "css": "CSS",
    "html": "HTML",
    "ict": "ICT",
    "it": "IT",
    "java": "Java",
    "javascript": "JavaScript",
    "php": "PHP",
    "qa": "QA",
    "sql": "SQL",
    "ui": "UI",
    "ux": "UX",
    "xml": "XML",
}
TEXT_TRANSLATION = {
    0x00A0: " ",
    0x2018: "'",
    0x2019: "'",
    0x201C: '"',
    0x201D: '"',
    0x2013: "-",
    0x2014: "-",
}
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
BULLET_CHARS = "\u2022\u2023\u2043\u25e6\u2219"
BULLET_PREFIX_PATTERN = re.compile(r"^[\s\-*" + BULLET_CHARS + r"]+")


def row_value(row: dict[str, Any], key: str, fallback: Any = None) -> Any:
    return row.get(key, fallback) if isinstance(row, dict) else fallback


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_match_key(value: Any) -> str:
    cleaned = clean_job_text(value)
    return re.sub(r"[^a-z0-9+#.]+", " ", cleaned.lower()).strip()


def clean_job_text(value: Any, preserve_newlines: bool = False) -> str:
    text = str(value or "").translate(TEXT_TRANSLATION)
    text = CONTROL_CHARACTER_PATTERN.sub(" ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)

    if preserve_newlines:
        lines = []
        for line in text.split("\n"):
            cleaned_line = re.sub(r"\s+", " ", line).strip()
            cleaned_line = BULLET_PREFIX_PATTERN.sub("", cleaned_line).strip()
            if cleaned_line:
                lines.append(cleaned_line)
        return "\n".join(lines)

    text = re.sub(r"\n+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def format_job_title(value: str) -> str:
    cleaned = clean_job_text(value)
    force_title_case = bool(cleaned and (cleaned == cleaned.lower() or cleaned == cleaned.upper()))

    def fix_token(match: re.Match[str]) -> str:
        token = match.group(0)
        lower_token = token.lower()
        if lower_token in TECH_ACRONYMS:
            return TECH_ACRONYMS[lower_token]
        if force_title_case and re.search(r"[a-zA-Z]", token):
            return token[:1].upper() + token[1:].lower()
        return token

    return re.sub(r"[A-Za-z0-9.+#]+", fix_token, cleaned)


def clean_job_role_name(value: Any) -> str:
    cleaned = clean_job_text(value)
    cleaned = re.sub(r"^(?:job\s*title|role|occupation)\s*[:\-]\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*/\s*", " / ", cleaned)
    cleaned = cleaned.strip(" .,:;-/")
    return format_job_title(cleaned)


def clean_requirement_type(value: Any) -> str:
    cleaned = clean_job_text(value).lower()
    cleaned = re.sub(r"[^a-z0-9]+", "_", cleaned).strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    if not cleaned:
        return "other"
    if cleaned in REQUIREMENT_TYPE_ALIASES:
        return REQUIREMENT_TYPE_ALIASES[cleaned]
    if cleaned in KNOWN_REQUIREMENT_TYPES:
        return cleaned
    return "other"


def clean_importance_weight(value: Any, default: int = 3) -> int:
    weight = safe_int(value, default)
    return min(5, max(1, weight))


def clean_job_role_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": safe_int(row_value(row, "id")),
        "role_name": clean_job_role_name(row_value(row, "role_name")),
        "masco_description": clean_job_text(row_value(row, "masco_description")),
        "requirement_count": safe_int(row_value(row, "requirement_count")),
    }


def clean_job_requirement_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": safe_int(row_value(row, "id")),
        "role_id": safe_int(row_value(row, "role_id")),
        "requirement_type": clean_requirement_type(row_value(row, "requirement_type")),
        "requirement_text": clean_job_text(row_value(row, "requirement_text")),
        "importance_weight": clean_importance_weight(row_value(row, "importance_weight")),
    }


def values_differ(raw_value: Any, cleaned_value: Any) -> bool:
    if isinstance(cleaned_value, int):
        return safe_int(raw_value, cleaned_value) != cleaned_value
    return clean_job_text(raw_value) != str(cleaned_value or "")


def build_job_data_cleaning_plan(
    role_rows: list[dict[str, Any]],
    requirement_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    cleaned_roles = [clean_job_role_row(row) for row in role_rows]
    role_name_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for role in cleaned_roles:
        key = normalize_match_key(role["role_name"])
        if key:
            role_name_groups[key].append(role)

    duplicate_role_keys = {key for key, roles in role_name_groups.items() if len(roles) > 1}
    role_updates = []
    manual_review_roles = []
    raw_role_by_id = {safe_int(row_value(row, "id")): row for row in role_rows}

    for role in cleaned_roles:
        role_id = safe_int(role["id"])
        raw_role = raw_role_by_id.get(role_id, {})
        role_key = normalize_match_key(role["role_name"])
        if not role["role_name"]:
            manual_review_roles.append({"id": role_id, "reason": "empty_role_name"})
            continue
        if role_key in duplicate_role_keys:
            manual_review_roles.append(
                {
                    "id": role_id,
                    "role_name": role["role_name"],
                    "reason": "duplicate_cleaned_role_name",
                }
            )
            continue
        if (
            values_differ(row_value(raw_role, "role_name"), role["role_name"])
            or values_differ(row_value(raw_role, "masco_description"), role["masco_description"])
        ):
            role_updates.append(
                {
                    "id": role_id,
                    "role_name": role["role_name"],
                    "masco_description": role["masco_description"],
                }
            )

    cleaned_requirements = [clean_job_requirement_row(row) for row in requirement_rows]
    raw_requirement_by_id = {safe_int(row_value(row, "id")): row for row in requirement_rows}
    requirement_groups: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
    manual_review_requirements = []

    for requirement in cleaned_requirements:
        if not requirement["role_id"]:
            manual_review_requirements.append({"id": requirement["id"], "reason": "missing_role_id"})
            continue
        if not requirement["requirement_text"]:
            manual_review_requirements.append({"id": requirement["id"], "reason": "empty_requirement_text"})
            continue
        key = (
            requirement["role_id"],
            requirement["requirement_type"],
            normalize_match_key(requirement["requirement_text"]),
        )
        requirement_groups[key].append(requirement)

    duplicate_requirement_ids: list[int] = []
    duplicate_requirement_groups = []
    kept_requirement_ids: set[int] = set()

    for group in requirement_groups.values():
        ordered_group = sorted(group, key=lambda item: item["id"])
        kept_requirement_ids.add(ordered_group[0]["id"])
        if len(ordered_group) <= 1:
            continue
        remove_ids = [item["id"] for item in ordered_group[1:]]
        duplicate_requirement_ids.extend(remove_ids)
        duplicate_requirement_groups.append(
            {
                "role_id": ordered_group[0]["role_id"],
                "requirement_type": ordered_group[0]["requirement_type"],
                "requirement_text": ordered_group[0]["requirement_text"],
                "keep_id": ordered_group[0]["id"],
                "remove_ids": remove_ids,
            }
        )

    duplicate_requirement_id_set = set(duplicate_requirement_ids)
    requirement_updates = []
    for requirement in cleaned_requirements:
        requirement_id = requirement["id"]
        if requirement_id in duplicate_requirement_id_set:
            continue
        if requirement_id not in kept_requirement_ids:
            continue
        raw_requirement = raw_requirement_by_id.get(requirement_id, {})
        if (
            values_differ(row_value(raw_requirement, "requirement_type"), requirement["requirement_type"])
            or values_differ(row_value(raw_requirement, "requirement_text"), requirement["requirement_text"])
            or values_differ(row_value(raw_requirement, "importance_weight"), requirement["importance_weight"])
        ):
            requirement_updates.append(
                {
                    "id": requirement_id,
                    "requirement_type": requirement["requirement_type"],
                    "requirement_text": requirement["requirement_text"],
                    "importance_weight": requirement["importance_weight"],
                }
            )

    return {
        "summary": {
            "role_rows": len(role_rows),
            "role_updates_needed": len(role_updates),
            "manual_review_role_rows": len(manual_review_roles),
            "requirement_rows": len(requirement_rows),
            "requirement_updates_needed": len(requirement_updates),
            "duplicate_requirement_rows": len(duplicate_requirement_ids),
            "manual_review_requirement_rows": len(manual_review_requirements),
        },
        "role_updates": role_updates,
        "requirement_updates": requirement_updates,
        "duplicate_requirement_ids": duplicate_requirement_ids,
        "duplicate_requirement_groups": duplicate_requirement_groups[:25],
        "manual_review": {
            "roles": manual_review_roles[:25],
            "requirements": manual_review_requirements[:25],
        },
    }


def build_job_data_quality_report(
    role_rows: list[dict[str, Any]],
    requirement_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    plan = build_job_data_cleaning_plan(role_rows, requirement_rows)
    cleaned_roles = [clean_job_role_row(row) for row in role_rows]
    cleaned_requirements = [clean_job_requirement_row(row) for row in requirement_rows]
    role_keys = [normalize_match_key(role["role_name"]) for role in cleaned_roles if role["role_name"]]
    duplicate_role_groups = [
        {"role_name_key": key, "count": count}
        for key, count in Counter(role_keys).items()
        if count > 1
    ]

    raw_type_counts = Counter(str(row_value(row, "requirement_type", "") or "").strip() or "blank" for row in requirement_rows)
    clean_type_counts = Counter(requirement["requirement_type"] for requirement in cleaned_requirements)
    usable_requirements = [
        requirement
        for requirement in cleaned_requirements
        if requirement["role_id"] and requirement["requirement_text"]
    ]
    unique_requirement_keys = {
        (
            requirement["role_id"],
            requirement["requirement_type"],
            normalize_match_key(requirement["requirement_text"]),
        )
        for requirement in usable_requirements
    }

    issue_count = (
        plan["summary"]["role_updates_needed"]
        + plan["summary"]["manual_review_role_rows"]
        + plan["summary"]["requirement_updates_needed"]
        + plan["summary"]["duplicate_requirement_rows"]
        + plan["summary"]["manual_review_requirement_rows"]
    )

    return {
        "status": "clean" if issue_count == 0 else "needs_cleaning",
        "stage": "Part 5 - Data cleaning/preprocessing",
        "summary": {
            **plan["summary"],
            "usable_role_rows_after_cleaning": sum(1 for role in cleaned_roles if role["role_name"]),
            "usable_requirement_rows_after_cleaning": len(usable_requirements),
            "unique_requirement_rows_after_cleaning": len(unique_requirement_keys),
        },
        "requirement_type_counts": {
            "raw": dict(raw_type_counts.most_common()),
            "cleaned": dict(clean_type_counts.most_common()),
        },
        "duplicate_role_groups": duplicate_role_groups[:25],
        "sample_role_updates": plan["role_updates"][:10],
        "sample_requirement_updates": plan["requirement_updates"][:10],
        "sample_duplicate_requirement_groups": plan["duplicate_requirement_groups"][:10],
        "manual_review": plan["manual_review"],
        "preprocessing_rules": [
            "Trim extra spaces and control characters.",
            "Standardize smart quotes, dashes, and non-breaking spaces.",
            "Normalize role title capitalization while preserving technical acronyms.",
            "Map requirement types into task, skill, knowledge, tool, education, and experience.",
            "Clamp importance_weight into the 1 to 5 range.",
            "Detect duplicated requirements per role after text normalization.",
        ],
    }
