from __future__ import annotations

import unittest
from pathlib import Path


FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


class HistoryButtonWiringTests(unittest.TestCase):
    def test_guest_account_icon_offers_sign_in_and_history_on_every_page(self):
        common_source = (FRONTEND_DIR / "app_common.js").read_text(encoding="utf-8")
        self.assertIn('hasAccount ? ["info", "settings", "history"] : ["access", "history"]', common_source)
        self.assertIn('button.dataset.accountGo', common_source)
        for page_name in ("home_page.html", "analysis_result.html", "job_data_insights.html"):
            with self.subTest(page=page_name):
                page_source = (FRONTEND_DIR / page_name).read_text(encoding="utf-8")
                self.assertIn('id="openAuthModalBtn"', page_source)
                self.assertIn('aria-controls="profileModal"', page_source)
                self.assertNotIn('id="openHistoryBtn"', page_source)
                self.assertIn('data-account-view="access"', page_source)
                self.assertIn('data-account-view="history"', page_source)
                self.assertIn('data-account-go="history"', page_source)
                self.assertIn('data-account-auth-action="login"', page_source)
                self.assertIn('data-account-auth-action="register"', page_source)
                self.assertIn("createHistoryModal({", page_source)

    def test_guest_sign_in_action_opens_existing_auth_flow(self):
        for page_name in ("home_page.html", "analysis_result.html"):
            with self.subTest(page=page_name):
                page_source = (FRONTEND_DIR / page_name).read_text(encoding="utf-8")
                self.assertIn('closeProfileModal();', page_source)
                self.assertIn('openAuthModal(mode);', page_source)


if __name__ == "__main__":
    unittest.main()
