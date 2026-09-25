"""Export, validate, and seed the public job catalogue.

Only the five public catalogue fields are handled by this module. User accounts,
resumes, analyses, PINs, and download history are never queried or serialized.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from database import get_db_connection


SCHEMA_VERSION = 1
DEFAULT_CATALOG_PATH = Path(__file__).with_name("job_catalog_seed.json")
ROOT_FIELDS = {
    "schema_version",
    "role_count",
    "requirement_count",
    "content_sha256",
    "roles",
}
ROLE_FIELDS = {"role_name", "masco_description", "requirements"}
REQUIREMENT_FIELDS = {
    "requirement_type",
    "requirement_text",
    "importance_weight",
}


class CatalogValidationError(ValueError):
    """Raised when a catalogue file is incomplete, unsafe, or inconsistent."""


def _canonical_roles_json(roles: Sequence[Mapping[str, Any]]) -> str:
    return json.dumps(
        roles,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def calculate_content_sha256(roles: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(_canonical_roles_json(roles).encode("utf-8")).hexdigest()


def _require_exact_fields(
    value: Mapping[str, Any], expected: set[str], location: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected {', '.join(unexpected)}")
        raise CatalogValidationError(f"{location} has invalid fields ({'; '.join(details)})")


def _require_text(value: Any, location: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise CatalogValidationError(f"{location} must be text")
    if value != value.strip():
        raise CatalogValidationError(f"{location} has leading or trailing whitespace")
    if not allow_empty and not value:
        raise CatalogValidationError(f"{location} cannot be empty")
    if "\x00" in value:
        raise CatalogValidationError(f"{location} contains a null character")
    return value


def validate_catalog(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a plain dictionary safe to insert into MySQL."""
    if not isinstance(document, Mapping):
        raise CatalogValidationError("catalogue root must be an object")
    _require_exact_fields(document, ROOT_FIELDS, "catalogue root")
    if document["schema_version"] != SCHEMA_VERSION:
        raise CatalogValidationError(
            f"unsupported schema_version {document['schema_version']!r}; expected {SCHEMA_VERSION}"
        )
    roles_value = document["roles"]
    if not isinstance(roles_value, list):
        raise CatalogValidationError("roles must be a list")

    seen_roles: set[str] = set()
    requirement_count = 0
    previous_role_sort_key: tuple[str, str] | None = None
    for role_index, role in enumerate(roles_value):
        location = f"roles[{role_index}]"
        if not isinstance(role, Mapping):
            raise CatalogValidationError(f"{location} must be an object")
        _require_exact_fields(role, ROLE_FIELDS, location)
        role_name = _require_text(role["role_name"], f"{location}.role_name")
        if len(role_name) > 255:
            raise CatalogValidationError(f"{location}.role_name exceeds 255 characters")
        role_key = role_name.casefold()
        if role_key in seen_roles:
            raise CatalogValidationError(f"duplicate role_name: {role_name}")
        seen_roles.add(role_key)
        role_sort_key = (role_key, role_name)
        if previous_role_sort_key is not None and role_sort_key < previous_role_sort_key:
            raise CatalogValidationError("roles must be sorted by role_name")
        previous_role_sort_key = role_sort_key
        _require_text(
            role["masco_description"],
            f"{location}.masco_description",
            allow_empty=True,
        )

        requirements = role["requirements"]
        if not isinstance(requirements, list):
            raise CatalogValidationError(f"{location}.requirements must be a list")
        seen_requirements: set[tuple[str, str]] = set()
        previous_requirement_sort_key: tuple[str, str, int] | None = None
        for requirement_index, requirement in enumerate(requirements):
            requirement_location = f"{location}.requirements[{requirement_index}]"
            if not isinstance(requirement, Mapping):
                raise CatalogValidationError(f"{requirement_location} must be an object")
            _require_exact_fields(requirement, REQUIREMENT_FIELDS, requirement_location)
            requirement_type = _require_text(
                requirement["requirement_type"],
                f"{requirement_location}.requirement_type",
            )
            if len(requirement_type) > 50:
                raise CatalogValidationError(
                    f"{requirement_location}.requirement_type exceeds 50 characters"
                )
            requirement_text = _require_text(
                requirement["requirement_text"],
                f"{requirement_location}.requirement_text",
            )
            weight = requirement["importance_weight"]
            if isinstance(weight, bool) or not isinstance(weight, int) or not 1 <= weight <= 5:
                raise CatalogValidationError(
                    f"{requirement_location}.importance_weight must be an integer from 1 to 5"
                )
            requirement_key = (requirement_type.casefold(), requirement_text.casefold())
            if requirement_key in seen_requirements:
                raise CatalogValidationError(
                    f"duplicate requirement for {role_name}: {requirement_text}"
                )
            seen_requirements.add(requirement_key)
            requirement_sort_key = (requirement_type.casefold(), requirement_text.casefold(), weight)
            if (
                previous_requirement_sort_key is not None
                and requirement_sort_key < previous_requirement_sort_key
            ):
                raise CatalogValidationError(
                    f"requirements for {role_name} must be sorted by type and text"
                )
            previous_requirement_sort_key = requirement_sort_key
            requirement_count += 1

    role_count = document["role_count"]
    declared_requirement_count = document["requirement_count"]
    if isinstance(role_count, bool) or not isinstance(role_count, int):
        raise CatalogValidationError("role_count must be an integer")
    if isinstance(declared_requirement_count, bool) or not isinstance(
        declared_requirement_count, int
    ):
        raise CatalogValidationError("requirement_count must be an integer")
    if role_count != len(roles_value):
        raise CatalogValidationError(
            f"role_count is {role_count}, but roles contains {len(roles_value)} items"
        )
    if declared_requirement_count != requirement_count:
        raise CatalogValidationError(
            "requirement_count is "
            f"{declared_requirement_count}, but roles contain {requirement_count} requirements"
        )
    expected_digest = calculate_content_sha256(roles_value)
    digest = document["content_sha256"]
    if not isinstance(digest, str) or digest != expected_digest:
        raise CatalogValidationError("content_sha256 does not match the catalogue content")
    return dict(document)


def build_catalog_document(
    role_rows: Iterable[Mapping[str, Any]],
    requirement_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build deterministic seed data from the two public catalogue queries."""
    roles_by_id: dict[int, dict[str, Any]] = {}
    for row in role_rows:
        role_id = int(row["id"])
        if role_id in roles_by_id:
            raise CatalogValidationError(f"duplicate database role id: {role_id}")
        roles_by_id[role_id] = {
            "role_name": str(row["role_name"] or "").strip(),
            "masco_description": str(row["masco_description"] or "").strip(),
            "requirements": [],
        }

    for row in requirement_rows:
        role_id = int(row["role_id"])
        if role_id not in roles_by_id:
            raise CatalogValidationError(
                f"requirement {row.get('id', '?')} refers to unknown role id {role_id}"
            )
        roles_by_id[role_id]["requirements"].append(
            {
                "requirement_type": str(row["requirement_type"] or "").strip(),
                "requirement_text": str(row["requirement_text"] or "").strip(),
                "importance_weight": int(row["importance_weight"]),
            }
        )

    roles = sorted(
        roles_by_id.values(),
        key=lambda role: (role["role_name"].casefold(), role["role_name"]),
    )
    for role in roles:
        role["requirements"].sort(
            key=lambda requirement: (
                requirement["requirement_type"].casefold(),
                requirement["requirement_text"].casefold(),
                requirement["importance_weight"],
            )
        )
    document = {
        "schema_version": SCHEMA_VERSION,
        "role_count": len(roles),
        "requirement_count": sum(len(role["requirements"]) for role in roles),
        "content_sha256": calculate_content_sha256(roles),
        "roles": roles,
    }
    return validate_catalog(document)


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CatalogValidationError(f"catalogue file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise CatalogValidationError(f"invalid JSON in {path}: {error}") from error
    return validate_catalog(document)


def _fetch_catalog_rows(connection: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cursor = connection.cursor(dictionary=True)
    try:
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
            ORDER BY role_id, requirement_type, requirement_text, id
            """
        )
        requirement_rows = cursor.fetchall()
        return role_rows, requirement_rows
    finally:
        cursor.close()


def export_catalog(path: Path = DEFAULT_CATALOG_PATH, *, force: bool = False) -> dict[str, Any]:
    if path.exists() and not force:
        raise FileExistsError(f"refusing to overwrite {path}; pass --force to replace it")
    connection = get_db_connection()
    try:
        role_rows, requirement_rows = _fetch_catalog_rows(connection)
    finally:
        connection.close()
    document = build_catalog_document(role_rows, requirement_rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return document


def seed_catalog(
    document: Mapping[str, Any],
    *,
    replace: bool = False,
) -> tuple[int, int, bool]:
    """Seed an empty database, or explicitly replace a different catalogue.

    Returns ``(role_count, requirement_count, changed)``. Re-running the seed
    against identical data is a safe no-op. A different non-empty catalogue is
    never overwritten unless ``replace=True``.
    """
    validated = validate_catalog(document)
    connection = get_db_connection()
    cursor = None
    try:
        current_role_rows, current_requirement_rows = _fetch_catalog_rows(connection)
        current_document = build_catalog_document(current_role_rows, current_requirement_rows)
        current_has_data = bool(current_role_rows or current_requirement_rows)
        if current_has_data and current_document["content_sha256"] == validated["content_sha256"]:
            return validated["role_count"], validated["requirement_count"], False
        if current_has_data and not replace:
            raise RuntimeError(
                "database already contains different job catalogue data; "
                "inspect it first or pass --replace"
            )

        cursor = connection.cursor()
        if current_has_data:
            cursor.execute("DELETE FROM job_requirements")
            cursor.execute("DELETE FROM job_roles")

        role_ids: dict[str, int] = {}
        for role in validated["roles"]:
            cursor.execute(
                """
                INSERT INTO job_roles (role_name, masco_description)
                VALUES (%s, %s)
                """,
                (role["role_name"], role["masco_description"] or None),
            )
            role_ids[role["role_name"]] = int(cursor.lastrowid)

        requirement_values = [
            (
                role_ids[role["role_name"]],
                requirement["requirement_type"],
                requirement["requirement_text"],
                requirement["importance_weight"],
            )
            for role in validated["roles"]
            for requirement in role["requirements"]
        ]
        if requirement_values:
            cursor.executemany(
                """
                INSERT INTO job_requirements (
                    role_id,
                    requirement_type,
                    requirement_text,
                    importance_weight
                ) VALUES (%s, %s, %s, %s)
                """,
                requirement_values,
            )
        connection.commit()
        return validated["role_count"], validated["requirement_count"], True
    except Exception:
        connection.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


def verify_database(document: Mapping[str, Any]) -> tuple[int, int]:
    validated = validate_catalog(document)
    connection = get_db_connection()
    try:
        role_rows, requirement_rows = _fetch_catalog_rows(connection)
    finally:
        connection.close()
    current = build_catalog_document(role_rows, requirement_rows)
    if current["content_sha256"] != validated["content_sha256"]:
        raise RuntimeError(
            "database catalogue differs from the bundled seed "
            f"(database {current['content_sha256']}, seed {validated['content_sha256']})"
        )
    return current["role_count"], current["requirement_count"]


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export", help="export only public job catalogue fields from the configured database"
    )
    export_parser.add_argument("--output", type=Path, default=DEFAULT_CATALOG_PATH)
    export_parser.add_argument("--force", action="store_true")

    seed_parser = subparsers.add_parser(
        "seed", help="load the bundled catalogue into the configured database"
    )
    seed_parser.add_argument("--input", type=Path, default=DEFAULT_CATALOG_PATH)
    seed_parser.add_argument(
        "--replace",
        action="store_true",
        help="replace a non-empty catalogue that differs from the seed",
    )

    verify_parser = subparsers.add_parser(
        "verify", help="validate the seed file and optionally compare it with the database"
    )
    verify_parser.add_argument("--input", type=Path, default=DEFAULT_CATALOG_PATH)
    verify_parser.add_argument("--database", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_argument_parser().parse_args(argv)
    if args.command == "export":
        document = export_catalog(args.output, force=args.force)
        print(
            f"Exported {document['role_count']} roles and "
            f"{document['requirement_count']} requirements to {args.output}"
        )
        return 0

    document = load_catalog(args.input)
    if args.command == "seed":
        role_count, requirement_count, changed = seed_catalog(
            document, replace=args.replace
        )
        action = "Seeded" if changed else "Already current:"
        print(f"{action} {role_count} roles and {requirement_count} requirements")
        return 0

    if args.database:
        role_count, requirement_count = verify_database(document)
        print(f"Verified database: {role_count} roles and {requirement_count} requirements")
    else:
        print(
            f"Verified seed file: {document['role_count']} roles and "
            f"{document['requirement_count']} requirements"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
