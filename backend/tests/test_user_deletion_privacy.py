from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


with patch.dict(
    os.environ,
    {
        "APP_ENV": "testing",
        "FLASK_SECRET_KEY": "deletion-tests-only-secret-key-value-1234567890",
    },
    clear=False,
):
    app_module = importlib.import_module("app")


class FakeCursor:
    def __init__(self, user_exists: bool = True, fail_on_delete: bool = False):
        self.user_exists = user_exists
        self.fail_on_delete = fail_on_delete
        self.executed: list[tuple[str, tuple]] = []
        self.closed = False

    def execute(self, query, params=()):
        normalized_query = " ".join(query.lower().split())
        self.executed.append((normalized_query, tuple(params)))
        if self.fail_on_delete and normalized_query.startswith("delete from download_history"):
            raise app_module.DatabaseError("delete failed")

    def fetchone(self):
        return {"id": 2} if self.user_exists else None

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, user_exists: bool = True, fail_on_delete: bool = False):
        self.fake_cursor = FakeCursor(user_exists=user_exists, fail_on_delete=fail_on_delete)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self, dictionary=False):
        if not dictionary:
            raise AssertionError("User deletion must use a dictionary cursor")
        return self.fake_cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class UserDeletionPrivacyTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True, SECRET_KEY="deletion-test-secret")
        self.client = app_module.app.test_client()

    def sign_in_as_admin(self):
        with self.client.session_transaction() as current_session:
            current_session["user_id"] = 9

    def test_deleting_user_purges_downloads_and_analyses_before_account(self):
        self.sign_in_as_admin()
        connection = FakeConnection()

        with patch.object(app_module, "require_admin_json_response", return_value=None), patch.object(
            app_module, "get_db_connection", return_value=connection
        ):
            response = self.client.delete("/admin/users/2")

        self.assertEqual(response.status_code, 200)
        delete_queries = [
            (query, params)
            for query, params in connection.fake_cursor.executed
            if query.startswith("delete from")
        ]
        self.assertEqual(
            [query.split()[2] for query, _ in delete_queries],
            ["download_history", "resume_analyses", "users"],
        )
        self.assertEqual(delete_queries[0][1], (2, 2))
        self.assertEqual(delete_queries[1][1], (2,))
        self.assertEqual(delete_queries[2][1], (2,))
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        self.assertTrue(connection.fake_cursor.closed)
        self.assertTrue(connection.closed)

    def test_deletion_error_rolls_back_and_closes_resources(self):
        self.sign_in_as_admin()
        connection = FakeConnection(fail_on_delete=True)

        with patch.object(app_module, "require_admin_json_response", return_value=None), patch.object(
            app_module, "get_db_connection", return_value=connection
        ):
            response = self.client.delete("/admin/users/2")

        self.assertEqual(response.status_code, 500)
        self.assertFalse(connection.committed)
        self.assertTrue(connection.rolled_back)
        self.assertTrue(connection.fake_cursor.closed)
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
