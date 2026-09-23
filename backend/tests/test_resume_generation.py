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


if __name__ == "__main__":
    unittest.main()
