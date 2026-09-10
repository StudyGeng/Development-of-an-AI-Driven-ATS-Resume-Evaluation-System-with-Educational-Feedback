from __future__ import annotations

import importlib
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import database  # noqa: E402


with patch.dict(
    os.environ,
    {
        "APP_ENV": "testing",
        "FLASK_SECRET_KEY": "history-tests-only-secret-key-value-1234567890",
    },
    clear=False,
), patch.object(database, "init_mysql_database"):
    app_module = importlib.import_module("app")


USERS = {
    1: {"id": 1, "email": "alice@example.com", "is_admin": 0},
    2: {"id": 2, "email": "bob@example.com", "is_admin": 0},
    4: {"id": 4, "email": "demoted@example.com", "is_admin": 0},
    9: {"id": 9, "email": "admin@example.com", "is_admin": 1},
}

ANALYSES = {
    1: [
        {
            "id": 101,
            "uploaded_resume_name": "alice-resume.pdf",
            "target_role": "Software Developer",
            "matching_percentage": 82.65,
            "feedback": "Alice-only feedback",
            "resume_excerpt": "Alice-only excerpt",
            "created_at": datetime(2026, 9, 1, 10, 30),
        }
    ],
    2: [
        {
            "id": 202,
            "uploaded_resume_name": "bob-resume.pdf",
            "target_role": "Cybersecurity Analyst",
            "matching_percentage": 78,
            "feedback": "Bob-only feedback",
            "resume_excerpt": "Bob-only excerpt",
            "created_at": datetime(2026, 9, 2, 11, 45),
        }
    ],
    9: [],
}


class FakeCursor:
    def __init__(self, users, analyses, fail=False):
        self.users = users
        self.analyses = analyses
        self.fail = fail
        self.executed: list[tuple[str, tuple]] = []
        self.result = None
        self.closed = False

    def execute(self, query, params=()):
        if self.fail:
            raise app_module.DatabaseError("database unavailable")

        normalized_query = " ".join(query.lower().split())
        safe_params = tuple(params)
        self.executed.append((normalized_query, safe_params))

        if "from users where id = %s" in normalized_query:
            user = self.users.get(int(safe_params[0]))
            self.result = dict(user) if user else None
            return

        if "from users where email = %s" in normalized_query:
            requested_email = str(safe_params[0]).lower()
            user = next(
                (candidate for candidate in self.users.values() if candidate["email"].lower() == requested_email),
                None,
            )
            self.result = {"id": user["id"]} if user else None
            return

        if "from resume_analyses" in normalized_query:
            user_id = int(safe_params[0])
            self.result = [dict(row) for row in self.analyses.get(user_id, [])]
            return

        raise AssertionError(f"Unexpected query: {normalized_query}")

    def fetchone(self):
        return self.result

    def fetchall(self):
        return list(self.result or [])

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, users, analyses, fail=False):
        self.fake_cursor = FakeCursor(users, analyses, fail=fail)
        self.closed = False

    def cursor(self, dictionary=False):
        if not dictionary:
            raise AssertionError("History queries must use a dictionary cursor")
        return self.fake_cursor

    def close(self):
        self.closed = True


class FakeDatabaseFactory:
    def __init__(self, users=None, analyses=None, fail=False):
        self.users = users if users is not None else USERS
        self.analyses = analyses if analyses is not None else ANALYSES
        self.fail = fail
        self.connections: list[FakeConnection] = []

    def __call__(self):
        connection = FakeConnection(self.users, self.analyses, fail=self.fail)
        self.connections.append(connection)
        return connection


class AnalysisHistoryAuthorizationTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True, SECRET_KEY="history-test-secret")
        self.client = app_module.app.test_client()
        self.database = FakeDatabaseFactory()
        self.connection_patch = patch.object(app_module, "get_db_connection", self.database)
        self.connection_patch.start()

    def tearDown(self):
        self.connection_patch.stop()

    def sign_in(self, user_id, email="", is_admin=False):
        with self.client.session_transaction() as current_session:
            current_session["user_id"] = user_id
            current_session["email"] = email
            current_session["is_admin"] = is_admin

    def executed_queries(self):
        if not self.database.connections:
            return []
        return self.database.connections[-1].fake_cursor.executed

    def assert_database_resources_closed(self):
        connection = self.database.connections[-1]
        self.assertTrue(connection.fake_cursor.closed)
        self.assertTrue(connection.closed)

    def test_anonymous_request_is_rejected_without_database_access(self):
        response = self.client.get("/analyses?email=bob@example.com")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"detail": "Authentication required"})
        self.assertEqual(self.database.connections, [])

    def test_authenticated_user_gets_only_own_history_without_email(self):
        self.sign_in(1, "alice@example.com")

        response = self.client.get("/analyses")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["analyses"][0]["id"], 101)
        self.assertNotIn("Bob-only", response.get_data(as_text=True))
        history_queries = [query for query, _ in self.executed_queries() if "from resume_analyses" in query]
        self.assertEqual(len(history_queries), 1)
        self.assertEqual(self.executed_queries()[-1][1], (1,))
        self.assert_database_resources_closed()

    def test_own_email_is_accepted_but_history_still_uses_session_user_id(self):
        self.sign_in(1, "stale-email@example.com")

        response = self.client.get("/analyses?email=ALICE%40EXAMPLE.COM")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["analyses"][0]["id"], 101)
        self.assertFalse(any("where email = %s" in query for query, _ in self.executed_queries()))
        self.assertEqual(self.executed_queries()[-1][1], (1,))

    def test_regular_user_cannot_request_another_users_history(self):
        self.sign_in(1, "alice@example.com")

        response = self.client.get("/analyses?email=bob@example.com")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {"detail": "You can only view your own analysis history"})
        self.assertNotIn("Bob-only", response.get_data(as_text=True))
        self.assertFalse(any("where email = %s" in query for query, _ in self.executed_queries()))
        self.assertFalse(any("from resume_analyses" in query for query, _ in self.executed_queries()))
        self.assert_database_resources_closed()

    def test_forged_admin_cookie_flag_does_not_override_live_user_role(self):
        self.sign_in(4, "demoted@example.com", is_admin=True)

        response = self.client.get("/analyses?email=bob@example.com")

        self.assertEqual(response.status_code, 403)
        with self.client.session_transaction() as current_session:
            self.assertFalse(current_session["is_admin"])

    def test_forged_admin_cookie_flag_cannot_access_admin_api(self):
        self.sign_in(4, "demoted@example.com", is_admin=True)

        with patch.object(app_module, "build_admin_dashboard_payload") as build_dashboard:
            response = self.client.get("/admin/dashboard-insights")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {"detail": "Admin access required"})
        build_dashboard.assert_not_called()
        with self.client.session_transaction() as current_session:
            self.assertFalse(current_session["is_admin"])
        self.assert_database_resources_closed()

    def test_live_database_admin_can_access_admin_api_despite_stale_cookie_flag(self):
        self.sign_in(9, "admin@example.com", is_admin=False)
        expected_payload = {"summary": {"registered_users": 3}}

        with patch.object(app_module, "build_admin_dashboard_payload", return_value=expected_payload) as build_dashboard:
            response = self.client.get("/admin/dashboard-insights")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), expected_payload)
        build_dashboard.assert_called_once_with()
        with self.client.session_transaction() as current_session:
            self.assertTrue(current_session["is_admin"])
        self.assert_database_resources_closed()

    def test_forged_admin_cookie_flag_cannot_access_admin_html_page(self):
        self.sign_in(4, "demoted@example.com", is_admin=True)

        response = self.client.get("/job_data_insights.html")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/home_page.html?admin_required=1")
        with self.client.session_transaction() as current_session:
            self.assertFalse(current_session["is_admin"])
        self.assert_database_resources_closed()

    def test_live_admin_can_request_another_users_history(self):
        self.sign_in(9, "admin@example.com", is_admin=False)

        response = self.client.get("/analyses?email=" + quote("bob@example.com"))

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["analyses"][0]["id"], 202)
        self.assertNotIn("Alice-only", response.get_data(as_text=True))
        self.assertEqual(self.executed_queries()[-1][1], (2,))
        with self.client.session_transaction() as current_session:
            self.assertTrue(current_session["is_admin"])
        self.assert_database_resources_closed()

    def test_admin_request_for_unknown_user_returns_not_found(self):
        self.sign_in(9, "admin@example.com", is_admin=True)

        response = self.client.get("/analyses?email=missing@example.com")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"detail": "User not found"})
        self.assertFalse(any("from resume_analyses" in query for query, _ in self.executed_queries()))

    def test_admin_request_with_invalid_email_returns_bad_request(self):
        self.sign_in(9, "admin@example.com", is_admin=True)

        response = self.client.get("/analyses?email=not-an-email")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"detail": "Please provide a valid email address"})
        self.assertFalse(any("where email = %s" in query for query, _ in self.executed_queries()))

    def test_deleted_session_user_is_rejected_and_session_is_cleared(self):
        self.sign_in(999, "deleted@example.com", is_admin=True)

        response = self.client.get("/analyses")

        self.assertEqual(response.status_code, 401)
        with self.client.session_transaction() as current_session:
            self.assertNotIn("user_id", current_session)
            self.assertNotIn("is_admin", current_session)
        self.assert_database_resources_closed()

    def test_malformed_session_user_id_is_rejected_without_database_access(self):
        self.sign_in("not-an-integer", "alice@example.com", is_admin=True)

        response = self.client.get("/analyses")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.database.connections, [])

    def test_database_error_returns_generic_message_and_closes_resources(self):
        self.sign_in(1, "alice@example.com")
        self.connection_patch.stop()
        self.database = FakeDatabaseFactory(fail=True)
        self.connection_patch = patch.object(app_module, "get_db_connection", self.database)
        self.connection_patch.start()

        response = self.client.get("/analyses")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"detail": "Database error while loading analysis history"})
        self.assertNotIn("unavailable", response.get_data(as_text=True))
        self.assert_database_resources_closed()


if __name__ == "__main__":
    unittest.main()
