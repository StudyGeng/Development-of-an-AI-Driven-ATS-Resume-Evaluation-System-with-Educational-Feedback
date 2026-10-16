# UTS CS Career Navigator

UTS CS Career Navigator is a Final Year Project web application for Computer Science students. It compares a résumé with CS job requirements, gives an ATS-style readiness score, and explains what matched and what to improve. It can also suggest related roles and help create a résumé draft.

## Open the live app

**[Launch UTS CS Career Navigator](https://development-of-an-ai-driven-ats-resume.onrender.com/)**

The app runs on Render Free. After a period without visitors, the first page load can take about a minute while the service starts again. [Why this happens](https://render.com/docs/free#spinning-down-on-idle).

## How to use it

1. Open the live app and click **Log In / Sign Up**. New users choose **Sign Up**, enter their details, click **Send PIN**, enter the six-digit code received by email, and click **Create Account**. Returning users log in with their email and password.
2. Click **Evaluate My Resume**.
3. Search for a target CS job role, or leave the role blank to let the system suggest one from the résumé.
4. Click **Choose File** or drag a résumé into the upload area. The app accepts PDF, PNG, JPG, and JPEG files up to 10 MB. A PDF with selectable text usually gives the clearest analysis; scanned PDFs may not provide readable text.
5. Click **Start ATS + NLP Analysis** and wait for the results page.
6. Review the **UTS CS Readiness Score**, job-role match, matched and missing keywords, and improvement suggestions.
7. If useful, click **Generate Optimized Resume** to preview a draft and download it as PDF or HTML. You can also export the feedback report as PDF or HTML without generating a résumé.

Click **History** to reopen recent results saved in the current browser. To explore the interface before uploading a résumé, use **View Sample Feedback** on the home page.

## What the feedback means

The score is an educational estimate from this project's rules and text-matching methods. It is not a score from an employer's applicant tracking system, and it cannot guarantee an interview or a particular hiring outcome. The app uses explainable keyword, requirement, TF-IDF, experience, education, and document-quality checks; it does not call an external AI or LLM API.

Review any generated résumé before using it. If the app shows a **Fictional example - not for submission** notice, that example is only a reference and must not be presented as your own experience.

## Data and technology

The Flask web app and its frontend are hosted on Render. Supabase PostgreSQL stores the CS job catalogue, user accounts, and saved analysis records. Analysis records can include parts of the text extracted from an uploaded résumé. Upload only a résumé you own or have permission to use.

The project uses Python, Flask, scikit-learn, pypdf, Tesseract OCR for supported image uploads, ReportLab, HTML, JavaScript, and Tailwind CSS.

## For developers

- [Render and Supabase deployment guide](DEPLOY_RENDER_FREE.md)
- [Environment variable example](config/env.example)
- Run the backend tests from the project root with `python -m unittest discover -s backend/tests -v`.

## License

This project is released under the [MIT License](LICENSE).
