# Host the Flask app on Render Free with Supabase

This option runs the existing Flask website and API in one Render web service. Supabase remains the PostgreSQL database. It is suitable for a small FYP testing session; Render Free has limited CPU and memory and sleeps when idle. [Render Free limits](https://render.com/docs/free), [compute plans](https://render.com/docs/compute-plans).

## 1. Put the current code on GitHub

The project already has a root `Dockerfile` that builds the frontend CSS, installs Python and Tesseract, and starts Gunicorn. Render can build it directly from a GitHub repository. **Commit and push the current code before creating the service**; Render cannot deploy changes that exist only on your laptop. Keep `config/env` and all passwords out of GitHub. [Render Docker deployment](https://render.com/docs/docker), [web service sources](https://render.com/docs/web-services).

The Supabase testing database has already been migrated and seeded locally. The role list and analysis history stay in Supabase when the Render process sleeps or restarts. The completed database URI is private; use the **Shared/Session pooler** connection string on port `5432` with `sslmode=require`. [Supabase connection methods](https://supabase.com/docs/guides/database/connecting-to-postgres).

## 2. Create a free web service

In [Render Dashboard](https://dashboard.render.com/), choose **New → Web Service → Git Provider**, connect GitHub, and select the repository containing this `Dockerfile`. Set:

| Setting | Value |
| --- | --- |
| Language/Runtime | `Docker` |
| Root directory | Repository root (leave blank unless Render asks) |
| Region | `Singapore` |
| Instance type/Compute plan | `Free` |
| Health check path | `/health` |

The Dockerfile already binds Gunicorn to Render's `PORT`; no separate frontend service, build command, or start command is needed. Render provides an HTTPS `onrender.com` address after deployment. [Docker setup](https://render.com/docs/docker), [regions](https://render.com/docs/regions), [port binding](https://render.com/docs/web-services), [health checks](https://render.com/docs/health-checks).

## 3. Add private environment variables

Before clicking **Create Web Service**, open **Advanced** and add the environment variables below. If the service was already created, add them on its **Environment** page and redeploy. Enter each value privately in Render; never paste real secrets into a tracked file, screenshot, or chat. [Render environment variables](https://render.com/docs/configure-environment-variables).

| Key | Value |
| --- | --- |
| `DATABASE_URL` | Completed Supabase Shared/Session pooler URI, including `sslmode=require` |
| `APP_ENV` | `production` |
| `FLASK_SECRET_KEY` | Stable random value of at least 32 characters |
| `ADMIN_EMAILS` | Your administrator email address |
| `TRUSTED_PROXY_COUNT` | `1` |
| `EMAIL_PIN_DEV_MODE` | `0` |

Generate `FLASK_SECRET_KEY` on this laptop with `.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"`, then paste it directly into Render's private value field. Keep it unchanged on later deploys so existing sign-ins remain valid. The app requires a real email PIN provider in production. Choose one of the email options below.

### Email option A: try Brevo Free without buying a domain

Brevo's Free plan includes transactional email and 300 sends per day with no time limit. Brevo lets you verify a personal email address as a sender, but may rewrite that address for delivery. Create and verify the sender, then find the **SMTP login** and **SMTP key** in Brevo. Add these Render variables:

| Key | Value |
| --- | --- |
| `SMTP_HOST` | `smtp-relay.brevo.com` |
| `SMTP_PORT` | `2525` |
| `SMTP_USERNAME` | Your Brevo SMTP login |
| `SMTP_PASSWORD` | Your Brevo **SMTP key**, not its API key |
| `SMTP_FROM` | The personal email address you verified in Brevo |
| `SMTP_USE_TLS` | `1` |

Render Free blocks SMTP ports `25`, `465`, and `587`, while Brevo supports `2525`. This path is plausible but **has not been tested on this Render account**. Send a real PIN to a second email address before inviting students; Brevo may require additional account activation or domain verification. [Brevo Free](https://help.brevo.com/hc/en-us/articles/208589409-About-Brevo-s-pricing-plans), [sender verification](https://help.brevo.com/hc/en-us/articles/208836149-Create-a-new-sender-From-name-and-From-email), [SMTP setup](https://help.brevo.com/hc/en-us/articles/7924908994450-Send-transactional-emails-using-Brevo-SMTP), [SMTP port](https://help.brevo.com/hc/en-us/articles/10905415650322-Which-SMTP-port-should-I-use-Port-587-465-or-2525), [Render Free limits](https://render.com/docs/free).

### Email option B: Resend with a domain you own

Set `RESEND_API_KEY` and `RESEND_FROM` in Render instead of the SMTP variables. For students to register with their own email addresses, verify a sending domain in Resend first and use an address on that domain for `RESEND_FROM`. Resend's default `onboarding@resend.dev` sender can only email the address associated with the Resend account. Resend has a free email allowance, but acquiring a domain may cost money if you do not already own one. The app calls Resend through HTTPS, so Render's SMTP port block does not affect this option. [Resend test-domain restriction](https://resend.com/docs/knowledge-base/403-error-resend-dev-domain), [domain setup](https://resend.com/docs/add-a-domain), [pricing](https://resend.com/pricing).

## 4. Deploy and test

Create the service and wait for its build and deploy logs to finish. Render Free does **not** offer a pre-deploy command or one-off shell job. That is fine for this first deploy because the Supabase schema and catalogue were already created locally. For future schema changes, run `.\.venv\Scripts\python.exe backend/manage.py migrate` and `.\.venv\Scripts\python.exe backend/seed_job_catalog.py seed` from this laptop with its private `config/env`, then verify with `.\.venv\Scripts\python.exe backend/seed_job_catalog.py verify --database` before pushing the new app version. [Render deploy steps](https://render.com/docs/deploys), [Free limitations](https://render.com/docs/free).

Open the Render URL and check, in order:

1. `/health` returns `status: ok`, then the home page loads.
2. Job roles appear; this checks the live Supabase connection (`/health` alone does not).
3. Request a registration PIN, receive the email, register, and sign in.
4. Upload a text-based PDF resume, view the analysis, download the report, then sign out and back in to confirm the analysis remains in history.

For supervisor and student testing, open the site shortly before the session. A Free service sleeps after 15 minutes without traffic and can take about a minute to wake. Free provides 0.1 CPU, 512 MB RAM, and 750 instance hours per workspace each month; PDF analysis and image OCR may be slow or run out of memory if several students use it together. Its local filesystem is temporary, so collect research evaluation results separately rather than relying on files inside the container. [Render Free limits](https://render.com/docs/free), [compute plans](https://render.com/docs/compute-plans).

A Supabase Free project may pause after low activity; check its dashboard and resume it before a scheduled testing session. [Supabase project pausing](https://supabase.com/docs/guides/platform/free-project-pausing).
