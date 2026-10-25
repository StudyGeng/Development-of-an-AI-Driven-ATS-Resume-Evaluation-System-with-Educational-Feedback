import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from analysis_helpers import compute_tfidf_score, get_scoring_cache, scoring_cache_scope  # noqa: E402
from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: E402


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
    def test_fast_tfidf_matches_two_document_vectorizer(self):
        document_pairs = [
            ("", ""),
            ("the and or", "a to in"),
            ("Python Flask API testing", "Python API development"),
            ("SQL SQL SQL Docker", "Docker and SQL"),
            ("C++ .NET React.js", "React JavaScript and C++"),
            ("Multilingual résumé: Bahasa Melayu and Python", "Python developer"),
        ]

        for resume_text, job_text in document_pairs:
            with self.subTest(resume=resume_text, job=job_text):
                vectorizer = TfidfVectorizer(stop_words="english", binary=True)
                try:
                    matrix = vectorizer.fit_transform([resume_text, job_text])
                    expected = float((matrix[0] @ matrix[1].T).toarray()[0][0]) * 100.0
                except ValueError:
                    expected = 0.0
                self.assertAlmostEqual(compute_tfidf_score(resume_text, job_text), expected, places=10)

    def test_scoring_cache_is_discarded_after_a_ranking_run(self):
        self.assertIsNone(get_scoring_cache())
        with scoring_cache_scope():
            first = compute_tfidf_score("Python and SQL", "Python developer")
            self.assertEqual(compute_tfidf_score("Python and SQL", "Python developer"), first)
            self.assertEqual(len(get_scoring_cache().tfidf), 1)
        self.assertIsNone(get_scoring_cache())

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

    def test_indexed_phrase_evidence_matches_full_clause_scan(self):
        import re

        def full_scan(phrase, resume_text):
            phrase_tokens = app_module.tokenize_canonical_keywords(phrase)
            if not phrase_tokens:
                return False
            clauses = re.split(r"[\n.!?;]+|\b(?:but|however|although)\b", resume_text, flags=re.I)
            width = len(phrase_tokens)
            for clause in clauses:
                tokens = [
                    app_module.normalize_keyword_token(token)
                    for token in re.findall(r"[a-zA-Z][a-zA-Z+.#/-]*", clause.lower())
                ]
                for index in range(0, len(tokens) - width + 1):
                    if tokens[index : index + width] == phrase_tokens and not (
                        set(tokens[max(0, index - 6) : index]) & app_module.NEGATED_EVIDENCE_MARKERS
                    ):
                        return True
            return False

        texts = [
            "No experience with Python or SQL, but strong Java project experience.",
            "Not used in this role: one two three four five six seven Python scripts.",
            "Never deployed Docker. Built machine learning models with Docker later",
            "Machine learning; learning machine. Built REST APIs and APIs testing",
        ]
        phrases = ["python", "sql", "java", "docker", "machine learning", "learning machine", "rest api", "api testing", "kotlin"]
        for text in texts:
            for phrase in phrases:
                expected = full_scan(phrase, text)
                self.assertEqual(app_module.phrase_has_positive_resume_evidence(phrase, text), expected, (phrase, text))
                with scoring_cache_scope():
                    self.assertEqual(app_module.phrase_has_positive_resume_evidence(phrase, text), expected, (phrase, text))

    def test_nested_scoring_scope_shares_cache_and_clears_after_outer_scope(self):
        with scoring_cache_scope():
            outer = get_scoring_cache()
            with scoring_cache_scope():
                self.assertIs(get_scoring_cache(), outer)
            self.assertIs(get_scoring_cache(), outer)
        self.assertIsNone(get_scoring_cache())

    def test_role_title_alias_ties_resolve_the_same_in_any_order(self):
        # Both aliases are equally similar to the title, so only a fixed
        # tie-break keeps the score stable across server processes.
        aliases = ["cybersecurity specialist", "data security specialist"]
        scores = []
        for ordered in (aliases, list(reversed(aliases))):
            with patch.dict(app_module.ROLE_TITLE_MATCH_ALIASES, {"data security specialist": ordered}):
                scores.append(
                    app_module.compute_role_title_match_score(
                        "Security Engineer", "Data Security Specialist", "Security Engineer"
                    )
                )
        self.assertEqual(scores[0], scores[1])

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

    def test_title_after_employer_date_line_is_parsed(self):
        text = """
        PROFESSIONAL EXPERIENCE
        SAINS, Kuching, Sarawak Jan 2023 - June 2023
        Full-Stack Developer Intern
        Developed and designed a responsive website.

        New Century, Sarikei, Sarawak July 2023 - Dec 2023
        IT Technician
        Troubleshot hardware, software, and infrastructure issues.

        EDUCATION
        University of Technology Sarawak Feb 2024 - Present
        Bachelor of Computer Science
        """

        entries = app_module.extract_employment_entries(text)

        self.assertEqual(
            [entry["title"] for entry in entries],
            ["Full-Stack Developer Intern", "IT Technician"],
        )
        self.assertEqual(app_module.extract_most_recent_job_title(text), "IT Technician")
        self.assertEqual(app_module.get_total_employment_months(entries), 12)

    def test_career_title_match_credits_relevant_previous_dated_role(self):
        text = """
        PROFESSIONAL EXPERIENCE
        SAINS, Kuching, Sarawak Jan 2023 - June 2023
        Full-Stack Developer Intern
        Developed and tested a responsive web application.

        New Century, Sarikei, Sarawak July 2023 - Dec 2023
        IT Technician
        Troubleshot hardware and infrastructure issues.
        """

        result = app_module.compute_career_role_title_match(
            text,
            "Full Stack Developer",
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["most_recent_title"], "IT Technician")
        self.assertEqual(result["matched_title"], "Full-Stack Developer Intern")
        self.assertFalse(result["matched_title_is_current"])
        self.assertFalse(result["exact_current_title_match"])
        self.assertEqual(result["score"], 85.0)

    def test_explicit_non_it_headline_is_negative_title_evidence(self):
        text = """
        STAFF PHARMACIST   PROFESSIONAL SUMMARY
        Licensed pharmacist experienced in dispensing medication and counselling patients.
        """

        headline = app_module.extract_explicit_resume_headline(text)
        result = app_module.compute_career_role_title_match(
            text,
            "Software Developer",
        )

        self.assertEqual(headline, "Staff Pharmacist")
        self.assertTrue(app_module.is_explicit_non_it_resume_headline(headline))
        self.assertTrue(result["available"])
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["most_recent_title"], "Staff Pharmacist")
        self.assertEqual(result["signal_source"], "non_it_headline")
        self.assertTrue(result["explicit_non_it_headline"])

    def test_non_it_headline_detection_supports_other_professional_titles(self):
        text = """
        ASSISTANT FOOTBALL COACH   PROFILE
        Supported player development, training sessions, and match preparation.
        """

        headline = app_module.extract_explicit_resume_headline(text)

        self.assertEqual(headline, "Assistant Football Coach")
        self.assertTrue(app_module.is_explicit_non_it_resume_headline(headline))

    def test_non_it_headline_survives_whitespace_collapsing(self):
        cleaned = app_module.clean_resume_source_text(
            "         STAFF PHARMACIST       Summary     "
            "Patient counselling and medication dispensing."
        )

        headline = app_module.extract_explicit_resume_headline(cleaned)
        result = app_module.compute_career_role_title_match(
            cleaned,
            "Software Developer",
        )

        self.assertEqual(headline, "Staff Pharmacist")
        self.assertEqual(result["signal_source"], "non_it_headline")
        self.assertTrue(result["explicit_non_it_headline"])

    def test_non_it_headline_flag_is_kept_with_dated_employment(self):
        text = (
            "STAFF PHARMACIST Summary Patient counselling and medication dispensing."
        )
        employment_entries = [
            {
                "title": "Staff Pharmacist",
                "start": app_module.datetime(2022, 1, 1),
                "end": app_module.datetime(2024, 1, 1),
            }
        ]

        result = app_module.compute_career_role_title_match(
            text,
            "Software Developer",
            employment_entries,
        )

        self.assertEqual(result["signal_source"], "employment_title")
        self.assertTrue(result["explicit_non_it_headline"])
        self.assertEqual(result["non_it_headline"], "Staff Pharmacist")

    def test_candidate_name_and_ordinary_summary_are_not_job_headlines(self):
        name_first = """
        NGU MING HENN
        PROFESSIONAL SUMMARY
        Computer Science student with Python and web application projects.
        """
        ordinary_summary = """
        PROFESSIONAL SUMMARY
        Software developer with Python and testing projects.
        """

        self.assertEqual(app_module.extract_explicit_resume_headline(name_first), "")
        self.assertEqual(app_module.extract_explicit_resume_headline(ordinary_summary), "")

    def test_technical_headline_does_not_trigger_non_it_penalty(self):
        text = """
        SOFTWARE DEVELOPER   PROFESSIONAL SUMMARY
        Built Python APIs and tested web applications.
        """

        headline = app_module.extract_explicit_resume_headline(text)
        result = app_module.compute_career_role_title_match(text, "Software Developer")

        self.assertEqual(headline, "Software Developer")
        self.assertFalse(app_module.is_explicit_non_it_resume_headline(headline))
        self.assertFalse(result["available"])
        self.assertEqual(result["signal_source"], "unavailable")

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

    def test_role_specific_software_evidence_does_not_saturate_every_role(self):
        text = (
            "Built a React HTML frontend and Flask backend; integrated a REST API, "
            "MySQL, authentication, and Docker deployment."
        )

        programmer = app_module.compute_role_evidence_score(text, "Computer Programmer", [])
        developer = app_module.compute_role_evidence_score(text, "Software Developer", [])
        designer = app_module.compute_role_evidence_score(text, "Software Designer", [])
        full_stack = app_module.compute_role_evidence_score(text, "Full Stack Developer", [])

        self.assertEqual(programmer, 36.0)
        self.assertEqual(developer, 60.0)
        self.assertEqual(designer, 15.0)
        self.assertEqual(full_stack, 90.0)
        self.assertGreater(full_stack, developer)
        self.assertGreater(developer, designer)

    def test_unrelated_business_impact_does_not_boost_technical_role_evidence(self):
        technical_text = (
            "Built a React HTML frontend and Flask backend; integrated a REST API, "
            "MySQL, authentication, and Docker deployment."
        )
        unrelated_impact_text = technical_text + " Increased retail sales by 30%."

        baseline = app_module.compute_role_evidence_score(
            technical_text,
            "Full Stack Developer",
            [],
        )
        with_unrelated_impact = app_module.compute_role_evidence_score(
            unrelated_impact_text,
            "Full Stack Developer",
            [],
        )

        self.assertEqual(baseline, 90.0)
        self.assertEqual(with_unrelated_impact, baseline)

    def test_specific_frameworks_require_direct_resume_evidence(self):
        text = "Built a Java backend server with a React frontend and deployed the application."
        tokens = set(app_module.tokenize_canonical_keywords(text))

        self.assertFalse(app_module.keyword_matches_resume(".NET", text, tokens))
        self.assertFalse(app_module.keyword_matches_resume("jQuery", text, tokens))
        self.assertFalse(app_module.keyword_matches_resume("Tomcat", text, tokens))
        self.assertTrue(app_module.keyword_matches_resume("Java", text, tokens))

    def test_exact_recent_title_outranks_unrelated_historical_evidence(self):
        current_title_role = app_module.compute_database_role_confidence_score(32.78, 50, 100, True)
        old_testing_role = app_module.compute_database_role_confidence_score(44.2, 98, 5.22, False)

        self.assertGreater(current_title_role, old_testing_role)

    def test_current_exact_title_keeps_evidence_gated_confidence_floor(self):
        score = app_module.compute_database_role_confidence_score(44.2, 98, 100, True)

        self.assertEqual(score, 82.0)

    def test_missing_parsed_title_renormalizes_available_role_evidence(self):
        unavailable_title_score = app_module.compute_database_role_confidence_score(
            44.2,
            98,
            0,
            False,
            False,
        )
        mismatched_title_score = app_module.compute_database_role_confidence_score(
            44.2,
            98,
            0,
            False,
            True,
        )

        self.assertEqual(unavailable_title_score, 52.48)
        self.assertEqual(mismatched_title_score, 34.11)

    def test_role_ranking_distinguishes_unavailable_title_from_mismatched_title(self):
        role = {"id": 1, "role_name": "Software Developer"}
        fit_result = {"combined_score": 44.2, "important_keywords": ["Python"]}
        with (
            patch.object(app_module, "fetch_job_catalog", return_value=([role], {1: ["Python"]})),
            patch.object(app_module, "build_job_description_from_requirements", return_value="Python"),
            patch.object(app_module, "evaluate_job_fit", return_value=fit_result),
            patch.object(app_module, "compute_role_evidence_score", return_value=98),
        ):
            unavailable = app_module.rank_database_role_matches(
                "Skills include Python and software development.",
                limit=1,
            )[0]
            mismatched = app_module.rank_database_role_matches(
                "WORK EXPERIENCE\nIT Manager 01/2024 - Present\nManaged infrastructure.",
                limit=1,
            )[0]

        self.assertFalse(unavailable["title_match_available"])
        self.assertEqual(unavailable["confidence_score"], 52.48)
        self.assertTrue(mismatched["title_match_available"])
        self.assertLess(mismatched["confidence_score"], unavailable["confidence_score"])

    def test_selected_role_calibration_uses_career_title_evidence(self):
        text = """
        PROFESSIONAL EXPERIENCE
        Example Company Jan 2023 - Jun 2023
        Full-Stack Developer Intern
        Built web applications.
        Service Company Jul 2023 - Dec 2023
        IT Technician
        Supported infrastructure.
        """
        job_match = {"role": {"role_name": "Full Stack Developer"}}
        fit_result = {"combined_score": 40.0, "important_keywords": []}

        with patch.object(app_module, "compute_role_evidence_score", return_value=50.0):
            score = app_module.get_calibrated_job_fit_score(text, job_match, fit_result)

        self.assertEqual(score, 56.75)

    def test_selected_non_it_resume_below_35_requires_review(self):
        selected_job = {"role": {"role_name": "Software Developer"}}
        ranked_matches = [
            {
                "role": {"role_name": "Software Developer"},
                "confidence_score": 34.99,
                "fit_result": {"combined_score": 40.0},
                "title_match_source": "non_it_headline",
                "title_match_title": "Staff Pharmacist",
                "explicit_non_it_headline": True,
            }
        ]

        recommendation = app_module.build_role_recommendation(
            "Software Developer",
            selected_job,
            selected_job,
            selected_job,
            {"combined_score": 40.0},
            {"combined_score": 40.0},
            ranked_matches,
        )

        self.assertEqual(recommendation["match_status"], "review_required")
        self.assertTrue(recommendation["human_review_required"])
        self.assertEqual(recommendation["review_threshold"], 35.0)
        self.assertEqual(recommendation["non_it_headline"], "Staff Pharmacist")
        self.assertIn("REVIEW REQUIRED", recommendation["reason_summary"])

    def test_auto_non_it_resume_below_35_has_no_suitable_role(self):
        detected_job = {"role": {"role_name": "Software Developer"}}
        ranked_matches = [
            {
                "role": {"role_name": "Software Developer"},
                "confidence_score": 34.99,
                "fit_result": {"combined_score": 40.0},
                "title_match_source": "non_it_headline",
                "title_match_title": "Assistant Football Coach",
                "explicit_non_it_headline": True,
            }
        ]

        recommendation = app_module.build_role_recommendation(
            "",
            None,
            detected_job,
            detected_job,
            None,
            {"combined_score": 40.0},
            ranked_matches,
        )

        self.assertEqual(recommendation["match_status"], "no_suitable_role")
        self.assertTrue(recommendation["human_review_required"])
        self.assertEqual(recommendation["closest_role"], "Software Developer")
        self.assertEqual(recommendation["closest_role_score"], 34.99)
        self.assertIn("not a confirmed recommendation", recommendation["reason_summary"])

    def test_universal_below_20_rule_does_not_need_non_it_headline(self):
        detected_job = {"role": {"role_name": "Software Developer"}}
        ranked_matches = [
            {
                "role": {"role_name": "Software Developer"},
                "confidence_score": 19.99,
                "fit_result": {"combined_score": 19.99},
            }
        ]

        recommendation = app_module.build_role_recommendation(
            "",
            None,
            detected_job,
            detected_job,
            None,
            {"combined_score": 19.99},
            ranked_matches,
        )

        self.assertEqual(recommendation["match_status"], "no_suitable_role")
        self.assertEqual(recommendation["review_threshold"], 20.0)

    def test_genuine_low_it_evidence_at_or_above_20_remains_a_match(self):
        detected_job = {"role": {"role_name": "Software Developer"}}
        ranked_matches = [
            {
                "role": {"role_name": "Software Developer"},
                "confidence_score": 20.0,
                "fit_result": {"combined_score": 20.0},
                "title_match_source": "unavailable",
                "explicit_non_it_headline": False,
            }
        ]

        recommendation = app_module.build_role_recommendation(
            "",
            None,
            detected_job,
            detected_job,
            None,
            {"combined_score": 20.0},
            ranked_matches,
        )

        self.assertEqual(recommendation["match_status"], "matched")
        self.assertFalse(recommendation["human_review_required"])
        self.assertEqual(recommendation["review_threshold"], 20.0)

    def test_top_role_reason_uses_neutral_evidence_wording(self):
        items = app_module.build_top_role_match_items(
            [
                {
                    "role": {"role_name": "Software Developer"},
                    "confidence_score": 48.43,
                    "fit_result": {
                        "matched_keywords": ["software", "develop"],
                        "missing_keywords": ["debug"],
                    },
                }
            ],
            "",
            "Software Developer",
        )

        self.assertEqual(items[0]["confidence_label"], "Moderate evidence")
        self.assertTrue(items[0]["reason"].startswith("Evidence found:"))
        self.assertNotIn("Strong evidence:", items[0]["reason"])

    def test_top_role_score_display_keeps_one_decimal_near_label_thresholds(self):
        frontend = (BACKEND_DIR.parent / "frontend" / "analysis_result.html").read_text(
            encoding="utf-8"
        )
        renderer = frontend.split("function renderTopRoleMatches", 1)[1].split(
            "function ",
            1,
        )[0]

        self.assertIn("Math.round(score * 10) / 10", renderer)
        self.assertNotIn('Math.round(score) + "/100"', renderer)

    def test_frontend_uses_backend_review_status_without_blanket_50_gate(self):
        frontend = (BACKEND_DIR.parent / "frontend" / "analysis_result.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('matchStatus === "review required"', frontend)
        self.assertIn('matchStatus === "no suitable role"', frontend)
        self.assertIn('mode = "autoNoSuitableRole"', frontend)
        self.assertIn("No Suitable CS Role Confirmed", frontend)
        self.assertIn("Closest evidence only", frontend)
        self.assertNotIn("SELECTED_ROLE_EXAMPLE_THRESHOLD", frontend)

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
