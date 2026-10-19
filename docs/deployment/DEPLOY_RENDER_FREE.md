# Host the Flask app on Render Free with Supabase

This option runs the existing Flask website and API in one Render web service. Supabase remains the PostgreSQL database. It is suitable for a small FYP testing session; Render Free has limited CPU and memory and sleeps when idle. [Render Free limits](https://render.com/docs/free), [compute plans](https://render.com/docs/compute-plans).

## 1. Put the current code on GitHub

The project already has a root `Dockerfile` that builds the frontend CSS, installs Python and Tesseract, and starts Gunicorn. Render can build it directly from a GitHub repository. **Use the intended live commit on `main` when creating or updating the service**; Render cannot deploy changes that exist only on your laptop. Use `update_version` for further work, while `old_version` preserves the previous `main` release. Keep `config/env` and all passwords out of GitHub. [Render Docker deployment](https://render.com/docs/docker), [web service sources](https://render.com/docs/web-services).

The Supabase testing database has already been migrated and seeded locally. The role list and analysis history stay in Supabase when the Render process sleeps or restarts. The completed database URI is private; use the **Shared/Session pooler** connection string on port `5432` with `sslmode=require`. [Supabase connection methods](https://supabase.com/docs/guides/database/connecting-to-postgres).

## 2. Create a free web service

In [Render Dashboard](https://dashboard.render.com/), choose **New → Web Service → Git Provider**, connect GitHub, and select the repository containing this `Dockerfile`. Set:

| Setting | Value |
| --- | --- |
| Branch | `main` |
| Language/Runtime | `Docker` |
| Root directory | Repository root (leave blank unless Render asks) |
| Region | `Singapore` |
| Instance type/Compute plan | `Free` |
| Health check path | `/health` |
| Secret files | Leave empty |
| Registry credential | `No credential` |
| Docker build context directory | `.` |
| Dockerfile path | `./Dockerfile` |
| Docker command | Leave empty |

For an existing Render service, check its selected branch in the Render dashboard and set it to `main` for the current layout. If automatic deploy is on, a push to `main` starts a deployment; otherwise choose **Manual Deploy → Deploy latest commit** after selecting `main`. The `old_version` branch keeps the former layout available for rollback. Editing this guide does not change the service setting.

The Dockerfile already binds Gunicorn to Render's `PORT`; no separate frontend service, build command, or start command is needed. Render provides an HTTPS `onrender.com` address after deployment. [Docker setup](https://render.com/docs/docker), [regions](https://render.com/docs/regions), [port binding](https://render.com/docs/web-services), [health checks](https://render.com/docs/health-checks).

## 3. Add private environment variables

Before clicking **Deploy web service**, find **Environment Variables**. If the service was already created, use its **Environment** page and redeploy after saving. Enter values privately in Render; never paste real secrets into a tracked file, screenshot, or chat. [Render environment variables](https://render.com/docs/configure-environment-variables).

For this laptop, a private import file is prepared at `config/.env.render`. It contains the six settings below plus the Brevo sender address and name. Complete the Brevo steps in the next section and add its real `BREVO_API_KEY` privately. Then open `config/.env.render`, copy its contents, choose **Add from .env** in Render, and paste them into that dialog. Review the imported rows before deploying. This file is ignored by Git and must stay private; do not upload it to GitHub or add it under Secret Files.

To regenerate that file from the local settings, run `.\.venv\Scripts\python.exe tools/prepare_render_env.py` from the project folder. It keeps an existing generated session secret and privately entered Brevo API key while excluding Gmail passwords and unrelated settings. After adding the API key, run `.\.venv\Scripts\python.exe tools/prepare_render_env.py --check`; this reports missing setting names without displaying their values. These checks validate configuration only, without contacting Supabase or sending email.

| Key | Value |
| --- | --- |
| `DATABASE_URL` | Completed Supabase Shared/Session pooler URI, including `sslmode=require` |
| `APP_ENV` | `production` |
| `FLASK_SECRET_KEY` | Stable random value of at least 32 characters |
| `ADMIN_EMAILS` | Your administrator email address |
| `TRUSTED_PROXY_COUNT` | `1` |
| `EMAIL_PIN_DEV_MODE` | `0` |

The private import file includes a generated `FLASK_SECRET_KEY`; keep that value unchanged on later deploys so existing sign-ins remain valid. If entering rows manually instead, use Render's **Generate** button only for `FLASK_SECRET_KEY`. The app requires a configured email PIN provider in production. Complete one of the email options below before deploying.

### Email option A: Brevo Free over HTTPS

The app supports Brevo's HTTPS email API. This connection avoids the SMTP ports blocked by Render Free. Brevo Free includes transactional email and up to 300 email sends per day, with no time limit. [Brevo Free limits](https://help.brevo.com/hc/en-us/articles/208580669-FAQs-What-are-the-limits-of-the-Free-plan), [API sending](https://developers.brevo.com/docs/send-a-transactional-email), [Render Free limits](https://render.com/docs/free).

1. Create a [Brevo Free account](https://onboarding.brevo.com/) using your existing Gmail. Complete its account verification and profile steps with your own details.
2. In Brevo, open the account dropdown, then **Settings > Senders, Domains, IPs > Senders > Add a sender**. Set **From name** to `UTS ATS System` and **From email** to your Gmail address. Save it, open the verification email in Gmail, and enter the six-digit code in Brevo. [Sender setup](https://help.brevo.com/hc/en-us/articles/208836149-Create-a-new-sender-From-name-and-From-email).
3. Open **Settings > SMTP & API > API Keys & MCP > Generate a new API key**. Complete any verification prompt, name the key `ATS Render`, choose its expiry, and generate it. Use a normal **API key**, not an SMTP key or MCP key. Copy it while it is displayed. [API key creation](https://help.brevo.com/hc/en-us/articles/209467485-Create-and-manage-your-API-keys).
4. Paste that key privately after `BREVO_API_KEY=` in `config/.env.render`. Confirm `BREVO_FROM` matches the sender email you verified. Import the completed file into Render, or enter the three rows below manually.

| Key | Value |
| --- | --- |
| `BREVO_API_KEY` | Your real Brevo API key, entered privately |
| `BREVO_FROM` | Only the verified email address, for example `yourgmail@gmail.com` |
| `BREVO_FROM_NAME` | `UTS ATS System` (optional display name) |

For this Brevo setup, do not copy your local Gmail `SMTP_*` variables or unused `RESEND_*` variables into Render. `BREVO_FROM` takes just the email address; put the display name in `BREVO_FROM_NAME`.

**A blank `BREVO_API_KEY` is unfinished configuration.** With the prepared Brevo sender settings, production startup rejects a missing key. Keep `APP_ENV=production` and `EMAIL_PIN_DEV_MODE=0`; enter the real key before deploying. Starting successfully checks that configuration is present, not that Brevo accepted it or an email arrived.

Your Gmail remains your administrator email. When used as a Brevo sender, Gmail cannot be authenticated as a domain you own. Brevo currently replaces personal sender addresses with a compliant address for delivery, including an address ending in `t-sender-sib.com` for transactional messages. This is a temporary provider measure; it does not guarantee delivery. Brevo recommends an authenticated domain for longer-term use. [Personal sender handling](https://help.brevo.com/hc/en-us/articles/14925263522578-Comply-with-Gmail-Yahoo-and-Microsoft-s-requirements-for-email-senders).

Brevo may also require **transactional platform activation**. If your new account shows that sending is disabled or not activated, create a support ticket from that account and explain that this FYP app sends registration verification codes requested by its users. Wait for Brevo to enable sending before inviting students. [Activation troubleshooting](https://help.brevo.com/hc/en-us/articles/115000188150-Troubleshooting-Issues-with-Brevo-SMTP).

If Brevo rejects a request with **HTTP 401** and mentions an **unrecognized IP address**, authorize the hosting service's addresses. In the Render service, open **Connect > Outbound** and copy its IP ranges. In Brevo, open **Settings > Security > Authorized IPs > Authorize IP addresses** and add those ranges; CIDR ranges are supported. Keep existing trusted addresses and IP blocking enabled. A laptop check uses a different address from Render, so approving only the laptop does not authorize the hosted app. [Render outbound addresses](https://render.com/docs/outbound-ip-addresses), [Brevo authorized IPs](https://help.brevo.com/hc/en-us/articles/5740111683858-Authorize-and-block-IP-addresses-for-API-and-SMTP-security).

**Real email delivery has not yet been validated for this deployment.** After deploying, request a PIN through the website using an email address you control. Confirm that it arrives, check Spam as well, and finish registration with that PIN. If it fails, inspect **Transactional > Logs** in Brevo for the reason; a successful API request alone is not proof of inbox delivery. [Brevo email logs](https://help.brevo.com/hc/en-us/articles/360021533839-Manage-your-transactional-logs-and-email-previews).

### Email option B: Resend with a domain you own

Set `RESEND_API_KEY` and `RESEND_FROM` in Render instead of the SMTP variables. For students to register with their own email addresses, verify a sending domain in Resend first and use an address on that domain for `RESEND_FROM`. Resend's default `onboarding@resend.dev` sender can only email the address associated with the Resend account. Resend has a free email allowance, but acquiring a domain may cost money if you do not already own one. The app calls Resend through HTTPS, so Render's SMTP port block does not affect this option. [Resend test-domain restriction](https://resend.com/docs/knowledge-base/403-error-resend-dev-domain), [domain setup](https://resend.com/docs/add-a-domain), [pricing](https://resend.com/pricing).

## 4. Deploy and test

Create the service and wait for its build and deploy logs to finish. Render Free does **not** offer a pre-deploy command or one-off shell job. That is fine for this first deploy because the Supabase schema and catalogue were already created locally. For future schema changes, run `.\.venv\Scripts\python.exe backend/manage.py migrate` and `.\.venv\Scripts\python.exe backend/seed_job_catalog.py seed` from this laptop with its private `config/env`, then verify with `.\.venv\Scripts\python.exe backend/seed_job_catalog.py verify --database` before pushing the new app version. [Render deploy steps](https://render.com/docs/deploys), [Free limitations](https://render.com/docs/free).

The account profile stores photos in the `user_avatars` table. Run the migration before deploying this version of `main` so profile photo uploads have database storage.

Open the Render URL and check, in order:

1. `/health` returns `status: ok`, then the home page loads.
2. Job roles appear; this checks the live Supabase connection (`/health` alone does not).
3. Request a registration PIN, receive the email, register, and sign in.
4. Upload a text-based PDF resume, view the analysis, download the report, then sign out and back in to confirm the analysis remains in history.

### Fix a 502 with `FLASK_SECRET_KEY is required in production`

A deployment can show **Deploy succeeded** while the Flask worker fails to start. If the runtime logs contain that secret-key error followed by **Worker failed to boot**, the service is missing its production environment settings.

1. Finish the private `config/.env.render` file, including the real `BREVO_API_KEY`, and run the `--check` command above.
2. In the existing Render service, open **Environment > Add from .env** and paste that file's contents. If matching keys already exist, deliberately replace their values with the prepared values; do not leave an old blank `FLASK_SECRET_KEY` row.
3. Save the settings and choose **Save, rebuild, and deploy**. Ensure the service uses branch `main`; if it still shows an older commit, use **Manual Deploy > Deploy latest commit** after the intended live code is on `main` in GitHub.
4. Under **Settings**, set **Health Check Path** to `/health` (without a `z`). Wait for the worker to start, then open the site's `/health` URL and continue the tests above.

For supervisor and student testing, open the site shortly before the session. A Free service sleeps after 15 minutes without traffic and can take about a minute to wake. Free provides 0.1 CPU, 512 MB RAM, and 750 instance hours per workspace each month; PDF analysis and image OCR may be slow or run out of memory if several students use it together. Its local filesystem is temporary, so collect research evaluation results separately rather than relying on files inside the container. [Render Free limits](https://render.com/docs/free), [compute plans](https://render.com/docs/compute-plans).

A Supabase Free project may pause after low activity; check its dashboard and resume it before a scheduled testing session. [Supabase project pausing](https://supabase.com/docs/guides/platform/free-project-pausing).
