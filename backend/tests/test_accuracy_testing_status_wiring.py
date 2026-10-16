from __future__ import annotations

import re
import unittest
from pathlib import Path


RESULTS_PAGE = Path(__file__).resolve().parents[2] / "frontend" / "analysis_result.html"


class AccuracyTestingStatusWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page_source = RESULTS_PAGE.read_text(encoding="utf-8")

    def test_results_page_shows_each_excel_export_status_once_per_analysis(self):
        source = self.page_source

        self.assertIn("function showAccuracyTestingSaveStatus(result)", source)
        self.assertIn("result.accuracy_testing_attempted !== true", source)
        self.assertIn('sessionStorage.getItem(notificationKey) === "1"', source)
        self.assertIn('sessionStorage.setItem(notificationKey, "1")', source)
        self.assertRegex(
            source,
            re.compile(
                r"function loadAnalysisResult\(\).*?"
                r"showAccuracyTestingSaveStatus\(result\);",
                re.DOTALL,
            ),
        )

    def test_results_page_has_messages_for_saved_pending_and_warning_outcomes(self):
        source = self.page_source

        self.assertIn("Saved to Excel Test Run History as UNREVIEWED.", source)
        self.assertIn("Saved to the ATS accuracy workbook and Test Run History.", source)
        self.assertIn(
            "Excel is open. Result queued; close Excel and run one more analysis.",
            source,
        )
        self.assertIn("result.accuracy_testing_warning", source)
        self.assertIn('showToast(String(result.accuracy_testing_warning), "error")', source)


if __name__ == "__main__":
    unittest.main()
