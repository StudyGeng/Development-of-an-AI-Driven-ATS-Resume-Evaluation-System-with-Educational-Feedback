from __future__ import annotations

import re
import unittest
from pathlib import Path


FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


class HistoryButtonWiringTests(unittest.TestCase):
    def test_history_button_is_passed_to_modal_controller_on_both_pages(self):
        expected_wiring = re.compile(
            r"createHistoryModal\(\{\s*"
            r"modal:\s*historyModal,\s*"
            r"openButton:\s*openHistoryBtn,"
        )

        for page_name in ("home_page.html", "analysis_result.html"):
            with self.subTest(page=page_name):
                page_source = (FRONTEND_DIR / page_name).read_text(encoding="utf-8")
                self.assertRegex(page_source, expected_wiring)


if __name__ == "__main__":
    unittest.main()
