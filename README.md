# UTS CS Career Navigator

UTS CS Career Navigator is a Final Year Project web application for Computer Science students at the University of Technology Sarawak (UTS), Malaysia. It compares a résumé with CS job requirements, gives an ATS-style readiness score, and explains what matched and what to improve. It can also suggest related roles and help create a résumé draft. This is an educational prototype, not an official university service.

## Open the live app

**[Launch UTS CS Career Navigator](https://development-of-an-ai-driven-ats-resume.onrender.com/)**

The app runs on Render Free. After a period without visitors, the first page load can take about a minute while the service starts again. [Why this happens](https://render.com/docs/free#spinning-down-on-idle).

## How to use it

1. Open the live app and select the account icon. New users choose **Sign up**, enter their details, click **Send PIN**, enter the six-digit code received by email, and sign up. Returning users choose **Sign in**.
2. Click **Check my resume**.
3. Search for a target CS job role, or leave the role blank to let the system suggest one from the résumé.
4. Click **Choose File** or drag a résumé into the upload area. The app accepts PDF, PNG, JPG, and JPEG files up to 10 MB. A PDF with selectable text usually gives the clearest analysis; scanned PDFs may not provide readable text.
5. Click **Analyze resume** and wait for the results page.
6. Review the **UTS CS Readiness Score**, job-role match, matched and missing keywords, and improvement suggestions.
7. If useful, click **Generate Optimized Resume** to preview a draft and download it as a PDF. You can also preview the complete feedback report on the results page or download the report as a PDF.

Open the account menu and choose **History** to reopen reports saved to your account, including from another device. If a report could not be saved to the database, it remains available only in the browser where you ran the analysis. After visiting the home page or fictional sample, use **Your report** in the header to return to your latest analysis. To explore the interface before uploading a résumé, use **See a sample report** on the home page. The sample uses fictional information and is clearly labelled.

## What the feedback means

The score is an educational estimate from this project's rules and text-matching methods. It is not a score from an employer's applicant tracking system, and it cannot guarantee an interview or a particular hiring outcome. The app uses explainable keyword, requirement, TF-IDF, experience, education, and document-quality checks; it does not call an external AI or LLM API.

Review any generated résumé before using it. If the app shows a **Fictional example - not for submission** notice, that example is only a reference and must not be presented as your own experience.

## Data and technology

The Flask web app and its frontend are hosted on Render. Supabase PostgreSQL stores the CS job catalogue, user accounts, and saved analysis records. Analysis records can include parts of the text extracted from an uploaded résumé. Upload only a résumé you own or have permission to use.

The project uses Python, Flask, scikit-learn, pypdf, Tesseract OCR for supported image uploads, ReportLab, HTML, JavaScript, and Tailwind CSS.

## For developers

- Branches: `main` contains the current Render testing version, `update_version` holds the updated app, `method_2` prepares Netlify and Koyeb staging, and `old_version` preserves the previous release.
- To preview locally, build the stylesheet with `npm ci` and `npm run build:css`, then start the configured Flask app with `.\\.venv\\Scripts\\python.exe backend/app.py`. Open `http://127.0.0.1:5000/`. Opening an HTML file directly cannot load account or admin data.
- `outputs/` contains local evaluation data and is ignored by Git. QA screenshots and temporary browser profiles are not needed by the app and should be removed after testing.
- [Render deployment guide](docs/deployment/DEPLOY_RENDER_FREE.md)
- [Supabase setup guide](docs/deployment/DEPLOY_SUPABASE.md)
- [Netlify + Koyeb staging guide](docs/deployment/DEPLOY_KOYEB_NETLIFY.md)
- [Environment variable example](config/env.example)
- Run the backend tests from the project root with `python -m unittest discover -s backend/tests -v`.

## License

This project is released under the [MIT License](LICENSE).
