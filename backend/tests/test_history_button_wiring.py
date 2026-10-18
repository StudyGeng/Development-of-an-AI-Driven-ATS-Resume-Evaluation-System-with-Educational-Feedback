from __future__ import annotations

import re
import unittest
from pathlib import Path


FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


class HistoryButtonWiringTests(unittest.TestCase):
    def test_history_button_opens_account_history_on_both_pages(self):
        for page_name in ("home_page.html", "analysis_result.html"):
            with self.subTest(page=page_name):
                page_source = (FRONTEND_DIR / page_name).read_text(encoding="utf-8")
                self.assertIn('data-account-view="history"', page_source)
                self.assertIn("createHistoryModal({", page_source)
                self.assertRegex(
                    page_source,
                    re.compile(r'openHistoryBtn\.addEventListener\("click",\s*(?:openHistoryModal|\(\) => openProfileModal\("history"\))\)'),
                )
                self.assertIn('openProfileModal("history")', page_source)


if __name__ == "__main__":
    unittest.main()
