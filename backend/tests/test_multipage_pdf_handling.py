from __future__ import annotations

import importlib
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.datastructures import FileStorage


PROJECT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_DIR / "backend"
FRONTEND_FILE = PROJECT_DIR / "frontend" / "analysis_result.html"
TWO_PAGE_RESUME = PROJECT_DIR / "Resume_template" / "real_resume" / "NGU MING HENN_Resume_FINAL.pdf"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

with patch.dict(
    os.environ,
    {
        "APP_ENV": "testing",
        "FLASK_SECRET_KEY": "multi-page-pdf-tests-only-secret-key-value-1234567890",
        "ACCURACY_TESTING_AUTO_EXPORT": "0",
    },
    clear=False,
):
    app_module = importlib.import_module("app")


class MultiPagePdfHandlingTests(unittest.TestCase):
    def test_supplied_two_page_resume_is_extracted_across_both_pages(self):
        self.assertTrue(TWO_PAGE_RESUME.exists())

        with TWO_PAGE_RESUME.open("rb") as source:
            upload = FileStorage(
                stream=source,
                filename=TWO_PAGE_RESUME.name,
                content_type="application/pdf",
            )
            resume_text, document_info = app_module.extract_resume_text(upload, upload.filename)

        self.assertEqual(document_info["page_count"], 2)
        self.assertIn("PROFESSIONAL EXPERIENCE", resume_text)
        self.assertIn("SKILLS", resume_text)
        self.assertIn("Tools & Platforms", resume_text)
        self.assertGreater(len(resume_text.split()), 400)
        self.assertEqual(
            [entry["title"] for entry in app_module.extract_employment_entries(resume_text)],
            ["Full-Stack Developer Intern", "IT Technician"],
        )
        self.assertEqual(app_module.extract_most_recent_job_title(resume_text), "IT Technician")

    def test_results_preview_renders_every_pdf_page(self):
        source = FRONTEND_FILE.read_text(encoding="utf-8")
        function_start = source.index("async function renderPdfResumePreview")
        function_end = source.index("function renderAtsQualityCriteria", function_start)
        renderer = source[function_start:function_end]

        self.assertIn('id="resumePdfRenderedPages"', source)
        self.assertRegex(
            renderer,
            re.compile(
                r"for\s*\(\s*let\s+pageNumber\s*=\s*1;\s*"
                r"pageNumber\s*<=\s*pdf\.numPages;\s*pageNumber\s*\+=\s*1\s*\)"
            ),
        )
        self.assertIn("pdf.getPage(pageNumber)", renderer)
        self.assertIn('"Page " + pageNumber + " of " + pdf.numPages', renderer)
        self.assertNotIn("pdf.getPage(1)", renderer)


if __name__ == "__main__":
    unittest.main()
