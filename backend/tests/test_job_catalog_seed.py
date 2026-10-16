from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import seed_job_catalog  # noqa: E402


ROLE_ROWS = [
    {
        "id": 7,
        "role_name": "Software Developer",
        "masco_description": "Builds and maintains software.",
    }
]
REQUIREMENT_ROWS = [
    {
        "id": 71,
        "role_id": 7,
        "requirement_type": "skill",
        "requirement_text": "Python",
        "importance_weight": 5,
    }
]


class JobCatalogSeedTests(unittest.TestCase):
    def test_bundled_catalogue_is_valid_complete_and_public_only(self):
        document = seed_job_catalog.load_catalog()

        self.assertEqual(document["role_count"], 96)
        self.assertEqual(document["requirement_count"], 2847)
        serialized = json.dumps(document).casefold()
        for private_field in (
            "password_hash",
            "pin_hash",
            "resume_excerpt",
            "analysis_json",
            "downloaded_file_name",
            "user_id",
            "email_verified_at",
        ):
            self.assertNotIn(f'"{private_field}"', serialized)

    def test_build_catalogue_is_deterministic_and_ignores_database_ids(self):
        first = seed_job_catalog.build_catalog_document(ROLE_ROWS, REQUIREMENT_ROWS)
        roles_with_different_ids = [dict(ROLE_ROWS[0], id=900)]
        requirements_with_different_ids = [
            dict(REQUIREMENT_ROWS[0], id=901, role_id=900)
        ]
        second = seed_job_catalog.build_catalog_document(
            roles_with_different_ids, requirements_with_different_ids
        )

        self.assertEqual(first, second)

    def test_validation_rejects_unexpected_private_fields(self):
        document = seed_job_catalog.build_catalog_document(ROLE_ROWS, REQUIREMENT_ROWS)
        document["users"] = [{"email": "private@example.test"}]

        with self.assertRaisesRegex(
            seed_job_catalog.CatalogValidationError, "unexpected users"
        ):
            seed_job_catalog.validate_catalog(document)

    def test_validation_rejects_modified_content_hash(self):
        document = seed_job_catalog.build_catalog_document(ROLE_ROWS, REQUIREMENT_ROWS)
        document["roles"][0]["requirements"][0]["requirement_text"] = "Java"

        with self.assertRaisesRegex(
            seed_job_catalog.CatalogValidationError, "content_sha256"
        ):
            seed_job_catalog.validate_catalog(document)

    def test_seed_loads_an_empty_database_in_one_transaction(self):
        document = seed_job_catalog.build_catalog_document(ROLE_ROWS, REQUIREMENT_ROWS)
        select_cursor = Mock()
        select_cursor.fetchall.side_effect = [[], []]
        insert_cursor = Mock()
        insert_cursor.lastrowid = 42
        connection = Mock()
        connection.cursor.side_effect = [select_cursor, insert_cursor]

        with patch.object(
            seed_job_catalog, "get_db_connection", return_value=connection
        ):
            result = seed_job_catalog.seed_catalog(document)

        self.assertEqual(result, (1, 1, True))
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()
        insert_cursor.executemany.assert_called_once()
        inserted_requirement = insert_cursor.executemany.call_args.args[1][0]
        self.assertEqual(inserted_requirement, (42, "skill", "Python", 5))

    def test_seed_does_not_overwrite_a_different_nonempty_catalogue(self):
        document = seed_job_catalog.build_catalog_document(ROLE_ROWS, REQUIREMENT_ROWS)
        existing_roles = [dict(ROLE_ROWS[0], role_name="Data Analyst")]
        existing_requirements = [
            dict(REQUIREMENT_ROWS[0], requirement_text="SQL")
        ]
        select_cursor = Mock()
        select_cursor.fetchall.side_effect = [existing_roles, existing_requirements]
        connection = Mock()
        connection.cursor.return_value = select_cursor

        with patch.object(
            seed_job_catalog, "get_db_connection", return_value=connection
        ), self.assertRaisesRegex(RuntimeError, "--replace"):
            seed_job_catalog.seed_catalog(document)

        connection.commit.assert_not_called()
        connection.rollback.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
