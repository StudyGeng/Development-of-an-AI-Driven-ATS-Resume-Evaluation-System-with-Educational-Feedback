"""Database-backed personal history works across browser sessions and stays private."""

from __future__ import annotations

import importlib
import json
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
    {"APP_ENV": "testing", "FLASK_SECRET_KEY": "history-sync-tests-only-secret-key-1234567890"},
    clear=False,
):
    app_module = importlib.import_module("app")


class HistoryCursor:
    def __init__(self, database):
        self.database = database
        self.result = None
        self.rowcount = 0
        self.closed = False

    def execute(self, query, params=()):
        statement = " ".join(query.lower().split())
        args = tuple(params)
        self.database.queries.append((statement, args))

        if statement.startswith("select id, full_name, email") and "from users" in statement:
            self.result = self.database.users.get(args[0])
        elif statement.startswith("select user_id, analysis_json, resume_excerpt"):
            self.result = self.database.analyses.get(args[0])
        elif statement.startswith("select id from resume_analyses where id = %s and user_id = %s"):
            analysis = self.database.analyses.get(args[0])
            self.result = {"id": args[0]} if analysis and analysis["user_id"] == args[1] else None
        elif statement.startswith("delete from download_history") and "analysis_id in" in statement:
            owner_id = args[0]
            owned_ids = {item_id for item_id, row in self.database.analyses.items() if row["user_id"] == owner_id}
            self.database.downloads = [
                row for row in self.database.downloads
                if not (row["user_id"] == owner_id and row["analysis_id"] in owned_ids)
            ]
        elif statement.startswith("delete from download_history"):
            analysis_id, owner_id = args
            self.database.downloads = [
                row for row in self.database.downloads
                if not (row["analysis_id"] == analysis_id and row["user_id"] == owner_id)
            ]
        elif statement.startswith("delete from resume_analyses where id = %s and user_id = %s"):
            analysis_id, owner_id = args
            matching = self.database.analyses.get(analysis_id)
            self.rowcount = int(bool(matching and matching["user_id"] == owner_id))
            if self.rowcount:
                del self.database.analyses[analysis_id]
        elif statement.startswith("delete from resume_analyses where user_id = %s"):
            owner_id = args[0]
            owned_ids = [item_id for item_id, row in self.database.analyses.items() if row["user_id"] == owner_id]
            self.rowcount = len(owned_ids)
            for analysis_id in owned_ids:
                del self.database.analyses[analysis_id]
        else:
            raise AssertionError(f"Unexpected query: {statement}")

    def fetchone(self):
        return self.result

    def close(self):
        self.closed = True


class HistoryConnection:
    def __init__(self, database):
        self.database = database
        self.cursors = []
        self.closed = False
        self.committed = False
        self.rolled_back = False

    def cursor(self, dictionary=False):
        cursor = HistoryCursor(self.database)
        self.cursors.append(cursor)
        return cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class HistoryDatabase:
    def __init__(self):
        self.users = {
            1: {"id": 1, "full_name": "Alice", "email": "alice@example.com", "is_admin": 0},
            2: {"id": 2, "full_name": "Bob", "email": "bob@example.com", "is_admin": 0},
            9: {"id": 9, "full_name": "Admin", "email": "admin@example.com", "is_admin": 1},
        }
        self.analyses = {
            101: {
                "user_id": 1,
                "analysis_json": json.dumps({"feedback": "Alice's report", "target_role": "Data Analyst"}),
                "resume_excerpt": "Alice's resume text",
            },
            102: {
                "user_id": 1,
                "analysis_json": json.dumps({"feedback": "Alice's second report"}),
                "resume_excerpt": "Second excerpt",
            },
            202: {
                "user_id": 2,
                "analysis_json": json.dumps({"feedback": "Bob's report"}),
                "resume_excerpt": "Bob's private text",
            },
        }
        self.downloads = [
            {"user_id": 1, "analysis_id": 101},
            {"user_id": 1, "analysis_id": 102},
            {"user_id": 2, "analysis_id": 202},
        ]
        self.queries = []
        self.connections = []

    def __call__(self):
        connection = HistoryConnection(self)
        self.connections.append(connection)
        return connection


class AnalysisHistorySyncTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True, SECRET_KEY="history-sync-test-secret")
        self.database = HistoryDatabase()
        self.patch = patch.object(app_module, "get_db_connection", self.database)
        self.patch.start()
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.patch.stop()

    @staticmethod
    def sign_in(client, user_id):
        with client.session_transaction() as session:
            session["user_id"] = user_id

    def test_second_browser_session_can_open_same_saved_report(self):
        other_device = app_module.app.test_client()
        self.sign_in(self.client, 1)
        self.sign_in(other_device, 1)

        first = self.client.get("/analyses/101")
        second = other_device.get("/analyses/101")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json(), second.get_json())
        analysis = second.get_json()["analysis"]
        self.assertEqual(analysis["analysis_id"], 101)
        self.assertEqual(analysis["feedback"], "Alice's report")
        self.assertEqual(analysis["resume_excerpt"], "Alice's resume text")
        self.assertTrue(analysis["history_saved"])
        self.assertEqual(second.headers["Cache-Control"], "private, no-store")

    def test_missing_or_foreign_report_uses_same_not_found_response(self):
        self.sign_in(self.client, 1)
        foreign = self.client.get("/analyses/202")
        missing = self.client.get("/analyses/999")

        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(foreign.get_json(), missing.get_json())
        self.assertNotIn("Bob", foreign.get_data(as_text=True))

    def test_live_admin_cannot_open_another_users_personal_report(self):
        self.sign_in(self.client, 9)
        self.assertEqual(self.client.get("/analyses/202").status_code, 404)

    def test_invalid_snapshot_does_not_leak_raw_database_content(self):
        self.database.analyses[101]["analysis_json"] = "invalid confidential text"
        self.sign_in(self.client, 1)
        response = self.client.get("/analyses/101")

        self.assertEqual(response.status_code, 409)
        self.assertNotIn("confidential", response.get_data(as_text=True))

    def test_anonymous_read_and_delete_require_authentication(self):
        for method, route in (("get", "/analyses/101"), ("delete", "/analyses/101"), ("delete", "/analyses")):
            with self.subTest(method=method, route=route):
                response = getattr(self.client, method)(route)
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.database.connections, [])

    def test_delete_one_report_removes_only_its_downloads(self):
        self.sign_in(self.client, 1)
        response = self.client.delete("/analyses/101")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"deleted": True, "id": 101})
        self.assertNotIn(101, self.database.analyses)
        self.assertIn(102, self.database.analyses)
        self.assertIn(202, self.database.analyses)
        self.assertEqual(self.database.downloads, [
            {"user_id": 1, "analysis_id": 102},
            {"user_id": 2, "analysis_id": 202},
        ])
        self.assertTrue(self.database.connections[-1].committed)

    def test_delete_foreign_report_is_not_found_and_preserves_rows(self):
        self.sign_in(self.client, 1)
        response = self.client.delete("/analyses/202")

        self.assertEqual(response.status_code, 404)
        self.assertIn(202, self.database.analyses)
        self.assertEqual(len(self.database.downloads), 3)
        self.assertFalse(any(statement.startswith("delete") for statement, _ in self.database.queries))

    def test_clear_history_is_limited_to_signed_in_user(self):
        self.sign_in(self.client, 1)
        response = self.client.delete("/analyses")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"deleted_count": 2})
        self.assertEqual(set(self.database.analyses), {202})
        self.assertEqual(self.database.downloads, [{"user_id": 2, "analysis_id": 202}])
        self.assertEqual(self.client.get("/analyses/101").status_code, 404)


if __name__ == "__main__":
    unittest.main()
