from __future__ import annotations

import importlib
import io
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
        "FLASK_SECRET_KEY": "ownership-tests-only-secret-key-value-1234567890",
    },
    clear=False,
):
    app_module = importlib.import_module("app")


USERS = {
    1: {"id": 1, "email": "alice@example.com", "is_admin": 0},
    2: {"id": 2, "email": "bob@example.com", "is_admin": 0},
    9: {"id": 9, "email": "admin@example.com", "is_admin": 1},
}

ANALYSES = {
    101: {"id": 101, "user_id": 1},
    202: {"id": 202, "user_id": 2},
    303: {"id": 303, "user_id": None},
}


class OwnershipCursor:
    def __init__(self, database):
        self.database = database
        self.result = None
        self.lastrowid = 700
        self.closed = False

    def execute(self, query, params=()):
        normalized_query = " ".join(query.lower().split())
        safe_params = tuple(params)
        self.database.executed.append((normalized_query, safe_params))

        if "from users" in normalized_query and "where id = %s" in normalized_query:
            user = self.database.users.get(int(safe_params[0]))
            self.result = dict(user) if user else None
            return

        if "from users" in normalized_query and "where email = %s" in normalized_query:
            requested_email = str(safe_params[0]).lower()
            user = next(
                (
                    candidate
                    for candidate in self.database.users.values()
                    if candidate["email"].lower() == requested_email
                ),
                None,
            )
            self.result = dict(user) if user else None
            return

        if "from resume_analyses" in normalized_query and "where id = %s" in normalized_query:
            analysis = self.database.analyses.get(int(safe_params[0]))
            self.result = dict(analysis) if analysis else None
            return

        if normalized_query.startswith("insert into download_history"):
            self.database.download_inserts.append(safe_params)
            self.result = None
            return

        raise AssertionError(f"Unexpected ownership query: {normalized_query}")

    def fetchone(self):
        return self.result

    def fetchall(self):
        return list(self.result or [])

    def close(self):
        self.closed = True


class OwnershipConnection:
    def __init__(self, database):
        self.database = database
        self.cursors = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self, dictionary=False):
        cursor = OwnershipCursor(self.database)
        self.cursors.append(cursor)
        return cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class OwnershipDatabase:
    def __init__(self):
        self.users = USERS
        self.analyses = ANALYSES
        self.connections = []
        self.executed = []
        self.download_inserts = []

    def __call__(self):
        connection = OwnershipConnection(self)
        self.connections.append(connection)
        return connection


class AnalysisOwnershipAuthorizationTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True, SECRET_KEY="ownership-test-secret")
        self.client = app_module.app.test_client()
        self.database = OwnershipDatabase()
        self.database_patch = patch.object(app_module, "get_db_connection", self.database)
        self.database_patch.start()

    def tearDown(self):
        self.database_patch.stop()

    def sign_in(self, user_id, email="", is_admin=False):
        with self.client.session_transaction() as current_session:
            current_session["user_id"] = user_id
            current_session["email"] = email
            current_session["is_admin"] = is_admin

    def post_download(self, analysis_id_marker="omitted"):
        payload = {
            "email": "bob@example.com",
            "downloaded_file_name": "comments.txt",
        }
        if analysis_id_marker != "omitted":
            payload["analysis_id"] = analysis_id_marker
        return self.client.post("/downloads", json=payload)

    def test_analyze_file_requires_authentication_before_file_validation(self):
        response = self.client.post("/analyze-file", data={})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"detail": "Authentication required"})
        self.assertEqual(self.database.connections, [])

    def test_analyze_file_persists_with_live_session_user_not_form_email(self):
        self.sign_in(1, email="bob@example.com", is_admin=True)
        minimal_analysis = {
            "compatibility": {"matching_percentage": 80.0},
            "feedback": "Test feedback",
            "resume_excerpt": "Test excerpt",
        }

        with (
            patch.object(app_module, "extract_resume_text", return_value=("resume text", {})),
            patch.object(app_module, "rank_database_role_matches", return_value=[]),
            patch.object(app_module, "pick_recommended_database_job", return_value=(None, None, None)),
            patch.object(app_module, "build_role_recommendation", return_value={"top_role_matches": []}),
            patch.object(app_module, "build_analysis_payload", return_value=minimal_analysis),
            patch.object(app_module, "extract_resume_contact", return_value={}),
            patch.object(app_module, "build_part8_recommendation_module", return_value={}),
            patch.object(app_module, "persist_analysis", return_value=501) as persist_analysis,
            patch.object(app_module, "update_accuracy_testing_workbook", return_value={}),
        ):
            response = self.client.post(
                "/analyze-file",
                data={
                    "resume_file": (io.BytesIO(b"fake pdf"), "resume.pdf"),
                    "job_description": "Build and test software",
                    "target_role": "Software Developer",
                    "user_email": "bob@example.com",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        persist_analysis.assert_called_once()
        call_args, call_kwargs = persist_analysis.call_args
        persisted_user_id = call_kwargs.get("user_id")
        if persisted_user_id is None and len(call_args) >= 4:
            persisted_user_id = call_args[3]
        self.assertEqual(persisted_user_id, 1)
        self.assertNotEqual(persisted_user_id, "bob@example.com")
        with self.client.session_transaction() as current_session:
            self.assertEqual(current_session["email"], "alice@example.com")
            self.assertFalse(current_session["is_admin"])

    def test_download_requires_authentication(self):
        response = self.post_download(101)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"detail": "Authentication required"})
        self.assertEqual(self.database.connections, [])

    def test_download_for_owned_analysis_uses_session_user_id(self):
        self.sign_in(1, email="bob@example.com")

        response = self.post_download(101)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.database.download_inserts), 1)
        inserted = self.database.download_inserts[0]
        self.assertEqual(inserted[0], 1)
        self.assertEqual(inserted[1], 101)

    def test_download_for_another_users_analysis_is_forbidden(self):
        self.sign_in(1, email="alice@example.com")

        response = self.post_download(202)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.database.download_inserts, [])

    def test_forged_email_and_admin_flag_do_not_allow_foreign_analysis(self):
        self.sign_in(1, email="bob@example.com", is_admin=True)

        response = self.post_download(202)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.database.download_inserts, [])
        with self.client.session_transaction() as current_session:
            self.assertEqual(current_session["email"], "alice@example.com")
            self.assertFalse(current_session["is_admin"])

    def test_live_admin_cannot_record_download_for_another_users_analysis(self):
        self.sign_in(9, email="admin@example.com", is_admin=True)

        response = self.post_download(202)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.database.download_inserts, [])

    def test_generated_resume_snapshot_can_only_be_loaded_by_its_owner(self):
        self.database.analyses[101] = {
            "id": 101,
            "user_id": 1,
            "analysis_json": '{"selected_role": "Software Engineer"}',
            "resume_excerpt": "Example excerpt",
        }

        owned_status, owned_analysis = app_module.load_owned_analysis_snapshot(1, 101)
        forbidden_status, forbidden_analysis = app_module.load_owned_analysis_snapshot(2, 101)

        self.assertEqual(owned_status, "owned")
        self.assertEqual(owned_analysis["analysis_id"], 101)
        self.assertEqual(owned_analysis["selected_role"], "Software Engineer")
        self.assertEqual(owned_analysis["resume_excerpt"], "Example excerpt")
        self.assertEqual(forbidden_status, "forbidden")
        self.assertIsNone(forbidden_analysis)
        self.assertTrue(all(connection.closed for connection in self.database.connections))

    def test_download_for_nonexistent_analysis_returns_not_found(self):
        self.sign_in(1, email="alice@example.com")

        response = self.post_download(999)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.database.download_inserts, [])

    def test_download_for_orphaned_analysis_is_forbidden(self):
        self.sign_in(1, email="alice@example.com")

        response = self.post_download(303)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.database.download_inserts, [])

    def test_download_without_analysis_id_is_allowed_for_session_user(self):
        self.sign_in(1, email="bob@example.com")

        response = self.post_download()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.database.download_inserts), 1)
        inserted = self.database.download_inserts[0]
        self.assertEqual(inserted[0], 1)
        self.assertIsNone(inserted[1])

    def test_download_with_explicit_null_analysis_id_is_allowed(self):
        self.sign_in(1, email="alice@example.com")

        response = self.post_download(None)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.database.download_inserts), 1)
        self.assertIsNone(self.database.download_inserts[0][1])

    def test_download_rejects_malformed_or_non_positive_analysis_id(self):
        self.sign_in(1, email="alice@example.com")

        for invalid_analysis_id in ("not-an-id", True, 1.5, 0, -1):
            with self.subTest(analysis_id=invalid_analysis_id):
                self.database.download_inserts.clear()
                response = self.post_download(invalid_analysis_id)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.database.download_inserts, [])


if __name__ == "__main__":
    unittest.main()
