# Deploy Flask with Supabase PostgreSQL

This guide starts a **fresh testing database**. The website is one Flask service on Railway; Flask serves the HTML/CSS/JavaScript and API through one HTTPS address. Supabase hosts PostgreSQL. Netlify is not needed for this arrangement.

## 1. Copy the Supabase database connection string

First, reset your database password in the Supabase dashboard under **Database → Settings** (or **Database → Configuration** if that is the label shown). The original password was pasted into a chat, so use a new one. Keep the new password only in your local ignored `config/env` file and your host's private variables; do not send it in chat. [Supabase password reset guide](https://supabase.com/docs/guides/troubleshooting/how-do-i-reset-my-supabase-database-password-oTs5sB).

In your Supabase project, click **Connect → Connection string → URI → Shared pooler** and use the session mode string on port `5432`. Some dashboard versions label this choice **Session pooler**. Copy the entire URI shown there. It is the PostgreSQL connection string, **not** the `https://…supabase.co` project URL shown on the project home page. Use the host and username Supabase gives you; do not construct them from your project reference. Session mode works over IPv4. [Supabase connection guide](https://supabase.com/docs/guides/database/connecting-to-postgres).

Replace `[YOUR-PASSWORD]` in the copied URI with your Supabase **database password**. If that password contains URL reserved characters, percent-encode the password portion before inserting it. Add `sslmode=require` as a URI query parameter (use `?sslmode=require` when the URI has no query yet, or `&sslmode=require` when it has one). Supabase documents TLS for database connections in its [connection guide](https://supabase.com/docs/guides/database/connecting-to-postgres#ssl).

Keep the completed URI private. Do not paste it into GitHub, the frontend, Netlify, a screenshot, or a chat. The backend uses `DATABASE_URL` directly, so no Supabase anon/publishable key is required. User and resume tables live in a private database schema, outside the exposed Data API schema.

### Connect from your Windows development copy first

Add the completed URI as `DATABASE_URL=...` in the ignored `config/env` file (copy `config/env.example` if that file does not exist). Existing `MYSQL_*` entries can remain in that file; `DATABASE_URL` selects PostgreSQL. For local testing, ensure the file also has `APP_ENV=development`. If you do not have an email provider configured locally, set `EMAIL_PIN_DEV_MODE=1` so you can test registration on your own computer. Keep this development setting off on Railway. Then run these commands from the project root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe backend\manage.py migrate
.\.venv\Scripts\python.exe backend\seed_job_catalog.py seed
.\.venv\Scripts\python.exe backend\seed_job_catalog.py verify --database
```

Start Flask locally with `.\.venv\Scripts\python.exe backend\app.py`, then open `http://127.0.0.1:5000/` and confirm the job-role list loads. If you use a different virtual environment, replace the Python path above with its interpreter.

The migration creates seven app tables in the private `ats_private` schema. The seed loads the public role catalogue into the fresh database. To check the result in **Supabase → SQL Editor**, run `select count(*) from ats_private.job_roles;` (expect `96`) and `select count(*) from ats_private.job_requirements;` (expect `2847`). The default `public` schema in Table Editor can still appear empty; choose `ats_private` or use the SQL Editor.

## 2. Create the Railway web service

Commit and push the deployment changes to the GitHub repository you will connect to Railway, or deploy this working directory with the Railway CLI. Then create a Railway project and add a service from that repository. Leave its **root directory** at the repository root, where `Dockerfile` is located. Railway detects the Dockerfile and builds the Tailwind stylesheet, Python dependencies, and Tesseract image OCR. [Railway Flask guide](https://docs.railway.com/guides/flask), [Dockerfile guide](https://docs.railway.com/builds/dockerfiles).

In the service **Variables** tab, set the following values. Put each real secret in Railway's variable field, never in a tracked file.

| Name | Value |
| --- | --- |
| `DATABASE_URL` | Your completed Supabase **Session pooler** PostgreSQL URI, including `sslmode=require` |
| `APP_ENV` | `production` |
| `FLASK_SECRET_KEY` | A stable, random secret of at least 32 characters |
| `ADMIN_EMAILS` | Your actual admin email address; comma separate multiple addresses |
| `TRUSTED_PROXY_COUNT` | `1` for the standard Railway edge proxy |
| `EMAIL_PIN_DEV_MODE` | `0` |
| `RESEND_API_KEY` and `RESEND_FROM` | A working Resend HTTP email configuration; use a sender/domain allowed to email your testers |

Generate `FLASK_SECRET_KEY` locally with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and paste the output directly into Railway's variable field. Keep this value the same on later deployments so login sessions remain valid. On Railway Free, Trial, or Hobby, use the Resend **HTTPS API** settings above; outbound SMTP is disabled on those plans. SMTP is available only on Pro and above. Registration and password reset require a working PIN email provider; production will refuse to start without one. [Railway service variables](https://docs.railway.com/variables), [Railway email networking](https://docs.railway.com/networking/outbound-networking).

Railway terminates HTTPS at its edge and passes forwarded headers to the container. The `TRUSTED_PROXY_COUNT=1` setting lets Flask recognize the HTTPS origin for its browser security checks. Review the proxy count if you later add another proxy or CDN. [Railway edge networking](https://docs.railway.com/networking/edge-networking).

## 3. Migrate and seed before the first start

In the Railway service **Settings → Pre-deploy Command**, enter:

```sh
sh -c "python backend/manage.py migrate && python backend/seed_job_catalog.py seed && python backend/seed_job_catalog.py verify --database"
```

This creates the PostgreSQL tables in Supabase, loads the bundled public job catalogue, and verifies it. Repeating the seed is a no-op while the catalogue is unchanged. If you later edit the catalogue in the app, the seed deliberately stops instead of overwriting those edits; remove the seed step from the pre-deploy command once you choose to manage that data manually. Do not use the MySQL-only `--create-database` option. Railway runs pre-deploy commands after the image build and before opening the new web deployment; a failed command blocks that deployment. [Railway pre-deploy commands](https://docs.railway.com/deployments/pre-deploy-command).

## 4. Publish and test

Set the service **Healthcheck Path** to `/health`. In **Settings → Networking**, choose **Generate Domain**. Deploy the service, then open the generated `https://…up.railway.app` address. The Dockerfile's Gunicorn command listens on Railway's `PORT`. [Railway healthchecks](https://docs.railway.com/deployments/healthchecks).

Check the deployment logs for a successful migration and catalogue verification. Then use the live website to:

1. Open `/health` and the home page.
2. Request a registration PIN, receive it by email, register, and sign in.
3. Load the role list; the database has 96 role records and 2,847 requirements. The website shows 95 distinct role names because two seeded sales-role names normalize to the same label.
4. Upload a text-based PDF resume and open its analysis and generated PDF report.
5. Sign out, sign back in, and confirm the analysis remains in history.

A healthy `/health` response only proves the web process is serving requests; the role, registration, upload, and history checks verify that the database and email provider also work. Scanned or image-only PDFs do not currently have OCR fallback. The container includes Tesseract for PNG/JPG/JPEG resume OCR.

The hosted app does not write the local accuracy testing workbook because `ACCURACY_TESTING_AUTO_EXPORT` is disabled in production. Plan a separate, consented record of student testing results. Do not collect real resumes until you have explained how they are stored and deleted.

## If deployment fails

- **Pre-deploy cannot connect:** Check the entire Session pooler URI, password encoding, `sslmode=require`, and that `DATABASE_URL` is set on this service. The project API URL and the transaction pooler port `6543` are different endpoints.
- **Web startup fails:** Check `FLASK_SECRET_KEY`, `ADMIN_EMAILS`, and the complete email provider configuration in the Railway logs.
- **Registration returns a CSRF error:** Confirm the browser uses the Railway HTTPS domain and `TRUSTED_PROXY_COUNT=1` for the standard Railway edge proxy.
- **Role list is empty:** Review pre-deploy logs and rerun the migration/seed after fixing the database connection.

The actual Supabase connection and Railway deployment need your private credentials and account access. These files prepare the app; they do not connect to your live project by themselves.
