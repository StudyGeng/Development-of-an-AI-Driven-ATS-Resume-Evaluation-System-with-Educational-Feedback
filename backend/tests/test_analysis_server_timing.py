from __future__ import annotations

import importlib
import io
import os
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

with patch.dict(
    os.environ,
    {
        "APP_ENV": "testing",
        "FLASK_SECRET_KEY": "analysis-timing-tests-only-secret-key-value-1234567890",
        "ACCURACY_TESTING_AUTO_EXPORT": "0",
    },
    clear=False,
):
    app_module = importlib.import_module("app")


class AnalysisServerTimingTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True, SECRET_KEY="analysis-timing-test-secret")
        self.client = app_module.app.test_client()

    def test_success_exposes_only_numeric_phase_durations(self):
        private_resume_text = "Private Candidate email@example.com built Python APIs"
        analysis = {
            "compatibility": {"matching_percentage": 74.0},
            "feedback": "Private feedback",
            "resume_excerpt": "Private excerpt",
        }
        with ExitStack() as stack:
            stack.enter_context(patch.object(app_module, "perf_counter", side_effect=[1.00, 1.01, 1.03, 1.06, 1.10, 1.15, 1.21]))
            stack.enter_context(patch.object(app_module, "get_authenticated_session_user", return_value={"id": 7}))
            stack.enter_context(patch.object(app_module, "should_enforce_extra_rate_limits", return_value=False))
            stack.enter_context(patch.object(app_module, "extract_resume_text", return_value=(private_resume_text, {"file_type": "pdf"})))
            stack.enter_context(patch.object(app_module, "resolve_database_job_description", return_value=None))
            stack.enter_context(patch.object(app_module, "rank_database_role_matches", return_value=[]))
            stack.enter_context(patch.object(app_module, "pick_recommended_database_job", return_value=(None, None, None)))
            stack.enter_context(patch.object(app_module, "build_role_recommendation", return_value={"top_role_matches": []}))
            stack.enter_context(patch.object(app_module, "build_analysis_payload", return_value=analysis))
            stack.enter_context(patch.object(app_module, "extract_resume_contact", return_value={}))
            stack.enter_context(patch.object(app_module, "build_part8_recommendation_module", return_value={}))
            stack.enter_context(patch.object(app_module, "persist_analysis", return_value=123))
            stack.enter_context(patch.object(app_module, "update_accuracy_testing_workbook", return_value={}))

            response = self.client.post(
                "/analyze-file",
                data={
                    "resume_file": (io.BytesIO(b"mock PDF"), "private-resume.pdf"),
                    "job_description": "Private software job",
                    "target_role": "Private Analyst",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["analysis_id"], 123)
        self.assertTrue(response.get_json()["history_saved"])
        self.assertEqual(
            response.headers["Server-Timing"],
            "auth;dur=10.00, extract;dur=20.00, role;dur=30.00, "
            "report;dur=40.00, save;dur=50.00, total;dur=210.00",
        )
        for sensitive_value in ("Private", "email@example.com", "private-resume.pdf"):
            self.assertNotIn(sensitive_value, response.headers["Server-Timing"])

    def test_unauthenticated_response_has_no_analysis_timing(self):
        with patch.object(app_module, "get_authenticated_session_user", return_value=None):
            response = self.client.post("/analyze-file", data={})

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("Server-Timing", response.headers)


if __name__ == "__main__":
    unittest.main()
