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

import app_config  # noqa: E402
import database  # noqa: E402


with patch.dict(
    os.environ,
    {
        "APP_ENV": "testing",
        "FLASK_SECRET_KEY": "accuracy-export-tests-only-secret-key-1234567890",
        "ACCURACY_TESTING_AUTO_EXPORT": "0",
    },
    clear=False,
), patch.object(database, "init_mysql_database"):
    app_module = importlib.import_module("app")


class FakeCell:
    def __init__(self, value=None):
        self.value = value


class FakeSheet:
    def __init__(self):
        self.max_row = 34
        self._cells: dict[tuple[int, int], FakeCell] = {}

    def cell(self, row: int, column: int) -> FakeCell:
        return self._cells.setdefault((row, column), FakeCell())


class FakeWorkbook:
    def __init__(self, sheet: FakeSheet):
        self.sheet = sheet
        self.sheetnames = ["Accuracy Testing"]

    def __getitem__(self, name: str) -> FakeSheet:
        if name != "Accuracy Testing":
            raise KeyError(name)
        return self.sheet


def prepared_workbook(source_file: str = "SD01.pdf") -> tuple[FakeWorkbook, FakeSheet]:
    sheet = FakeSheet()
    sheet.cell(5, 1).value = "SD01"
    sheet.cell(5, 2).value = "Software Developer"
    sheet.cell(5, 15).value = source_file
    return FakeWorkbook(sheet), sheet


def sample_analysis(uploaded_name: str = "SD01.pdf") -> dict:
    return {
        "uploaded_resume_name": uploaded_name,
        "matching_percentage": 82.5,
        "job_description_analysis": {
            "missing_keywords": ["testing"],
            "matched_keywords": ["python"],
        },
        "role_recommendation": {
            "recommended_role": "Software Developer",
            "reason_summary": "The recommendation is supported by Python development evidence.",
        },
    }


class AccuracyTestingConfigurationTests(unittest.TestCase):
    def test_export_defaults_off_in_development(self):
        with patch.dict(os.environ, {"APP_ENV": "development"}, clear=True):
            self.assertFalse(app_config.get_accuracy_testing_auto_export_enabled())

    def test_export_requires_explicit_local_opt_in(self):
        with patch.dict(
            os.environ,
            {"APP_ENV": "development", "ACCURACY_TESTING_AUTO_EXPORT": "1"},
            clear=True,
        ):
            self.assertTrue(app_config.get_accuracy_testing_auto_export_enabled())

    def test_export_never_enables_in_production(self):
        with patch.dict(
            os.environ,
            {"APP_ENV": "production", "ACCURACY_TESTING_AUTO_EXPORT": "1"},
            clear=True,
        ):
            self.assertFalse(app_config.get_accuracy_testing_auto_export_enabled())

    def test_invalid_export_setting_is_rejected(self):
        with patch.dict(
            os.environ,
            {"APP_ENV": "development", "ACCURACY_TESTING_AUTO_EXPORT": "maybe"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "ACCURACY_TESTING_AUTO_EXPORT"):
                app_config.get_accuracy_testing_auto_export_enabled()


class AccuracyTestingWorkbookTests(unittest.TestCase):
    def test_system_write_leaves_human_review_fields_blank(self):
        workbook, sheet = prepared_workbook()

        row, warning = app_module.write_accuracy_testing_analysis_row(
            workbook,
            sample_analysis(),
        )

        self.assertEqual((row, warning), (5, ""))
        self.assertEqual(sheet.cell(5, 3).value, "Software Developer")
        self.assertEqual(sheet.cell(5, 4).value, 82.5)
        self.assertIn("Missing: testing", sheet.cell(5, 8).value)
        self.assertIn("Python development evidence", sheet.cell(5, 9).value)
        for column in (5, 6, 7, 14):
            self.assertIsNone(sheet.cell(5, column).value)

    def test_system_write_preserves_existing_human_review(self):
        workbook, sheet = prepared_workbook()
        expected_review = {5: "No", 6: "Yes", 7: "No", 14: "Human tester explanation"}
        for column, value in expected_review.items():
            sheet.cell(5, column).value = value

        row, warning = app_module.write_accuracy_testing_analysis_row(
            workbook,
            sample_analysis(),
        )

        self.assertEqual((row, warning), (5, ""))
        for column, value in expected_review.items():
            self.assertEqual(sheet.cell(5, column).value, value)

    def test_source_file_selects_only_a_prepared_case(self):
        workbook, _ = prepared_workbook("software-developer_1.pdf")

        sheet, row, warning = app_module.find_accuracy_testing_row(
            workbook,
            sample_analysis("software-developer_1.pdf"),
        )

        self.assertIsNotNone(sheet)
        self.assertEqual((row, warning), (5, ""))

    def test_unregistered_resume_does_not_create_a_test_case(self):
        workbook, sheet = prepared_workbook("software-developer_1.pdf")

        row, warning = app_module.write_accuracy_testing_analysis_row(
            workbook,
            sample_analysis("unregistered-resume.pdf"),
        )

        self.assertIsNone(row)
        self.assertIn("not registered", warning)
        self.assertEqual(sheet.cell(5, 1).value, "SD01")
        self.assertIsNone(sheet.cell(6, 1).value)

    def test_production_export_stops_before_filesystem_or_queue_access(self):
        with patch.dict(
            os.environ,
            {"APP_ENV": "production", "ACCURACY_TESTING_AUTO_EXPORT": "1"},
            clear=False,
        ), patch.object(app_module.os.path, "exists") as path_exists, patch.object(
            app_module, "load_pending_accuracy_testing_analyses"
        ) as load_pending, patch.object(
            app_module, "queue_accuracy_testing_analysis"
        ) as queue_analysis:
            result = app_module.update_accuracy_testing_workbook(sample_analysis())

        self.assertFalse(result["accuracy_testing_saved"])
        self.assertIn("production", result["accuracy_testing_warning"])
        path_exists.assert_not_called()
        load_pending.assert_not_called()
        queue_analysis.assert_not_called()

    def test_disabled_local_export_stops_before_filesystem_access(self):
        with patch.dict(
            os.environ,
            {"APP_ENV": "testing", "ACCURACY_TESTING_AUTO_EXPORT": "0"},
            clear=False,
        ), patch.object(app_module.os.path, "exists") as path_exists:
            result = app_module.update_accuracy_testing_workbook(sample_analysis())

        self.assertFalse(result["accuracy_testing_saved"])
        self.assertIn("disabled", result["accuracy_testing_warning"])
        path_exists.assert_not_called()

    def test_production_health_hides_workbook_diagnostics(self):
        client = app_module.app.test_client()
        with patch.dict(os.environ, {"APP_ENV": "production"}, clear=False):
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertNotIn("accuracy_testing_workbook", payload)
        self.assertNotIn("accuracy_testing_workbook_exists", payload)
        self.assertNotIn("accuracy_testing_pending_count", payload)


if __name__ == "__main__":
    unittest.main()
