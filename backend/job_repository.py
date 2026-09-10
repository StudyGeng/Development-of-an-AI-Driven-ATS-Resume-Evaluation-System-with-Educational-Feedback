"""Database access for job roles, requirements, and cleaning operations."""

from __future__ import annotations

from typing import Any

from data_cleaning import (
    clean_importance_weight,
    clean_job_role_name,
    clean_job_text,
    clean_requirement_type,
    normalize_match_key as normalize_job_data_match_key,
)
from database import get_db_connection


def fetch_job_roles() -> list[dict[str, Any]]:
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT
            jr.id,
            jr.role_name,
            jr.masco_description,
            COUNT(req.id) AS requirement_count
        FROM job_roles jr
        LEFT JOIN job_requirements req
            ON req.role_id = jr.id
        GROUP BY jr.id, jr.role_name, jr.masco_description
        ORDER BY jr.role_name
        """
    )
    rows = cursor.fetchall()
    cursor.close()
    connection.close()
    roles: list[dict[str, Any]] = []
    seen_role_keys: set[str] = set()
    for row in rows:
        role_name = clean_job_role_name(row.get("role_name"))
        role_key = normalize_job_data_match_key(role_name)
        if not role_name or not role_key or role_key in seen_role_keys:
            continue
        seen_role_keys.add(role_key)
        roles.append(
            {
                "id": row["id"],
                "role_name": role_name,
                "masco_description": clean_job_text(row.get("masco_description")),
                "requirement_count": int(row.get("requirement_count") or 0),
            }
        )
    return roles


def fetch_job_requirements(role_id: int) -> list[dict[str, Any]]:
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT
            id,
            requirement_type,
            requirement_text,
            importance_weight
        FROM job_requirements
        WHERE role_id = %s
        ORDER BY importance_weight DESC, requirement_type, id
        """,
        (role_id,),
    )
    rows = cursor.fetchall()
    cursor.close()
    connection.close()
    requirements: list[dict[str, Any]] = []
    seen_requirement_keys: set[tuple[str, str]] = set()
    for row in rows:
        requirement_type = clean_requirement_type(row.get("requirement_type"))
        requirement_text = clean_job_text(row.get("requirement_text"))
        requirement_key = (requirement_type, normalize_job_data_match_key(requirement_text))
        if not requirement_text or not requirement_key[1] or requirement_key in seen_requirement_keys:
            continue
        seen_requirement_keys.add(requirement_key)
        requirements.append(
            {
                "id": row["id"],
                "requirement_type": requirement_type,
                "requirement_text": requirement_text,
                "importance_weight": clean_importance_weight(row.get("importance_weight")),
            }
        )
    return requirements


def fetch_raw_job_data_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
    cursor.close()
    connection.close()
    return role_rows, requirement_rows


def apply_job_data_cleaning_plan(plan: dict[str, Any]) -> dict[str, Any]:
    role_updates = plan.get("role_updates", []) or []
    requirement_updates = plan.get("requirement_updates", []) or []
    duplicate_requirement_ids = [
        int(requirement_id)
        for requirement_id in plan.get("duplicate_requirement_ids", []) or []
        if requirement_id
    ]

    connection = get_db_connection()
    cursor = connection.cursor()
    try:
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
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

    return {
        "role_updates_applied": len(role_updates),
        "requirement_updates_applied": len(requirement_updates),
        "duplicate_requirements_removed": len(duplicate_requirement_ids),
    }


def fetch_job_data_for_insights() -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    roles = fetch_job_roles()
    requirements_by_role: dict[int, list[dict[str, Any]]] = {}
    for role in roles:
        role_id = int(role["id"])
        requirements_by_role[role_id] = fetch_job_requirements(role_id)
    return roles, requirements_by_role
