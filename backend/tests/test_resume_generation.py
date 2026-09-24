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

import database  # noqa: E402


with patch.dict(
    os.environ,
    {
        "APP_ENV": "testing",
        "FLASK_SECRET_KEY": "resume-generation-tests-only-secret-key-value-1234567890",
        "ACCURACY_TESTING_AUTO_EXPORT": "0",
    },
    clear=False,
), patch.object(database, "init_mysql_database"):
    app_module = importlib.import_module("app")


SOURCE_RESUME_TEXT = """
C o n t a c t
E d u c a t i o n
+ 6 0  1 2  3 4 5 6 7 8 9
a l e x . t a n @ e x a m p l e . c o m
T e c h  S t a c k
A L E X
T A N
Computer Science (Final Year)
Web-Based AI Chatbot
Residential Information Website
M4FOOD: Food Donation Mobile Application
Developed an AI chatbot to automate user responses and simulate conversations
Integrated Google Gemini API for real-time response generation
Designed basic conversation flow and NLP logic
Technologies: Python, HTML, CSS
Role: Backend integration
Academic Projects
Frontend: .NET MAUI, HTML, CSS
Backend: Supabase, Python
Languages: Python, C++, JavaScript
AI/Tools: Prompt Engineering, AI Tool Integration
Others: Git/GitHub
Developed a mobile application to connect users with surplus food from bakeries
Designed and implemented responsive frontend UI using .NET MAUI
Integrated Google Maps API for location-based search
Technologies: .NET MAUI, Python, SQLite
Developed a web platform for users to browse houses and apartment listings
Built and managed backend database for data storage and retrieval
Ensured smooth integration between frontend interface and backend services
Technologies: HTML, CSS, JavaScript, Supabase
Bachelor of Computer Science
University of Technology Sarawak (Example Campus)
SMK Example (Example Town)
2023 - Present
2021 - 2023
Sijil Tinggi Persekolahan Malaysia (STPM)
Final Year Project: AI-Powered Land Suitability Platform
Developing an AI-powered system to evaluate land suitability for crop selection
Applies data analysis and machine learning techniques for decision-making
Integrates environmental data such as weather, soil, and location
Designing full-stack system including frontend interface and backend processing
Technologies: Python, HTML, CSS, Supabase
P o r t f o l i o
h t t p s : / / s a m p l e - p o r t f o l i o . v e r c e l . a p p /
"""


class ResumeGenerationTests(unittest.TestCase):
    def setUp(self):
        self.saved_testing = app_module.app.config.get("TESTING")
        self.saved_csrf_testing = app_module.app.config.get("ENFORCE_CSRF_IN_TESTS")
        app_module.app.config.update(TESTING=True, ENFORCE_CSRF_IN_TESTS=False)

    def tearDown(self):
        app_module.app.config.update(
            TESTING=self.saved_testing,
            ENFORCE_CSRF_IN_TESTS=self.saved_csrf_testing,
        )

    def build_draft(self, uploaded_name="Resume.pdf"):
        analysis = {
            "matching_percentage": 52,
            "uploaded_resume_name": uploaded_name,
            "target_role": "Software Developer",
            "top_role_matches": [{"role": "Software Developer", "score": 52}],
            "resume_summary": {
                "skills": [
                    "api",
                    "backend",
                    "css",
                    "database",
                    "frontend",
                    "git",
                    "html",
                    "javascript",
                    ".net",
                    "python",
                    "supabase",
                ],
                "education_markers": ["bachelor", "degree", "university"],
                "experience_markers": ["built", "designed", "developed", "project"],
            },
            "role_recommendation": {
                "recommended_role": "Software Developer",
                "matched_resume_signals": ["software", "develop", "application"],
            },
            "job_description_analysis": {"matched_keywords": [], "missing_keywords": []},
        }
        return app_module.build_generated_resume_payload(
            analysis,
            SOURCE_RESUME_TEXT,
            "Software Developer",
            "Fallback Account Name",
        )

    def test_preserves_source_candidate_contact_projects_and_education(self):
        draft = self.build_draft()

        self.assertEqual(draft["candidate"]["name"], "Alex Tan")
        self.assertEqual(draft["candidate"]["email"], "alex.tan@example.com")
        self.assertIn("https://sample-portfolio.vercel.app/", draft["candidate"]["links"])

        projects = draft["sections"]["projects"]
        self.assertEqual(len(projects), 4)
        for title in (
            "Web-Based AI Chatbot",
            "Residential Information Website",
            "M4FOOD: Food Donation Mobile Application",
            "Final Year Project: AI-Powered Land Suitability Platform",
        ):
            self.assertTrue(any(title in project for project in projects), title)
        residential = next(project for project in projects if "Residential Information Website" in project)
        self.assertIn("Developed a web platform for users to browse houses", residential)

        education = "\n".join(draft["sections"]["education"])
        self.assertIn("Bachelor of Computer Science - University of Technology Sarawak (2023 - Present)", education)
        self.assertIn("Sijil Tinggi Persekolahan Malaysia (STPM) - SMK Example (Example Town) (2021 - 2023)", education)

    def test_preserves_explicit_skills_without_inventing_rest_api(self):
        draft = self.build_draft()
        rendered_skills = {
            item
            for group in draft["sections"]["skill_groups"]
            for item in group["items"]
        }

        for expected in (
            ".NET MAUI",
            "C++",
            "GitHub",
            "Google Gemini API",
            "Google Maps API",
            "SQLite",
            "Prompt Engineering",
            "Machine Learning",
            "NLP",
        ):
            self.assertIn(expected, rendered_skills)
        self.assertNotIn("REST APIs", rendered_skills)
        self.assertNotIn(".NET", rendered_skills)
        self.assertEqual(app_module.format_resume_keyword("api"), "API Integration")

    def test_does_not_invent_achievements_links_or_candidate_identity(self):
        draft = self.build_draft("Incorrect Longer Person Name Resume.pdf")

        self.assertEqual(draft["candidate"]["name"], "Alex Tan")
        self.assertEqual(draft["sections"]["achievements"], [])
        self.assertIn("4 documented projects", draft["sections"]["professional_summary"])
        self.assertNotIn("built, designed, developed", draft["sections"]["professional_summary"].lower())

        links = app_module.get_generated_resume_contact_links(
            {
                "linkedin_url": "not-a-link",
                "github_url": "https://example.com/not-github",
                "portfolio_url": "asdasdsa",
                "contact_links": ["https://valid.example.com/work"],
            }
        )
        self.assertEqual(links, ["https://valid.example.com/work"])

    def test_achievement_detection_ignores_years_and_unmeasured_user_words(self):
        self.assertFalse(
            app_module.has_achievement_signal(
                "Tested the TVET 2023 mobile application and provided user guidance."
            )
        )
        self.assertFalse(
            app_module.has_achievement_signal(
                "Developed an application that lets users browse available services."
            )
        )
        self.assertTrue(
            app_module.has_achievement_signal(
                "Reduced event scheduling time by 30%."
            )
        )

    def test_education_is_rendered_as_degree_then_institution_and_dates(self):
        draft = self.build_draft()
        self.assertIn('<li class="education-item">', draft["html"])
        self.assertIn("<strong>Bachelor of Computer Science</strong>", draft["html"])
        self.assertIn("University of Technology Sarawak | 2023 - Present", draft["html"])

    def test_project_title_and_technology_metadata_are_not_duplicated(self):
        projects = app_module.build_project_entries(
            """
            PROJECTS
            E-Book Library Management System | HTML, CSS, JavaScript, Firebase
            Multi-Vendor Food Ordering System | HTML, CSS, JavaScript, Supabase
            """,
            [
                "E-Book Library Management System | HTML, CSS, JavaScript, Firebase.",
                "Developed search and borrowing workflows for an e-book library application.",
                "Multi-Vendor Food Ordering System | HTML, CSS, JavaScript, Supabase.",
                "Developed ordering workflows for multiple food vendors.",
            ],
        )

        self.assertEqual(len(projects), 2)
        for project in projects:
            title, separator, description = project.partition(" - ")
            self.assertTrue(title)
            if separator:
                self.assertNotIn(title, description)

    def test_selected_role_at_50_returns_clearly_labelled_sample_for_selected_role(self):
        unrelated_source = """
        AISHA BUSINESS USER
        Retail Operations Executive
        Managed store inventory and prepared monthly sales reports.
        """
        analysis = {
            "matching_percentage": 64,
            "selected_role": "Software Engineer",
            "role_recommendation": {
                "selected_role": "Software Engineer",
                "selected_database_role": "Software Developer",
                "selected_score": 50,
                "recommended_role": "IT Support Specialist",
                "top_role_matches": [
                    {"role": "IT Support Specialist", "score": 72},
                    {"role": "Computer Technician", "score": 68},
                    {"role": "Software Developer", "score": 50},
                ],
            },
        }

        draft = app_module.build_generated_resume_payload(
            analysis,
            unrelated_source,
            "IT Support Specialist",
            "Aisha Business User",
        )

        self.assertTrue(draft["is_sample_resume"])
        self.assertEqual(draft["target_role"], "Software Engineer")
        self.assertEqual(draft["candidate"]["name"], "Sample Candidate")
        self.assertEqual(draft["sample_reason_code"], "selected_role_at_or_below_50")
        self.assertEqual(draft["sample_target_role"], "Software Engineer")
        self.assertEqual(draft["sample_mapped_database_role"], "Software Developer")
        self.assertEqual(draft["sample_target_score"], 50)
        self.assertEqual(draft["sample_score_threshold"], 50)
        self.assertEqual(draft["best_top_role_score"], 72)
        self.assertEqual(
            draft["sample_metadata"],
            {
                "reason_code": "selected_role_at_or_below_50",
                "target_role": "Software Engineer",
                "mapped_database_role": "Software Developer",
                "profile_basis_role": "Software Engineer",
                "evidence_score": 50.0,
                "threshold": 50.0,
                "comparison": "at_or_below",
            },
        )
        self.assertIn("EXAMPLE ONLY - SAMPLE RESUME", draft["plain_text"])
        self.assertIn("EXAMPLE ONLY - SAMPLE RESUME", draft["html"])
        self.assertNotIn("Aisha Business User", draft["plain_text"])
        self.assertNotIn("Retail Operations Executive", draft["plain_text"])
        self.assertNotIn("monthly sales reports", draft["html"])

    def test_selected_role_above_50_keeps_truthful_user_resume_generation(self):
        analysis = {
            "matching_percentage": 64,
            "selected_role": "Software Engineer",
            "role_recommendation": {
                "selected_role": "Software Engineer",
                "selected_database_role": "Software Developer",
                "selected_score": 50.01,
                "recommended_role": "Software Developer",
                "top_role_matches": [{"role": "Software Developer", "score": 50.01}],
            },
            "resume_summary": {"skills": ["Python"]},
            "job_description_analysis": {"matched_keywords": ["Python"], "missing_keywords": []},
        }

        draft = app_module.build_generated_resume_payload(
            analysis,
            "JANE USER\nPython developer who built a scheduling application.",
            "Software Developer",
            "Jane User",
        )

        self.assertFalse(draft.get("is_sample_resume", False))
        self.assertEqual(draft["candidate"]["name"], "Jane User")
        self.assertNotIn("EXAMPLE ONLY", draft["plain_text"])

    def test_auto_detect_keeps_original_below_20_sample_rule(self):
        low_analysis = {
            "matching_percentage": 19,
            "top_role_matches": [
                {"role": "Data Analyst", "score": 19},
                {"role": "Software Developer", "score": 15},
            ],
        }
        low_draft = app_module.build_generated_resume_payload(
            low_analysis,
            "Unrelated source material",
        )

        self.assertTrue(low_draft["is_sample_resume"])
        self.assertEqual(low_draft["target_role"], "Data Analyst")
        self.assertEqual(low_draft["sample_reason_code"], "auto_detected_top_role_below_20")
        self.assertEqual(low_draft["sample_target_score"], 19)
        self.assertEqual(low_draft["sample_score_threshold"], 20)

        boundary_decision = app_module.get_resume_sample_decision(
            {"top_role_matches": [{"role": "Data Analyst", "score": 20}]}
        )
        self.assertFalse(boundary_decision["should_sample"])

    def test_explicit_selected_role_without_selected_score_is_not_guessed_from_top_role(self):
        decision = app_module.get_resume_sample_decision(
            {
                "selected_role": "Software Engineer",
                "role_recommendation": {
                    "selected_role": "Software Engineer",
                    "selected_database_role": "Software Developer",
                    "top_role_matches": [{"role": "Business Analyst", "score": 12}],
                },
            }
        )

        self.assertFalse(decision["should_sample"])
        self.assertEqual(decision["reason_code"], "selected_role_score_unavailable")

    def test_sample_pdf_contains_visible_example_only_banner(self):
        draft = app_module.build_sample_resume_payload(
            {},
            "Software Engineer",
            72,
            reason_code="selected_role_at_or_below_50",
            evidence_score=45,
            generation_threshold=50,
        )

        pdf_buffer = app_module.build_generated_resume_pdf_buffer(draft)
        extracted_text = "\n".join(
            page.extract_text() or ""
            for page in app_module.PdfReader(pdf_buffer).pages
        )

        self.assertIn("EXAMPLE ONLY - SAMPLE RESUME", extracted_text)
        self.assertIn("this is not the user's resume", extracted_text)

    def test_sample_content_is_tailored_to_data_cybersecurity_and_software_roles(self):
        data_sample = app_module.build_sample_resume_payload({}, "Data Analyst", 15)
        cyber_sample = app_module.build_sample_resume_payload({}, "Cybersecurity Analyst", 15)
        software_sample = app_module.build_sample_resume_payload({}, "Software Developer", 15)

        self.assertEqual(data_sample["sample_role_family"], "Data / AI")
        self.assertIn("Pandas", data_sample["sections"]["technical_skills"])
        self.assertIn("Power BI", data_sample["sections"]["technical_skills"])
        self.assertIn("held-out test set", " ".join(data_sample["sections"]["experience_bullets"]))
        self.assertNotIn("REST API", data_sample["sections"]["technical_skills"])

        self.assertEqual(cyber_sample["sample_role_family"], "Cybersecurity")
        self.assertIn("SIEM", cyber_sample["sections"]["technical_skills"])
        self.assertIn("OWASP", cyber_sample["sections"]["technical_skills"])
        self.assertIn("security alerts", " ".join(cyber_sample["sections"]["experience_bullets"]))
        self.assertNotIn("Pandas", cyber_sample["sections"]["technical_skills"])

        self.assertEqual(software_sample["sample_role_family"], "Software Development")
        self.assertIn("REST API", software_sample["sections"]["technical_skills"])
        self.assertIn("Issue Tracker Web App", " ".join(software_sample["sections"]["projects"]))
        self.assertNotEqual(data_sample["sections"]["projects"], software_sample["sections"]["projects"])
        self.assertNotEqual(cyber_sample["sections"]["projects"], software_sample["sections"]["projects"])

        for sample in (data_sample, cyber_sample, software_sample):
            self.assertEqual(sample["candidate"]["name"], "Sample Candidate")
            self.assertIn("EXAMPLE ONLY - SAMPLE RESUME", sample["plain_text"])
            self.assertGreaterEqual(len(sample["sections"]["experience_bullets"]), 3)
            self.assertGreaterEqual(len(sample["sections"]["projects"]), 3)
            self.assertIn("[N]", " ".join(sample["sections"]["experience_bullets"] + sample["sections"]["projects"]))
            for section_name in ("experience_bullets", "projects", "certifications", "achievements", "education"):
                for value in sample["sections"][section_name]:
                    self.assertTrue(value.startswith("Example"), (section_name, value))

    def test_every_sample_family_has_detailed_replaceable_evidence_examples(self):
        representative_roles = (
            "UI/UX Designer",
            "Cybersecurity Analyst",
            "Cloud Engineer",
            "Network Engineer",
            "Database Administrator",
            "QA Engineer",
            "IT Support Specialist",
            "Artificial Intelligence Engineer",
            "Software Developer",
            "Technology Consultant",
        )

        for role in representative_roles:
            with self.subTest(role=role):
                sample = app_module.build_sample_resume_payload({}, role, 15)
                experience = sample["sections"]["experience_bullets"]
                projects = sample["sections"]["projects"]

                self.assertGreaterEqual(len(experience), 3)
                self.assertGreaterEqual(len(projects), 3)
                self.assertTrue(all(item.startswith("Example experience bullet:") for item in experience))
                self.assertTrue(all(item.startswith("Example project:") for item in projects))
                self.assertIn("[N]", " ".join(experience + projects))
                self.assertIn("Replace every example", sample["plain_text"])

    def test_generic_selected_alias_uses_mapped_role_only_as_profile_fallback(self):
        swe_sample = app_module.build_sample_resume_payload(
            {"role_recommendation": {"selected_database_role": "Software Developer"}},
            "SWE",
            35,
        )
        ui_sample = app_module.build_sample_resume_payload(
            {"role_recommendation": {"selected_database_role": "UI/UX Developer"}},
            "UI Designer",
            35,
        )
        data_sample = app_module.build_sample_resume_payload(
            {"role_recommendation": {"selected_database_role": "Oracle Database Engineer"}},
            "Data Analyst",
            35,
        )

        self.assertEqual(swe_sample["target_role"], "SWE")
        self.assertEqual(swe_sample["sample_role_family"], "Software Development")
        self.assertEqual(swe_sample["sample_profile_basis_role"], "Software Developer")
        self.assertIn("REST API", swe_sample["sections"]["technical_skills"])

        self.assertEqual(ui_sample["target_role"], "UI Designer")
        self.assertEqual(ui_sample["sample_role_family"], "UI / UX")
        self.assertEqual(ui_sample["sample_profile_basis_role"], "UI/UX Developer")
        self.assertIn("Figma", ui_sample["sections"]["technical_skills"])

        self.assertEqual(data_sample["sample_role_family"], "Data / AI")
        self.assertEqual(data_sample["sample_profile_basis_role"], "Data Analyst")
        self.assertIn("Pandas", data_sample["sections"]["technical_skills"])

    def test_resume_generation_routes_require_a_live_authenticated_session(self):
        client = app_module.app.test_client()

        generated_response = client.post(
            "/generate-resume",
            json={"analysis": {"matching_percentage": 45}},
        )
        pdf_response = client.post(
            "/generate-resume-pdf",
            json={"generated_resume": {"target_role": "Software Engineer"}},
        )

        self.assertEqual(generated_response.status_code, 401)
        self.assertEqual(generated_response.get_json()["detail"], "Authentication required")
        self.assertEqual(pdf_response.status_code, 401)
        self.assertEqual(pdf_response.get_json()["detail"], "Authentication required")

    def test_generate_route_uses_owned_saved_analysis_instead_of_forged_browser_score(self):
        client = app_module.app.test_client()
        saved_analysis = {
            "analysis_id": 44,
            "selected_role": "Software Engineer",
            "role_recommendation": {
                "selected_role": "Software Engineer",
                "selected_database_role": "Software Developer",
                "selected_score": 45,
                "top_role_matches": [{"role": "IT Support Specialist", "score": 70}],
            },
            "resume_excerpt": "Retail operations and monthly sales reporting.",
        }
        forged_client_analysis = {
            **saved_analysis,
            "role_recommendation": {
                **saved_analysis["role_recommendation"],
                "selected_score": 90,
            },
        }

        with patch.object(
            app_module,
            "get_authenticated_session_user",
            return_value={"id": 1, "full_name": "Aisha User", "email": "aisha@example.com"},
        ), patch.object(
            app_module,
            "load_owned_analysis_snapshot",
            return_value=("owned", saved_analysis),
        ):
            response = client.post(
                "/generate-resume",
                json={
                    "analysis": forged_client_analysis,
                    "analysis_id": 44,
                    "resume_text": "Retail operations and monthly sales reporting.",
                    "target_role": "Software Engineer",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["is_sample_resume"])
        self.assertEqual(payload["sample_target_score"], 45)
        self.assertEqual(payload["target_role"], "Software Engineer")

    def test_generation_rejects_foreign_or_missing_analysis_id(self):
        client = app_module.app.test_client()
        authenticated_user = {"id": 1, "full_name": "Aisha User", "email": "aisha@example.com"}

        with patch.object(app_module, "get_authenticated_session_user", return_value=authenticated_user):
            missing_response = client.post(
                "/generate-resume-pdf",
                json={"generated_resume": {"target_role": "Software Engineer"}},
            )

        with patch.object(
            app_module,
            "get_authenticated_session_user",
            return_value=authenticated_user,
        ), patch.object(
            app_module,
            "load_owned_analysis_snapshot",
            return_value=("forbidden", None),
        ):
            forbidden_response = client.post(
                "/generate-resume",
                json={"analysis_id": 202, "analysis": {"analysis_id": 202}},
            )

        self.assertEqual(missing_response.status_code, 400)
        self.assertIn("analysis_id", missing_response.get_json()["detail"])
        self.assertEqual(forbidden_response.status_code, 403)
        self.assertIn("another user's analysis", forbidden_response.get_json()["detail"])

    def test_selected_role_uses_calibrated_score_even_when_outside_visible_top_three(self):
        selected_job = {"role": {"role_name": "Software Developer"}}
        detected_job = {"role": {"role_name": "IT Support Specialist"}}
        selected_fit = {"combined_score": 88, "matched_keywords": [], "missing_keywords": []}
        detected_fit = {"combined_score": 70, "matched_keywords": [], "missing_keywords": []}
        ranked_matches = [
            {"role": {"role_name": "IT Support Specialist"}, "confidence_score": 72, "fit_result": detected_fit},
            {"role": {"role_name": "Computer Technician"}, "confidence_score": 66, "fit_result": {}},
            {"role": {"role_name": "Systems Administrator"}, "confidence_score": 58, "fit_result": {}},
            {"role": {"role_name": "Software Developer"}, "confidence_score": 34, "fit_result": selected_fit},
        ]

        recommendation = app_module.build_role_recommendation(
            "Software Engineer",
            selected_job,
            detected_job,
            detected_job,
            selected_fit,
            detected_fit,
            ranked_matches,
        )

        self.assertEqual(recommendation["selected_score"], 34)
        self.assertEqual(len(recommendation["top_role_matches"]), 3)
        self.assertNotEqual(recommendation["selected_score"], selected_fit["combined_score"])

    def test_popular_role_aliases_use_the_matching_database_role_families(self):
        self.assertEqual(app_module.POPULAR_ROLE_ALIASES["data analyst"], "Business Data Analyst")
        self.assertEqual(app_module.POPULAR_ROLE_ALIASES["data scientist"], "Data Scientist")
        self.assertEqual(app_module.POPULAR_ROLE_ALIASES["machine learning engineer"], "Machine Learning Engineer")
        self.assertEqual(app_module.POPULAR_ROLE_ALIASES["devops engineer"], "Cloud Computing Solution Architect")
        self.assertEqual(app_module.POPULAR_ROLE_ALIASES["cloud engineer"], "Cloud Computing Solution Architect")
        self.assertEqual(app_module.get_role_family("Business Data Analyst"), "data_ai")
        self.assertEqual(app_module.get_role_family("Cloud Computing Solution Architect"), "cloud_devops")


if __name__ == "__main__":
    unittest.main()
