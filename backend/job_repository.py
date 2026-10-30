"""Database access for job roles, requirements, and cleaning operations."""

from __future__ import annotations

from threading import RLock
from typing import Any

from data_cleaning import (
    clean_importance_weight,
    clean_job_role_name,
    clean_job_text,
    clean_requirement_type,
    normalize_match_key as normalize_job_data_match_key,
)
from database import get_db_connection


JobCatalogSnapshot = tuple[
    tuple[dict[str, Any], ...],
    dict[int, tuple[dict[str, Any], ...]],
]

_job_catalog_cache: JobCatalogSnapshot | None = None
_job_catalog_cache_lock = RLock()


def _load_job_catalog_snapshot() -> JobCatalogSnapshot:
    """Load and normalize the complete public job catalogue in two queries."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, role_name, masco_description
            FROM job_roles
            ORDER BY role_name, id
            """
        )
        role_rows = cursor.fetchall()
        cursor.execute(
            """
            SELECT id, role_id, requirement_type, requirement_text, importance_weight
            FROM job_requirements
            ORDER BY role_id, importance_weight DESC, requirement_type, id
            """
        )
        requirement_rows = cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    raw_requirement_counts: dict[int, int] = {}
    for row in requirement_rows:
        role_id = int(row["role_id"])
        raw_requirement_counts[role_id] = raw_requirement_counts.get(role_id, 0) + 1

    roles: list[dict[str, Any]] = []
    seen_role_keys: set[str] = set()
    accepted_role_ids: set[int] = set()
    for row in role_rows:
        role_id = int(row["id"])
        role_name = clean_job_role_name(row.get("role_name"))
        role_key = normalize_job_data_match_key(role_name)
        if not role_name or not role_key or role_key in seen_role_keys:
            continue
        seen_role_keys.add(role_key)
        accepted_role_ids.add(role_id)
        roles.append(
            {
                "id": role_id,
                "role_name": role_name,
                "masco_description": clean_job_text(row.get("masco_description")),
                "requirement_count": raw_requirement_counts.get(role_id, 0),
            }
        )

    requirements_by_role: dict[int, list[dict[str, Any]]] = {
        role_id: [] for role_id in accepted_role_ids
    }
    seen_requirement_keys_by_role: dict[int, set[tuple[str, str]]] = {
        role_id: set() for role_id in accepted_role_ids
    }
    for row in requirement_rows:
        role_id = int(row["role_id"])
        if role_id not in accepted_role_ids:
            continue
        requirement_type = clean_requirement_type(row.get("requirement_type"))
        requirement_text = clean_job_text(row.get("requirement_text"))
        requirement_key = (requirement_type, normalize_job_data_match_key(requirement_text))
        if (
            not requirement_text
            or not requirement_key[1]
            or requirement_key in seen_requirement_keys_by_role[role_id]
        ):
            continue
        seen_requirement_keys_by_role[role_id].add(requirement_key)
        requirements_by_role[role_id].append(
            {
                "id": int(row["id"]),
                "requirement_type": requirement_type,
                "requirement_text": requirement_text,
                "importance_weight": clean_importance_weight(row.get("importance_weight")),
            }
        )

    return (
        tuple(roles),
        {
            role_id: tuple(requirements)
            for role_id, requirements in requirements_by_role.items()
        },
    )


def _get_job_catalog_snapshot() -> JobCatalogSnapshot:
    global _job_catalog_cache

    with _job_catalog_cache_lock:
        if _job_catalog_cache is None:
            _job_catalog_cache = _load_job_catalog_snapshot()
        return _job_catalog_cache


def get_job_catalog_version() -> object:
    """Return a token that is a new object whenever the catalogue is reloaded."""
    return _get_job_catalog_snapshot()


def invalidate_job_catalog_cache() -> None:
    """Discard this process's catalogue snapshot after a committed admin change."""
    global _job_catalog_cache

    with _job_catalog_cache_lock:
        _job_catalog_cache = None


def fetch_job_catalog() -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    """Return a defensive copy of the cached roles and grouped requirements."""
    roles, requirements_by_role = _get_job_catalog_snapshot()
    return (
        [dict(role) for role in roles],
        {
            role_id: [dict(requirement) for requirement in requirements]
            for role_id, requirements in requirements_by_role.items()
        },
    )


def fetch_job_roles() -> list[dict[str, Any]]:
    roles, _ = _get_job_catalog_snapshot()
    return [dict(role) for role in roles]


def fetch_job_requirements(role_id: int) -> list[dict[str, Any]]:
    _, requirements_by_role = _get_job_catalog_snapshot()
    return [dict(requirement) for requirement in requirements_by_role.get(int(role_id), ())]


def fetch_raw_job_data_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, role_name, masco_description
            FROM job_roles
            ORDER BY id
            """
        )
        role_rows = cursor.fetchall()
        cursor.execute(
            """
            SELECT id, role_id, requirement_type, requirement_text, importance_weight
            FROM job_requirements
            ORDER BY role_id, id
            """
        )
        requirement_rows = cursor.fetchall()
        return role_rows, requirement_rows
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


def apply_job_data_cleaning_plan(plan: dict[str, Any]) -> dict[str, Any]:
    role_updates = plan.get("role_updates", []) or []
    requirement_updates = plan.get("requirement_updates", []) or []
    duplicate_requirement_ids = [
        int(requirement_id)
        for requirement_id in plan.get("duplicate_requirement_ids", []) or []
        if requirement_id
    ]
    catalog_changed = bool(role_updates or requirement_updates or duplicate_requirement_ids)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        for update in role_updates:
            cursor.execute(
                """
                UPDATE job_roles
                SET role_name = %s,
                    masco_description = %s
                WHERE id = %s
                """,
                (update["role_name"], update["masco_description"], int(update["id"])),
            )

        for update in requirement_updates:
            cursor.execute(
                """
                UPDATE job_requirements
                SET requirement_type = %s,
                    requirement_text = %s,
                    importance_weight = %s
                WHERE id = %s
                """,
                (
                    update["requirement_type"],
                    update["requirement_text"],
                    int(update["importance_weight"]),
                    int(update["id"]),
                ),
            )

        if duplicate_requirement_ids:
            placeholders = ", ".join(["%s"] * len(duplicate_requirement_ids))
            cursor.execute(
                f"DELETE FROM job_requirements WHERE id IN ({placeholders})",
                tuple(duplicate_requirement_ids),
            )

        connection.commit()
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    else:
        if catalog_changed:
            invalidate_job_catalog_cache()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

    return {
        "role_updates_applied": len(role_updates),
        "requirement_updates_applied": len(requirement_updates),
        "duplicate_requirements_removed": len(duplicate_requirement_ids),
    }


def fetch_job_data_for_insights() -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    return fetch_job_catalog()
