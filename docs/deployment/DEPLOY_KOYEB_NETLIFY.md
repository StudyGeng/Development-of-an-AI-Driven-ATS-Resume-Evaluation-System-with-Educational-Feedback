# Method 2 staging: Netlify frontend + Koyeb API

This guide deploys the `method_2` branch as a **separate staging site**. Keep the existing Render site on `main` for current testers. Netlify serves the HTML, CSS and JavaScript; the Koyeb Docker service runs Flask, resume analysis, PDF generation, PIN email and database access. Supabase remains the PostgreSQL database. A paid Koyeb instance with fixed scaling at one instance stays running; the Koyeb Free instance sleeps after one hour of inactivity. [Koyeb scaling](https://www.koyeb.com/docs/reference/scaling), [Koyeb Free behavior](https://www.koyeb.com/docs/run-and-scale/scale-to-zero).

The two default hostnames (`*.netlify.app` and `*.koyeb.app`) are different sites. This branch supports cross-site credentialed API calls and partitioned session cookies for testing. Latest browsers support partitioned cookies, but older browsers may not. For a later public release, use sibling custom domains such as `app.example.com` and `api.example.com` and repeat the browser tests. [MDN partitioned cookies](https://developer.mozilla.org/en-US/docs/Web/Privacy/Guides/Third-party_cookies/Partitioned_cookies).

The Netlify site has its own browser storage. Reports and history saved in the current Render site's browser storage will not automatically appear on the Netlify site; create a new staging analysis when testing the return-to-report flow.

## 1. Deploy the Koyeb service from `method_2`

In Koyeb, create a **Web Service** from this GitHub repository. Select branch **`method_2`**, the repository-root `Dockerfile`, and Singapore (`SIN`) if available. Keep the Dockerfile's command. Expose **HTTP port 8080** at route `/`; Koyeb supplies `PORT` from the exposed port, and the Dockerfile binds Gunicorn to it. Configure an **HTTP health check** on `/health`. Select a **paid** instance and **fixed scaling: 1 instance** with scale-to-zero disabled. `eco-medium` (1 vCPU, 2 GB RAM) is a reasonable starting point for OCR and student testing; measure real analysis time and memory before choosing a smaller instance. The Free instance has 0.1 vCPU, can run only in Frankfurt or Washington, and always sleeps after inactivity. [Deploy with GitHub](https://www.koyeb.com/docs/build-and-deploy/deploy-with-git), [Koyeb instance sizes](https://www.koyeb.com/docs/reference/instances), [health checks](https://www.koyeb.com/docs/run-and-scale/health-checks).

Add these **private Koyeb service environment variables**. Paste actual secrets only into Koyeb, never Git, Netlify, screenshots, or chat. Use the existing provider's working PIN email settings; production startup requires one complete provider configuration.

| Variable | Staging value |
| --- | --- |
| `APP_ENV` | `production` |
| `DATABASE_URL` | Private Supabase Session pooler PostgreSQL URI with `sslmode=require` |
| `FLASK_SECRET_KEY` | Stable, unique random value of at least 32 characters |
| `ADMIN_EMAILS` | Your administrator email address |
| `EMAIL_PIN_DEV_MODE` | `0` |
| `TRUSTED_PROXY_COUNT` | `1` for the single trusted Koyeb edge hop; verify HTTPS POSTs after deploy |
| `CORS_ALLOWED_ORIGINS` | Exact Netlify site origin, for example `https://your-staging-name.netlify.app` (no trailing path or `*`) |
| `CROSS_SITE_SESSION_COOKIES` | `1` |
| `SESSION_COOKIE_PARTITIONED` | `1` |
| PIN email provider | `BREVO_API_KEY`, `BREVO_FROM`, optional `BREVO_FROM_NAME`; or a complete Resend/SMTP configuration |

Koyeb provides `PORT` automatically; make sure the exposed port is `8080`. Keep `FLASK_SECRET_KEY` unchanged on redeploys so active sessions remain valid. The `CORS_ALLOWED_ORIGINS` value must match the eventual Netlify **site URL exactly**. If Netlify assigns a different name, edit that value in Koyeb and redeploy. Do not place any backend secret or `DATABASE_URL` in Netlify. [Koyeb environment variables](https://www.koyeb.com/docs/build-and-deploy/environment-variables), [Koyeb exposing services](https://www.koyeb.com/docs/build-and-deploy/exposing-your-service).

The Docker image still contains the Flask HTML routes, so the Koyeb URL can serve pages directly for troubleshooting. The intended staging URL is the Netlify site. If the same Supabase database is used by Render and Koyeb, staging accounts and analyses share that database; use a separate staging database if data isolation is required. Apply any pending schema migration to the chosen database before testing profile photos or analysis history.

After Koyeb deploys, copy the public HTTPS API **origin** from its `*.koyeb.app` URL. Open `<KOYEB_ORIGIN>/health` and confirm JSON with `"status": "ok"`. A healthy endpoint only checks the web process; the role list, PIN, history and PDF tests below check its dependencies.

## 2. Deploy the Netlify site from `method_2`

Create a **new Netlify site** linked to the same GitHub repository and set its **production branch to `method_2`**. Leave the base directory at the repository root. This branch's `netlify.toml` runs `npm run build:netlify` and publishes only `dist/`. The build creates Tailwind CSS, copies static frontend files and writes a public `runtime_config.js` into `dist/`. It fails if the backend URL is missing, insecure, or contains a path. The tracked Flask default config stays unchanged. [Netlify build configuration](https://docs.netlify.com/build/configure-builds/overview/), [Netlify environment variables](https://docs.netlify.com/build/environment-variables/get-started/).

Under Netlify **environment variables**, set:

| Variable | Value |
| --- | --- |
| `PUBLIC_API_BASE_URL` | Exact Koyeb HTTPS origin, for example `https://your-api.koyeb.app` (no path or trailing slash needed) |

This is the **only** value written into public JavaScript. Its name starts with `PUBLIC` to make that visible. Do not add `DATABASE_URL`, Flask session secrets, or email API keys to the Netlify site. Select a stable Netlify site name, then set the resulting `https://<name>.netlify.app` origin as Koyeb's `CORS_ALLOWED_ORIGINS` and redeploy the Koyeb service if needed. Netlify's root URL rewrites to `home_page.html`; the other existing `.html` routes are published as static files. The app calls Koyeb directly, avoiding Netlify's 26-second proxy timeout for long analyses. [Netlify proxy timeout](https://docs.netlify.com/manage/routing/redirects/rewrites-proxies/).

## 3. Test the two-site flow

Use the **Netlify** URL for these checks in an up-to-date browser, including Edge and a mobile browser:

1. Open `/`, `home_page.html`, `analysis_result.html?sample=1`, and the page sections. Confirm CSS, account modal and fictional sample load.
2. In browser developer tools, confirm `/auth/csrf-token` and `/auth/session` go to the Koyeb origin, return the expected CORS allow-origin and credentials headers, and carry a session cookie with `Secure`, `SameSite=None`, and `Partitioned` where supported.
3. Request a sign-up PIN and receive it, create a test account, sign in, then reload and confirm the session persists. Test a wrong password and password reset/PIN delivery.
4. Load the role list, analyze a text PDF and an image resume, reopen the real report, inspect history and profile, then download the feedback and generated-resume PDFs.
5. Open the admin insights page using an admin account, then verify a normal account cannot fetch any admin data. Static Netlify HTML is publicly downloadable; backend admin API authorization remains the security boundary.
6. Compare a cold page load and analysis duration with the current Render site under similar conditions. If analysis is still slow, inspect Koyeb CPU/memory, request logs, OCR time and Supabase latency before changing instance size or analysis code.

If login succeeds but a reload signs the user out, inspect the cookie and CORS headers first. Default Netlify and Koyeb domains require cross-site cookies, and browser privacy settings or older browser versions can prevent those cookies from being sent. A future pair of sibling custom domains is the most reliable long-term setup. If HTTPS POSTs fail with CSRF/origin errors, confirm the exact `CORS_ALLOWED_ORIGINS` value and trusted proxy configuration. Never loosen CORS to `*` for authenticated requests.

Koyeb's HTTP edge closes requests after **100 seconds**, even though Gunicorn's configured timeout is 120 seconds. An unusually long OCR/analysis request can still time out. The paid always-running instance removes a cold start, and Netlify serves static files quickly; actual analysis time still depends on CPU, OCR, database latency and workload. [Koyeb edge timeout](https://www.koyeb.com/docs/reference/edge-network).

## Promotion and rollback

This guide does not switch the existing public Render URL or `main` branch. Keep the Koyeb and Netlify services pinned to `method_2` for testing. When staging passes sign-in, analysis, history, admin and PDF checks, update the public link or deployment settings deliberately. To stop testing, disable the new Netlify/Koyeb services; the current `main` Render app remains available.
