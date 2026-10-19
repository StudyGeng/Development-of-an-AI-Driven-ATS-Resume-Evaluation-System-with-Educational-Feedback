const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const frontend = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(frontend, "app_common.js"), "utf8");

function makePage(apiBaseUrl) {
  const calls = [];
  const pageOrigin = "https://career-navigator.netlify.app";
  const window = {
    APP_CONFIG: { apiBaseUrl },
    location: { origin: pageOrigin, href: pageOrigin + "/home_page.html", protocol: "https:" },
    fetch: async (input, options) => {
      calls.push({ input: String(input), options });
      if (String(input).endsWith("/auth/csrf-token")) {
        return { ok: true, json: async () => ({ csrf_token: "test-csrf" }) };
      }
      if (String(input).endsWith("/auth/session")) {
        return { ok: true, json: async () => ({ authenticated: false }) };
      }
      return { ok: true, status: 200 };
    },
    dispatchEvent() {}
  };
  const context = vm.createContext({
    window,
    document: { addEventListener() {} },
    localStorage: { getItem: () => null, removeItem() {}, setItem() {} },
    URL,
    Headers,
    Request,
    CustomEvent: class CustomEvent {}
  });
  vm.runInContext(source, context, { filename: "app_common.js" });
  return { window, calls };
}

async function main() {
  for (const page of ["home_page.html", "analysis_result.html", "job_data_insights.html"]) {
    const html = fs.readFileSync(path.join(frontend, page), "utf8");
    assert.match(html, /<script src="runtime_config\.js"><\/script>\s*<script src="app_common\.js"><\/script>/);
    assert.match(html, /CareerNavigatorCommon\.getApiBaseUrl\(\)/);
  }

  const remote = makePage("https://career-api.koyeb.app/");
  assert.equal(remote.window.CareerNavigatorCommon.getApiBaseUrl(), "https://career-api.koyeb.app");

  await remote.window.CareerNavigatorCommon.syncAuthUserFromServer();
  assert.equal(remote.calls.at(-1).input, "https://career-api.koyeb.app/auth/session");
  assert.equal(remote.calls.at(-1).options.credentials, "include");

  await remote.window.fetch("https://career-api.koyeb.app/users/login", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: "{}"
  });
  assert.equal(remote.calls.at(-2).input, "https://career-api.koyeb.app/auth/csrf-token");
  assert.equal(remote.calls.at(-2).options.credentials, "include");
  assert.equal(remote.calls.at(-1).options.credentials, "include");
  assert.equal(remote.calls.at(-1).options.headers.get("X-CSRF-Token"), "test-csrf");

  await remote.window.fetch("https://career-api.koyeb.app/users/me", {
    method: "PATCH",
    body: "{}"
  });
  assert.equal(remote.calls.at(-1).options.credentials, "include");
  assert.equal(remote.calls.at(-1).options.headers.get("X-CSRF-Token"), "test-csrf");

  await remote.window.fetch("https://career-api.koyeb.app/users/me/avatar", {
    method: "PUT",
    body: "fake-form-data"
  });
  assert.equal(remote.calls.at(-1).options.credentials, "include");
  assert.equal(remote.calls.at(-1).options.headers.get("Content-Type"), null);

  await remote.window.fetch("https://career-api.koyeb.app/feedback-report-pdf", { method: "POST" });
  assert.equal(remote.calls.at(-1).options.credentials, "include");

  await remote.window.fetch("https://fonts.googleapis.com/css2", { method: "GET" });
  assert.equal(remote.calls.at(-1).options.credentials, undefined);

  const local = makePage("");
  assert.equal(local.window.CareerNavigatorCommon.getApiBaseUrl(), "https://career-navigator.netlify.app");
  await local.window.fetch("https://career-navigator.netlify.app/users/login", { method: "POST" });
  assert.equal(local.calls.at(-2).input, "https://career-navigator.netlify.app/auth/csrf-token");
  assert.equal(local.calls.at(-1).options.credentials, "include");

  console.log("API origin, session, CSRF, credentials, upload, and PDF smoke checks passed.");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
