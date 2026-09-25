import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from analysis_helpers import compute_tfidf_score  # noqa: E402


with patch.dict(
    os.environ,
    {
        "APP_ENV": "testing",
        "FLASK_SECRET_KEY": "analysis-scoring-tests-only-secret-key-value-1234567890",
        "ACCURACY_TESTING_AUTO_EXPORT": "0",
    },
    clear=False,
):
    app_module = importlib.import_module("app")


class AnalysisScoringTests(unittest.TestCase):
    def test_repeating_keywords_does_not_raise_tfidf_score(self):
        job_text = "Python Flask API development and testing"
        normal_score = compute_tfidf_score("Python Flask API testing", job_text)
        stuffed_score = compute_tfidf_score(("Python " * 100) + "Flask API testing", job_text)

        self.assertAlmostEqual(normal_score, stuffed_score, places=8)

    def test_repeating_keyword_does_not_raise_requirement_score(self):
        requirement = "Python API testing"
        normal_text = "Built and tested a Python API."
        stuffed_text = normal_text + (" Python" * 50)

        normal = app_module.score_requirement_against_resume(
            normal_text,
            set(app_module.tokenize_canonical_keywords(normal_text)),
            requirement,
        )
        stuffed = app_module.score_requirement_against_resume(
            stuffed_text,
            set(app_module.tokenize_canonical_keywords(stuffed_text)),
            requirement,
        )

        self.assertEqual(normal["score"], stuffed["score"])

    def test_negated_skill_is_not_counted_as_positive_evidence(self):
        text = "No experience with Python or SQL, but strong Java project experience."
        tokens = set(app_module.tokenize_canonical_keywords(text))

        self.assertFalse(app_module.keyword_matches_resume("Python", text, tokens))
        self.assertFalse(app_module.keyword_matches_resume("SQL", text, tokens))
        self.assertTrue(app_module.keyword_matches_resume("Java", text, tokens))

    def test_title_fit_uses_most_recent_dated_job_not_old_role(self):
        text = """
        WORK EXPERIENCE
        IT Manager 08/2021 - Present
        Managed infrastructure and service operations.

        PREVIOUS EXPERIENCE
        Software Tester 01/2016 - 05/2017
        Tested web applications.
        """

        self.assertEqual(app_module.extract_most_recent_job_title(text), "IT Manager")
        self.assertLessEqual(app_module.compute_role_title_match_score(text, "Software Tester"), 10)
        self.assertEqual(app_module.compute_role_title_match_score(text, "Computer Services Manager"), 100)
        self.assertFalse(app_module.has_exact_role_title_match(text, "Software Tester"))
        self.assertTrue(app_module.has_exact_role_title_match(text, "Computer Services Manager"))

    def test_undated_target_role_in_summary_is_not_treated_as_held_title(self):
        text = "Career objective: seeking a Software Tester role. Skills include testing and Java."

        self.assertEqual(app_module.compute_role_title_match_score(text, "Software Tester"), 0)
        self.assertFalse(app_module.has_exact_role_title_match(text, "Software Tester"))

    def test_employment_months_union_overlaps_and_recognizes_admin_title(self):
        text = """
        WORK EXPERIENCE
        IT Manager 01/2020 - 12/2020
        System Admin, Example Company 06/2020 - 06/2021
        """

        entries = app_module.extract_employment_entries(text)

        self.assertEqual([entry["title"] for entry in entries], ["IT Manager", "System Admin"])
        self.assertEqual(app_module.get_total_employment_months(entries), 18)

    def test_skills_heading_and_optional_profile_link_are_not_false_warnings(self):
        filler = " ".join(
            ["Documented operational procedures and prepared clear team reports for service delivery."] * 35
        )
        text = f"""
        FIRST LAST
        first.last@example.com | +60 12 345 6789
        WORK EXPERIENCE
        IT Manager 08/2021 - Present
        Improved response time by 30 percent and supported 50 customers.
        Reduced processing time by 20 percent across 40 requests.
        Increased documented completion by 15 percent for 12 projects.
        SKILLS
        Communication
        PROJECTS
        Operations Review
        EDUCATION
        Bachelor of Information Technology
        {filler}
        """
        profile = app_module.extract_resume_profile(text)
        quality = app_module.analyze_resume_quality(
            text,
            "resume.pdf",
            {
                "file_type": "pdf",
                "raw_text_length": len(text),
                "embedded_image_count": 0,
                "ocr_used": False,
            },
            profile,
        )

        self.assertEqual(quality["issues"], [])
        self.assertEqual(quality["quality_score"], 100)

    def test_ten_quality_penalty_points_cost_two_overall_points(self):
        score_with_issues = app_module.compute_final_readiness_score(80, 80, 80, 90, 80, 80)
        score_without_issues = app_module.compute_final_readiness_score(80, 80, 80, 100, 80, 80)
        self.assertEqual(round(score_without_issues - score_with_issues, 2), 2.0)

    def test_interleaved_sidebar_heading_is_flagged_without_false_skill_warning(self):
        text = """
        CONTACT
        First Last
        IT Manager
        +44 1234567890
        WORK EXPERIENCE
        first.last@example.com
        SKILLS
        Example Company
        IT Manager 08/2021 - Present
        Computer Programming
        Troubleshooting
        Technical Writing
        Data Management and Analytics
        Team Leadership
        Project Management
        Wireshark
        Ansible
        Improved response time by 30% for 50 customers.
        EDUCATION
        Bachelor of Information Technology
        """
        profile = app_module.extract_resume_profile(text)
        quality = app_module.analyze_resume_quality(
            text,
            "it-manager.pdf",
            {"file_type": "pdf", "raw_text_length": len(text), "embedded_image_count": 0},
            profile,
        )
        issue_titles = [issue["title"] for issue in quality["issues"]]

        self.assertTrue(app_module.has_interleaved_section_heading_risk(text))
        self.assertIn("Possible multi-column reading-order issue", issue_titles)
        self.assertNotIn("Dedicated technical skills section not found", issue_titles)
        self.assertNotIn("Missing professional profile link", issue_titles)
        self.assertGreaterEqual(len(profile.skills), 4)

    def test_dated_project_is_not_treated_as_employment(self):
        text = """
        WORK EXPERIENCE
        IT Support Technician 01/2022 - 12/2022
        PROJECTS
        Software Tester Dashboard 2023 - 2024
        """

        entries = app_module.extract_employment_entries(text)

        self.assertEqual([entry["title"] for entry in entries], ["IT Support Technician"])
        self.assertEqual(app_module.extract_most_recent_job_title(text), "IT Support Technician")

    def test_qa_engineer_is_a_software_tester_title_alias(self):
        text = """
        WORK EXPERIENCE
        QA Engineer Jan 2024 - Present
        Automated regression tests.
        """

        self.assertEqual(app_module.compute_role_title_match_score(text, "Software Tester"), 100)
        self.assertTrue(app_module.has_exact_role_title_match(text, "Software Tester"))

    def test_generic_test_word_does_not_prove_automation_or_unit_testing(self):
        score = app_module.compute_role_evidence_score(
            "WORK EXPERIENCE\nTested software and documented results.",
            "Software Tester",
            [],
        )

        self.assertLess(score, 70)

    def test_exact_recent_title_outranks_unrelated_historical_evidence(self):
        current_title_role = app_module.compute_database_role_confidence_score(32.78, 50, 100, True)
        old_testing_role = app_module.compute_database_role_confidence_score(44.2, 98, 5.22, False)

        self.assertGreater(current_title_role, old_testing_role)

    def test_unrecognized_work_dates_are_an_advisory_quality_warning(self):
        text = """
        WORK EXPERIENCE
        Software Developer at Example Company
        Built and tested an application.
        SKILLS
        Python Java SQL Git
        PROJECTS
        Example System
        EDUCATION
        Bachelor of Computer Science
        """
        profile = app_module.extract_resume_profile(text)
        quality = app_module.analyze_resume_quality(
            text,
            "resume.pdf",
            {"file_type": "pdf", "raw_text_length": len(text), "embedded_image_count": 0},
            profile,
        )

        timeline_issue = next(
            issue for issue in quality["issues"] if issue["title"] == "Employment dates not recognizable"
        )
        self.assertEqual(timeline_issue["penalty"], 4)


if __name__ == "__main__":
    unittest.main()
