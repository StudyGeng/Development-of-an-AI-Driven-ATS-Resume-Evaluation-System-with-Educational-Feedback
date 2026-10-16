from __future__ import annotations

import importlib
import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import database  # noqa: E402
import job_repository  # noqa: E402


ROLE_ROWS = [
    {"id": 1, "role_name": "Software Developer", "masco_description": "Builds software."},
    {"id": 2, "role_name": "Data Analyst", "masco_description": "Studies data."},
]
REQUIREMENT_ROWS = [
    {
        "id": 11,
        "role_id": 1,
        "requirement_type": "skill",
        "requirement_text": "Python",
        "importance_weight": 5,
    },
    {
        "id": 12,
        "role_id": 1,
        "requirement_type": "education",
        "requirement_text": "Computer science degree",
        "importance_weight": 3,
    },
    {
        "id": 21,
        "role_id": 2,
        "requirement_type": "skill",
        "requirement_text": "SQL",
        "importance_weight": 5,
    },
]


class CatalogCursor:
    def __init__(self, role_rows=None, requirement_rows=None):
        self.role_rows = role_rows if role_rows is not None else ROLE_ROWS
        self.requirement_rows = requirement_rows if requirement_rows is not None else REQUIREMENT_ROWS
        self.executed_sql: list[str] = []
        self.last_sql = ""
        self.closed = False

    def execute(self, sql, params=None):
        del params
        self.last_sql = " ".join(str(sql).split()).lower()
        self.executed_sql.append(self.last_sql)

    def fetchall(self):
        if "from job_roles" in self.last_sql:
            return [dict(row) for row in self.role_rows]
        if "from job_requirements" in self.last_sql:
            return [dict(row) for row in self.requirement_rows]
        raise AssertionError(f"Unexpected query: {self.last_sql}")

    def close(self):
        self.closed = True


class CatalogConnection:
    def __init__(self, role_rows=None, requirement_rows=None):
        self.catalog_cursor = CatalogCursor(role_rows, requirement_rows)
        self.closed = False

    def cursor(self, dictionary=False):
        self.dictionary = dictionary
        return self.catalog_cursor

    def close(self):
        self.closed = True


class WriteCursor:
    def __init__(self, error=None):
        self.error = error
        self.closed = False

    def execute(self, sql, params=None):
        del sql, params
        if self.error is not None:
            raise self.error

    def close(self):
        self.closed = True


class WriteConnection:
    def __init__(self, error=None, commit_error=None, events=None):
        self.write_cursor = WriteCursor(error)
        self.commit_error = commit_error
        self.events = events if events is not None else []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.write_cursor

    def commit(self):
        self.events.append("commit")
        if self.commit_error is not None:
            raise self.commit_error
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class JobCatalogCacheTests(unittest.TestCase):
    def setUp(self):
        job_repository.invalidate_job_catalog_cache()

    def tearDown(self):
        job_repository.invalidate_job_catalog_cache()

    def test_cold_catalog_load_uses_one_connection_and_two_queries(self):
        connection = CatalogConnection()
        with patch.object(job_repository, "get_db_connection", return_value=connection) as get_connection:
            roles, requirements_by_role = job_repository.fetch_job_catalog()

        get_connection.assert_called_once_with()
        self.assertEqual(len(connection.catalog_cursor.executed_sql), 2)
        self.assertTrue(connection.catalog_cursor.closed)
        self.assertTrue(connection.closed)
        self.assertEqual([role["id"] for role in roles], [1, 2])
        self.assertEqual(roles[0]["requirement_count"], 2)
        self.assertEqual(len(requirements_by_role[1]), 2)
        self.assertEqual(len(requirements_by_role[2]), 1)

    def test_warm_reads_make_no_queries_and_cannot_mutate_cache(self):
        connection = CatalogConnection()
        with patch.object(job_repository, "get_db_connection", return_value=connection) as get_connection:
            roles, requirements_by_role = job_repository.fetch_job_catalog()
            roles[0]["role_name"] = "Tampered"
            requirements_by_role[1][0]["requirement_text"] = "Tampered"

            cached_roles = job_repository.fetch_job_roles()
            cached_requirements = job_repository.fetch_job_requirements(1)
            insight_roles, insight_requirements = job_repository.fetch_job_data_for_insights()

        get_connection.assert_called_once_with()
        self.assertEqual(len(connection.catalog_cursor.executed_sql), 2)
        self.assertEqual(cached_roles[0]["role_name"], "Software Developer")
        self.assertEqual(cached_requirements[0]["requirement_text"], "Python")
        self.assertEqual(insight_roles[0]["role_name"], "Software Developer")
        self.assertEqual(insight_requirements[1][0]["requirement_text"], "Python")

    def test_explicit_invalidation_reloads_the_catalog(self):
        original_connection = CatalogConnection()
        changed_connection = CatalogConnection(
            role_rows=[{"id": 1, "role_name": "Platform Engineer", "masco_description": "Builds platforms."}],
            requirement_rows=[],
        )
        with patch.object(
            job_repository,
            "get_db_connection",
            side_effect=[original_connection, changed_connection],
        ) as get_connection:
            first_roles = job_repository.fetch_job_roles()
            job_repository.invalidate_job_catalog_cache()
            changed_roles = job_repository.fetch_job_roles()

        self.assertEqual(get_connection.call_count, 2)
        self.assertEqual(first_roles[0]["role_name"], "Software Developer")
        self.assertEqual(changed_roles[0]["role_name"], "Platform Engineer")

    def test_concurrent_cold_reads_share_one_atomic_load(self):
        connection = CatalogConnection()
        with patch.object(job_repository, "get_db_connection", return_value=connection) as get_connection:
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(lambda _: job_repository.fetch_job_roles(), range(16)))

        get_connection.assert_called_once_with()
        self.assertEqual(len(connection.catalog_cursor.executed_sql), 2)
        self.assertTrue(all(len(roles) == 2 for roles in results))

    def test_cursor_creation_failure_still_returns_connection(self):
        connection = Mock()
        connection.cursor.side_effect = database.DatabaseError("cursor failed")
        with patch.object(job_repository, "get_db_connection", return_value=connection):
            with self.assertRaises(database.DatabaseError):
                job_repository.fetch_job_catalog()
        connection.close.assert_called_once_with()


class JobCatalogInvalidationTests(unittest.TestCase):
    def test_empty_admin_plan_does_not_invalidate_valid_cache(self):
        connection = WriteConnection()
        with patch.object(job_repository, "get_db_connection", return_value=connection), patch.object(
            job_repository,
            "invalidate_job_catalog_cache",
        ) as invalidate:
            result = job_repository.apply_job_data_cleaning_plan({})

        self.assertTrue(connection.committed)
        invalidate.assert_not_called()
        self.assertEqual(result["role_updates_applied"], 0)

    def test_successful_admin_write_invalidates_after_commit(self):
        events: list[str] = []
        connection = WriteConnection(events=events)
        with patch.object(job_repository, "get_db_connection", return_value=connection), patch.object(
            job_repository,
            "invalidate_job_catalog_cache",
            side_effect=lambda: events.append("invalidate"),
        ) as invalidate:
            result = job_repository.apply_job_data_cleaning_plan(
                {
                    "role_updates": [
                        {"id": 1, "role_name": "Software Engineer", "masco_description": "Builds software."}
                    ]
                }
            )

        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        self.assertTrue(connection.closed)
        invalidate.assert_called_once_with()
        self.assertEqual(events, ["commit", "invalidate"])
        self.assertEqual(result["role_updates_applied"], 1)

    def test_failed_admin_write_rolls_back_without_invalidating(self):
        connection = WriteConnection(database.DatabaseError("write failed"))
        with patch.object(job_repository, "get_db_connection", return_value=connection), patch.object(
            job_repository,
            "invalidate_job_catalog_cache",
        ) as invalidate:
            with self.assertRaises(database.DatabaseError):
                job_repository.apply_job_data_cleaning_plan(
                    {
                        "role_updates": [
                            {"id": 1, "role_name": "Software Engineer", "masco_description": "Builds software."}
                        ]
                    }
                )

        self.assertFalse(connection.committed)
        self.assertTrue(connection.rolled_back)
        self.assertTrue(connection.closed)
        invalidate.assert_not_called()

    def test_failed_commit_rolls_back_without_invalidating(self):
        connection = WriteConnection(commit_error=database.DatabaseError("commit failed"))
        with patch.object(job_repository, "get_db_connection", return_value=connection), patch.object(
            job_repository,
            "invalidate_job_catalog_cache",
        ) as invalidate:
            with self.assertRaises(database.DatabaseError):
                job_repository.apply_job_data_cleaning_plan(
                    {
                        "role_updates": [
                            {"id": 1, "role_name": "Software Engineer", "masco_description": "Builds software."}
                        ]
                    }
                )

        self.assertFalse(connection.committed)
        self.assertTrue(connection.rolled_back)
        self.assertTrue(connection.closed)
        invalidate.assert_not_called()


class RoleRankingQueryRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "testing",
                "FLASK_SECRET_KEY": "job-performance-tests-only-secret-value-123456789",
            },
            clear=False,
        ):
            cls.app_module = importlib.import_module("app")

    def test_ranking_95_roles_uses_one_bulk_catalog_access(self):
        roles = [
            {
                "id": role_id,
                "role_name": f"Role {role_id}",
                "masco_description": "Role description",
                "requirement_count": 1,
            }
            for role_id in range(1, 96)
        ]
        requirements_by_role = {
            role_id: [
                {
                    "id": role_id,
                    "requirement_type": "skill",
                    "requirement_text": f"Skill {role_id}",
                    "importance_weight": 5,
                }
            ]
            for role_id in range(1, 96)
        }
        fit_result = {"combined_score": 10.0, "important_keywords": []}

        with patch.object(
            self.app_module,
            "fetch_job_catalog",
            return_value=(roles, requirements_by_role),
        ) as fetch_catalog, patch.object(
            self.app_module,
            "fetch_job_requirements",
            side_effect=AssertionError("ranking must not fetch requirements per role"),
        ) as fetch_one, patch.object(
            self.app_module,
            "evaluate_job_fit",
            return_value=fit_result,
        ), patch.object(
            self.app_module,
            "compute_role_evidence_score",
            return_value=0.0,
        ), patch.object(
            self.app_module,
            "extract_employment_entries",
            return_value=[],
        ) as extract_entries:
            matches = self.app_module.rank_database_role_matches("resume text", limit=3)

        fetch_catalog.assert_called_once_with()
        fetch_one.assert_not_called()
        extract_entries.assert_called_once_with("resume text")
        self.assertEqual(len(matches), 3)


if __name__ == "__main__":
    unittest.main()
