# UTS CS Career Navigator

UTS CS Career Navigator is a Final Year Project web application that helps Computer Science students check how well their resume matches a selected CS job role.

Users can upload a resume, choose a target role, view an ATS-style score, check missing skills or keywords, get improvement suggestions, and generate an ATS-friendly resume draft.

## Main Features

- Resume upload for PDF, PNG, and JPG files
- ATS-style resume score and score breakdown
- CS job-role matching using MySQL job requirements
- Top 3 recommended role matches
- Matched keywords and missing keywords
- Resume quality checks for skills, education, experience, and formatting
- Improvement suggestions and priority actions
- ATS resume draft generation
- PDF export for generated resume and feedback report
- User registration, login, password reset, and profile update
- Email PIN verification
- User analysis history
- Admin dashboard for user activity, evaluation records, data cleaning, and job-data insights

## Technologies Used

- Python
- Flask
- MySQL 8.x
- scikit-learn
- pypdf
- Pillow
- pytesseract
- ReportLab
- HTML
- Tailwind CSS CDN
- JavaScript

## Project Structure

```text
.
|-- backend/
|   |-- app.py
|   |-- database.py
|   |-- data_cleaning.py
|   |-- job_data_insights.py
|   |-- requirements.txt
|   `-- schema.sql
|-- frontend/
|   |-- home_page.html
|   |-- analysis_result.html
|   |-- job_data_insights.html
|   `-- app_common.js
|-- config/
|   `-- env.example
|-- README.md
`-- LICENSE
```

## Requirements

Install these before running the project:

- Python 3.10 or newer
- MySQL 8.x
- Git
- Tesseract OCR, optional, only needed for image/scanned resume OCR

## Setup

### 1. Clone the project

```bash
git clone https://github.com/YOUR_USERNAME/uts-cs-career-navigator.git
cd uts-cs-career-navigator
```

### 2. Create a virtual environment

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

### 3. Configure the environment file

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
PORT=5000
FLASK_DEBUG=0
FLASK_SECRET_KEY=replace_with_a_random_secret
ADMIN_EMAILS=admin@uts.local
```

Use `MYSQL_PORT=3306` if your MySQL server uses the default MySQL port.

### 4. Create the MySQL database

Start MySQL, then run:

```bash
mysql -u root -p < schema.sql
```

The backend can also create the main tables automatically when it starts. However, the job matching feature needs data inside these tables:

- `job_roles`
- `job_requirements`

### 5. Run the application

From the `backend` folder:

```bash
python app.py
```

Open the app:

```text
http://127.0.0.1:5000
```

The Flask backend serves the frontend pages, so use the backend URL instead of opening the HTML files directly.

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

1. Add the admin email in `config/env`.

```text
ADMIN_EMAILS=admin@uts.local
```

2. Log in using that admin email.
3. Open the admin dashboard:

```text
http://127.0.0.1:5000/job_data_insights.html
```

The admin dashboard includes user activity, resume evaluation records, job-data cleaning tools, and EMASCO/MASCO job-data insights.

## Optional OCR Setup

Text-based PDF resumes work without OCR. For PNG, JPG, JPEG, or scanned resumes, install Tesseract OCR.

Windows example:

```text
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

Add this value to `config/env`, then restart the backend.

## Email PIN Setup

For email verification, configure SMTP settings in `config/env`.

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

For local testing, you can leave `SMTP_HOST` blank. The PIN will be printed in the backend terminal.

## Important Database Note

When running locally, this setting means the app connects to MySQL on your own computer:

```text
MYSQL_HOST=127.0.0.1
```

After hosting the Flask app online, `127.0.0.1` means the hosting server, not your laptop. If the hosted app needs to work when your laptop is off, use a cloud MySQL database and set the cloud database connection values in the hosting platform.

For a hosted Flask deployment, use:

```text
Build command: pip install -r requirements.txt
Start command: gunicorn app:app
Root directory: backend
```

If the cloud MySQL provider gives a CA certificate, set:

```text
MYSQL_SSL_CA=/path/to/ca.pem
```

## Main API Endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/` | Main dashboard |
| GET | `/health` | Health check |
| POST | `/analyze-file` | Upload and analyze resume |
| GET | `/job-roles` | List available job roles |
| GET | `/job-requirements` | Get requirements for a selected role |
| POST | `/generate-resume` | Generate ATS resume draft |
| POST | `/generate-resume-pdf` | Export generated resume as PDF |
| POST | `/feedback-report-pdf` | Export feedback report as PDF |
| POST | `/users/register` | Register user |
| POST | `/users/login` | Log in user |
| POST | `/users/logout` | Log out user |
| GET | `/job-data-insights` | Admin-only job-data insights |

## License

This project uses the MIT License. See `LICENSE` for details.
