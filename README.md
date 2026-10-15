# UTS CS Career Navigator

UTS CS Career Navigator is a Final Year Project web application that helps Computer Science students check how well their resume matches a selected CS job role.

Users can upload a resume, choose a target role, view an ATS-style score, check missing skills or keywords, get improvement suggestions, and generate an ATS-friendly resume draft.

## Main Features

- Resume upload for text-based PDF files
- Optional PNG/JPG resume-image OCR when Tesseract is installed
- ATS-style resume score and score breakdown
- CS job-role matching using the configured database's job requirements
- Top 3 recommended role matches
- Matched keywords and missing keywords
- Resume quality checks for skills, education, experience, and formatting
- Improvement suggestions and priority actions
- ATS resume draft generation
- PDF export for generated resume and feedback report
- User registration, login, and password reset
- Email PIN verification
- User analysis history
- Admin dashboard for user activity, evaluation records, data cleaning, and job-data insights

## Technologies Used

- Python
- Flask
- MySQL 8.x for the local setup below, or Supabase PostgreSQL for testing and hosting
- scikit-learn
- pypdf
- Pillow
- pytesseract
- ReportLab
- HTML
- Tailwind CSS (locally compiled production stylesheet)
- JavaScript

## How Job Matching Works

The current matcher is a transparent hybrid NLP and rule-based system. It does not call an external AI API and it is not a self-trained classifier or LLM.

1. The configured database supplies the job roles, MASCO descriptions, requirement types, importance weights, and job requirements.
2. The resume and requirements are normalized using skill aliases, word-form rules, and a curated set of related terms.
3. TF-IDF measures lexical text similarity between the resume and each requirement/job description.
4. For automatic role ranking, base requirement fit contributes 55%, role-specific evidence contributes 10%, and dated employment-title relevance contributes 35%. When no dated employment title can be parsed, the available requirement and role-evidence components are renormalized instead of treating the missing title as a mismatch. The separate ATS-readiness score also considers experience, education, and document quality.

Repeated keywords do not earn repeated requirement points, and TF-IDF uses binary term frequency so keyword stuffing cannot raise the lexical score simply by copying the same word many times. Clearly negated claims such as `no experience with Python` are not accepted as positive skill evidence. Dated employment titles are evaluated as career evidence: the most recent receives full title credit and each older entry receives 85% of the credit available to the entry before it. Only an exact current-title match can activate the existing exact-title confidence floor.

Automatic role ranking also has a conservative human-review gate. Any closest-role score below 20 is reported as `NO SUITABLE ROLE`. When a prominent resume headline is explicitly from a non-IT field, the review threshold is 35; this stops resumes such as pharmacist or football-coach profiles from being presented as confirmed CS matches while preserving genuine titleless junior-IT resumes around the same score range. For an unrelated resume with a selected job, the result is `REVIEW REQUIRED / NOT MATCH` and the resume builder offers a clearly labelled fictional example for that selected job. With no selected job, it offers a deterministic fallback example from the supported CS sample families and clearly states that the fallback is not a job recommendation.

Employment date ranges are currently advisory metadata. The application can report the total non-overlapping months it recognized, but it does not filter a candidate by duration because the job database has no structured `minimum_experience_months` field. Skill evidence is also not yet awarded with a universal “2 points in experience, 1 point in skills” rule; adding that rule safely requires reliable section-level parsing and evaluation against human-labelled resumes.

This design is explainable, but TF-IDF mainly recognizes shared wording. The curated related-term rules help with known alternatives, while unseen paraphrases, negated claims, and keyword stuffing can still be scored incorrectly. Do not describe the current implementation as an LLM or a trained predictive model.

Training a model from scratch is not required. A future semantic upgrade can use a local pretrained sentence-embedding model alongside exact technology matching and the existing weighted requirements. Such a change should be evaluated on separate human-labelled development and unseen test sets before its score weights are adopted.

## Project Structure

```text
.
|-- backend/
|   |-- admin_helpers.py
|   |-- analysis_helpers.py
|   |-- app.py
|   |-- app_config.py
|   |-- core_utils.py
|   |-- database.py
|   |-- data_cleaning.py
|   |-- export_helpers.py
|   |-- job_data_insights.py
|   |-- job_repository.py
|   |-- job_catalog_seed.json
|   |-- manage.py
|   |-- models.py
|   |-- requirements.txt
|   |-- seed_job_catalog.py
|   |-- security.py
|   `-- tests/
|-- frontend/
|   |-- home_page.html
|   |-- analysis_result.html
|   |-- job_data_insights.html
|   |-- app_common.js
|   `-- tailwind.input.css
|-- config/
|   `-- env.example
|-- package-lock.json
|-- package.json
|-- tailwind.config.js
|-- README.md
`-- LICENSE
```

## Requirements

Install these before running the project:

- Python 3.10 or newer
- MySQL 8.x for the local setup below, or a Supabase PostgreSQL project
- Git
- Node.js 18 or newer and npm (for the Tailwind CSS build)
- Tesseract OCR, optional, only needed for PNG/JPG resume-image OCR

For the existing Supabase PostgreSQL database, use [DEPLOY_RENDER_FREE.md](DEPLOY_RENDER_FREE.md) for a free Render web service. The [Railway guide](DEPLOY_SUPABASE.md) is also available.

## Setup

### 1. Clone the project

```bash
git clone https://github.com/StudyGeng/Development-of-an-AI-Driven-ATS-Resume-Evaluation-System-with-Educational-Feedback.git
cd Development-of-an-AI-Driven-ATS-Resume-Evaluation-System-with-Educational-Feedback
```

### 2. Build the production stylesheet

From the project root:

```bash
npm ci
npm run build:css
```

For frontend development, `npm run watch:css` rebuilds the stylesheet when HTML or JavaScript changes.

### 3. Create a virtual environment

Windows:

```bash
cd backend
python -m venv ../.venv
../.venv/Scripts/activate
pip install -r requirements.txt
```

macOS/Linux:

```bash
cd backend
python3 -m venv ../.venv
source ../.venv/bin/activate
pip install -r requirements.txt
```

### 4. Configure the environment file

Copy the example environment file:

```bash
copy ..\config\env.example ..\config\env
```

For macOS/Linux:

```bash
cp ../config/env.example ../config/env
```

Edit `config/env` and add your own settings:

```text
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3307
MYSQL_USER=root
MYSQL_PASSWORD=your_mysql_password
MYSQL_DATABASE=career_navigator
MYSQL_POOL_SIZE=5
PORT=5000
APP_ENV=development
FLASK_DEBUG=0
FLASK_SECRET_KEY=replace_me
SESSION_LIFETIME_DAYS=7
ADMIN_EMAILS=replace_with_your_real_admin_email
```

Use `MYSQL_PORT=3306` if your MySQL server uses the default MySQL port. `MYSQL_POOL_SIZE` controls the connections reserved by each application worker and must be between 1 and 32; keep `worker count x MYSQL_POOL_SIZE` below the cloud database connection limit.

`replace_me` is an intentionally invalid placeholder. Generate a unique secret with Python:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

For local development, paste the generated value after `FLASK_SECRET_KEY=` in the ignored `config/env` file. You can instead omit the key only when `APP_ENV=development`; Flask then creates a temporary key and existing login sessions are lost whenever the process restarts.

For a hosted deployment, set `APP_ENV=production` and save the generated secret as `FLASK_SECRET_KEY` in the hosting provider's environment-variable or secret settings. Never put the real production key in `config/env`, source code, a build command, screenshots, logs, or GitHub. Keep the same secret across application restarts and instances; changing it signs every user out.

Successful logins use an expiring persistent cookie, so closing and reopening the browser does not immediately sign the user out. `SESSION_LIFETIME_DAYS` defaults to 7 and accepts values from 1 to 30. The frontend validates the signed cookie against the live user record whenever a page opens; a deleted account or expired/invalid cookie is therefore removed from the browser UI instead of leaving a misleading remembered login.

### 5. Create or upgrade the MySQL database

For local first-time setup, start MySQL and run this explicit migration command from the `backend` folder:

```bash
python manage.py migrate --create-database
```

The opt-in flag creates `MYSQL_DATABASE` before applying its tables. For a managed cloud MySQL service, create/select the database through the provider and omit the flag so the migration does not require `CREATE DATABASE` permission:

```bash
python manage.py migrate
```

Run the appropriate command once before the first application start and again after pulling a version that contains schema changes. Importing or starting the Flask application does not create or alter tables, so multiple production workers cannot race to perform schema setup.

The migration creates or upgrades the main tables; the local opt-in form also creates the database. However, the job matching feature also needs data inside these tables:

- `job_roles`
- `job_requirements`

Load the bundled sanitized catalogue after migration:

```bash
python seed_job_catalog.py seed
python seed_job_catalog.py verify --database
```

The tracked seed contains 96 roles and 2,847 requirements. It contains only role names, MASCO descriptions, requirement types/text, and importance weights—never user accounts, resumes, PINs, analysis history, or download history. Seeding an identical catalogue is a safe no-op; a different non-empty catalogue is not overwritten unless you explicitly review it and use `--replace`.

### 6. Run the application

From the `backend` folder:

```bash
python app.py
```

Open the app:

```text
http://127.0.0.1:5000
```

The Flask backend serves the frontend pages, so use the backend URL instead of opening the HTML files directly.

## Accuracy Evaluation Rules

The application must not be used to judge its own evaluation quality. Accuracy testing is a separate, local-only workflow using the live workbook at `outputs/accuracy_evaluation/ats_resume_fyp_testing.xlsx`.

The `Accuracy Testing` sheet supports both a chosen target and a blank-role auto-detection test. Its visible columns A:J are `Test ID`, `Test Mode`, `Selected Role`, `Expected Role`, `Evidence Basis`, `Expected Fit`, `ATS Score`, `Recommended Role`, `System Fit`, and `Check`.

| Columns | Workbook workflow |
| --- | --- |
| A:F | Prepared test information and independent ground truth. Set these before viewing the result. |
| G:I | Filled automatically with ATS score, recommended role, and selected-role fit. |
| J | Shows `NEEDS HUMAN ANSWER`, `READY TO RUN`, `PASS`, or `REVIEW` using the rule for the row's test mode. |
| K | Hidden technical `Source File` used with test mode and selected role to map an upload to the correct row. |

The two test modes answer different questions:

1. `Selected role`: choose the exact role shown in column C before uploading. Column J checks whether the system's broad role-fit answer agrees with the prepared `MATCH` or `NOT MATCH` value in column F.
2. `Auto-detect`: leave the role field empty before uploading. Column J checks whether the top recommended database role exactly matches the independent expected role in column D. A related role from the same family is not counted as an exact hit.

The workbook contains the two original selected-role cases, seven professional auto-detect cases across several CS roles, and review drafts generated from unique `UNREVIEWED` history scenarios. A generated draft copies only the source filename, test mode, and selected role when applicable. It deliberately leaves Expected Role, Evidence Basis, and Expected Fit for independent human review. Complete the yellow cells before rerunning the exact PDF. The `Role List` tab contains all current database roles for the workbook dropdowns. Do not type results into G:I manually. Close Excel while running analyses so the backend can save each result.

`Accuracy Testing` always shows the latest result for each prepared case. Every successful run is also appended to `Test Run History`; repeating a case therefore preserves the earlier scores, recommendations, top-three roles, keyword evidence, and recommendation reason instead of replacing the historical record. A resume that is not registered as a prepared test case is still stored there, but its ground-truth columns stay blank and its result is `UNREVIEWED`, so it is not counted in the accuracy summary until a human prepares and reviews the expected answer. Use that append-only sheet to compare system versions and investigate changes. Do not edit or delete historical rows while a test series is in progress.

The summary reports selected-role accuracy and auto-detect exact-role accuracy separately. These are pilot test-set results, not proof of accuracy for all users or all roles. Every counted row needs independent ground truth prepared before the system result is viewed.

The `outputs/accuracy_evaluation` folder retains only the live accuracy workbook and its eight named ATS test PDFs; obsolete workbook layouts are not used for current tests. Other evaluation artifacts are kept in their own purpose-specific subfolders under `outputs`.

Automatic workbook export defaults to off, requires the explicit local setting `ACCURACY_TESTING_AUTO_EXPORT=1`, and is always disabled when `APP_ENV=production`. A hosted deployment therefore does not write evaluation data to a local Excel file.

The number displayed from a small or unfinished sample is a test-set result, not proof of accuracy for all resumes. State the number of valid reviewed cases beside every percentage and do not publish a final accuracy claim until the planned cases and human reviews are complete.

## How To Use

### Normal User

1. Open `http://127.0.0.1:5000`.
2. Register an account or log in.
3. Choose a target CS job role.
4. Upload a resume in PDF, PNG, JPG, or JPEG format.
5. View the ATS score, matched keywords, missing keywords, and improvement suggestions.
6. Check the top 3 recommended role matches.
7. Generate an ATS resume draft if needed.
8. Export the generated resume or feedback report as PDF.

### Admin User

1. Add the real administrator email in the private `config/env` file (or in the hosting provider's secret settings).

```text
ADMIN_EMAILS=replace_with_your_real_admin_email
```

There is no built-in administrator email. Replace the placeholder with the real address in your private environment settings. `ADMIN_EMAILS` accepts a comma-separated list when more than one administrator is needed. Never commit real administrator addresses as source-code defaults.

2. Register or log in using that configured email. The server still reloads the user's current `is_admin` value from the database for every protected administrator request, so a session cookie alone cannot grant administrator access.
3. Open the admin dashboard:

```text
http://127.0.0.1:5000/job_data_insights.html
```

The admin dashboard includes user activity, resume evaluation records, job-data cleaning tools, and EMASCO/MASCO job-data insights.

## Optional OCR Setup

Text-based PDF resumes work without OCR. For PNG, JPG, or JPEG resume images, install Tesseract OCR. Scanned or image-only PDF files do not currently receive OCR fallback; export them as a text-based PDF or convert the pages to a supported image format first.

Windows example:

```text
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

Add this value to `config/env`, then restart the backend.

## Email PIN Setup

For email verification, configure a working email provider. A production deployment refuses to start unless `ADMIN_EMAILS` and one complete email provider are configured. It never returns a PIN in an API response or prints one in public logs.

For hosted deployment on a free platform that blocks SMTP, use the Resend HTTP API:

```text
APP_ENV=production
RESEND_API_KEY=your_resend_api_key
RESEND_FROM=UTS CS Career Navigator <no-reply@your-verified-domain.example>
EMAIL_PIN_DEV_MODE=0
```

The Resend HTTP API sends email through HTTPS, so it does not need SMTP port `587`.

Resend requires both `RESEND_API_KEY` and `RESEND_FROM`. Do not configure only one of them.

For local SMTP testing, Gmail can also be used.

Gmail example:

```text
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=yourgmail@gmail.com
SMTP_PASSWORD=your_gmail_app_password
SMTP_FROM=yourgmail@gmail.com
SMTP_USE_TLS=1
EMAIL_PIN_DEV_MODE=0
```

Gmail requires 2-Step Verification and a Gmail App Password.

SMTP requires both `SMTP_HOST` and `SMTP_FROM`. If the SMTP provider requires authentication, `SMTP_USERNAME` and `SMTP_PASSWORD` must also be provided together; setting only one causes startup to fail. Production also requires `SMTP_USE_TLS=1` so PIN messages are not sent over an unencrypted SMTP connection.

For local development without an email provider, opt in explicitly:

```text
APP_ENV=development
EMAIL_PIN_DEV_MODE=1
```

Only this explicit development configuration may expose `dev_pin` in the local API response. `EMAIL_PIN_DEV_MODE=1` is forbidden in production and must never be added to a hosting provider's environment settings.

PIN requests are rate-limited per email address and source IP. Wrong verification attempts are limited per PIN, while all verification attempts are also limited per source IP. The defaults can be adjusted using:

```text
EMAIL_PIN_REQUEST_LIMIT_PER_EMAIL=3
EMAIL_PIN_REQUEST_LIMIT_PER_IP=10
EMAIL_PIN_REQUEST_WINDOW_MINUTES=15
EMAIL_PIN_ATTEMPT_LIMIT_PER_IP=20
EMAIL_PIN_RATE_WINDOW_MINUTES=15
EMAIL_PIN_MAX_ATTEMPTS=5
EMAIL_PIN_TTL_MINUTES=10
```

Keep conservative values in production. Raising these limits makes automated PIN guessing and email abuse easier.

The source-IP limits use the direct client address by default:

```text
TRUSTED_PROXY_COUNT=0
```

Leave this at `0` unless the application is deployed behind a trusted reverse proxy. On hosted infrastructure, set it only to the exact number of trusted proxy hops in front of Flask so the application can identify the real client IP safely. A wrong value can make the IP limits ineffective or allow a client-supplied forwarded address to be trusted.

## Browser and Upload Security

The website is designed to serve the frontend and API from the same origin. Cross-origin browser requests are disabled by default. If you deliberately host a separate frontend, set an exact HTTPS origin—never `*`—in the hosting environment:

```text
CORS_ALLOWED_ORIGINS=https://frontend.your-domain.example
```

Every state-changing browser request requires a same-origin check and a per-session CSRF token. The supplied frontend obtains and sends this token automatically. An external integration must first call `GET /auth/csrf-token` with its session cookie, then send the returned value in the `X-CSRF-Token` header.

The default resume upload limit is 10 MB. Flask also limits multipart form memory and the number of form parts. Login and resume-analysis requests are rate-limited per source IP; login is also limited per email address:

```text
MAX_RESUME_UPLOAD_BYTES=10485760
MAX_FORM_MEMORY_BYTES=500000
MAX_FORM_PARTS=32
LOGIN_REQUEST_LIMIT_PER_IP=10
LOGIN_REQUEST_LIMIT_PER_EMAIL=10
LOGIN_RATE_WINDOW_MINUTES=15
ANALYSIS_REQUEST_LIMIT_PER_IP=20
ANALYSIS_RATE_WINDOW_MINUTES=15
```

All responses receive a Content Security Policy, clickjacking protection, MIME-sniffing protection, referrer policy, permissions policy, and cross-origin isolation headers. Production HTTPS responses also receive HSTS. Ensure `TRUSTED_PROXY_COUNT` is correct when HTTPS is terminated by a reverse proxy, otherwise Flask cannot safely recognize the original HTTPS request.

## Important Database Note

For Supabase PostgreSQL hosting, follow [DEPLOY_RENDER_FREE.md](DEPLOY_RENDER_FREE.md) or the [Railway guide](DEPLOY_SUPABASE.md). The MySQL settings below apply when `DATABASE_URL` is absent.

When running locally, this setting means the app connects to MySQL on your own computer:

```text
MYSQL_HOST=127.0.0.1
```

After hosting the Flask app online, `127.0.0.1` means the hosting server, not your laptop. If the hosted app needs to work when your laptop is off, use a hosted database and set its connection values in the hosting platform.

The application loads the public job catalogue with two queries, groups requirements by role in memory, and reuses a process-local snapshot for later analyses. A successful administrator job-data cleaning operation invalidates that snapshot immediately. The documented one-worker Gunicorn command keeps this behavior consistent; if you later add multiple workers, restart all workers after catalogue changes or add a shared cache-version signal so every worker refreshes. Seed imports or direct SQL changes made while the app is running also require an app restart. Each worker owns its own database connection pool.

For a hosted Flask deployment, run the commands from the project root so that the CSS and Python application are both built:

```text
Build command: npm ci && npm run build:css && pip install -r backend/requirements.txt
Pre-deploy/release commands: python backend/manage.py migrate && python backend/seed_job_catalog.py seed
Start command: gunicorn --chdir backend app:app --bind 0.0.0.0:$PORT
Root directory: leave blank (project root)
```

Run migration and catalogue seeding as separate one-off, pre-deploy, or release steps before starting new web workers. Do not append them to the Gunicorn start command, because every worker start or restart should only launch the website. You can then run `python backend/seed_job_catalog.py verify --database` to confirm the hosted catalogue has all 96 roles and 2,847 requirements.

Configure these values in the hosting provider before starting the application:

```text
APP_ENV=production
FLASK_SECRET_KEY=replace_me
SESSION_LIFETIME_DAYS=7
ADMIN_EMAILS=replace_with_your_real_admin_email
EMAIL_PIN_DEV_MODE=0
TRUSTED_PROXY_COUNT=0
MYSQL_POOL_SIZE=5
```

The `replace_me` value above is only a safe placeholder and will be rejected. Generate the real value locally:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Paste the output directly into the provider's `FLASK_SECRET_KEY` secret field. Do not add it to any tracked file. Production startup fails when the key is missing, shorter than 32 characters, or still a known placeholder. `APP_ENV` defaults to production when it is absent, so a hosting configuration cannot silently fall back to an insecure development key.

Administrator access is also checked against the current database user record on protected requests. Removing a user's administrator status in the database therefore takes effect without relying only on the value stored in their signed session cookie.

Production startup also validates the email settings: configure both `RESEND_API_KEY` and `RESEND_FROM`, or configure `SMTP_HOST` and `SMTP_FROM` with `SMTP_USERNAME` and `SMTP_PASSWORD` either both present or both omitted. Production rejects `EMAIL_PIN_DEV_MODE=1`.

If the cloud MySQL provider gives a CA certificate, set:

```text
MYSQL_SSL_CA=/path/to/ca.pem
```

## Main API Endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/` | Main dashboard |
| GET | `/health` | Health check |
| POST | `/analyze-file` | Analyze a resume for the signed-in session user |
| GET | `/job-roles` | List available job roles |
| GET | `/job-requirements` | Get requirements for a selected role |
| POST | `/generate-resume` | Generate ATS resume draft |
| POST | `/generate-resume-pdf` | Export generated resume as PDF |
| POST | `/feedback-report-pdf` | Export feedback report as PDF |
| POST | `/users/register` | Register user |
| POST | `/users/login` | Log in user |
| POST | `/users/logout` | Log out user |
| GET | `/auth/session` | Validate and restore the current signed browser session |
| GET | `/analyses` | Load the signed-in user's analysis history; another email requires live admin access |
| POST | `/downloads` | Record a download only for an analysis owned by the signed-in user |
| GET | `/job-data-insights` | Admin-only job-data insights |

## Automated Tests

From the project root, run:

```bash
python -m unittest discover -s backend/tests -v
```

## License

This project uses the MIT License. See `LICENSE` for details.
