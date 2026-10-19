import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const frontendDirectory = path.join(projectRoot, "frontend");
const publishDirectory = path.join(projectRoot, "dist");
const rawApiBaseUrl = process.env.PUBLIC_API_BASE_URL?.trim();

if (process.env.NETLIFY === "true" && process.env.BRANCH && process.env.BRANCH !== "method_2") {
  throw new Error("This Netlify staging build must use the method_2 branch.");
}

if (!rawApiBaseUrl) {
  throw new Error("PUBLIC_API_BASE_URL is required and must be the HTTPS origin of the Koyeb API.");
}

let apiUrl;
try {
  apiUrl = new URL(rawApiBaseUrl);
} catch {
  throw new Error("PUBLIC_API_BASE_URL must be a valid HTTPS origin.");
}

if (
  apiUrl.protocol !== "https:" ||
  !apiUrl.hostname ||
  apiUrl.username ||
  apiUrl.password ||
  apiUrl.pathname !== "/" ||
  apiUrl.search ||
  apiUrl.hash
) {
  throw new Error("PUBLIC_API_BASE_URL must be an HTTPS origin without a path, query, fragment, or credentials.");
}

// Never remove any directory except this project's fixed, ignored publish directory.
if (publishDirectory !== path.join(projectRoot, "dist") || path.relative(projectRoot, publishDirectory) !== "dist") {
  throw new Error("Unexpected Netlify publish directory.");
}

fs.rmSync(publishDirectory, { recursive: true, force: true });
fs.mkdirSync(publishDirectory, { recursive: true });

for (const fileName of fs.readdirSync(frontendDirectory)) {
  if (!/\.(html|css|js)$/i.test(fileName) || fileName === "tailwind.input.css" || fileName === "runtime_config.js") {
    continue;
  }
  const sourcePath = path.join(frontendDirectory, fileName);
  if (fs.statSync(sourcePath).isFile()) {
    fs.copyFileSync(sourcePath, path.join(publishDirectory, fileName));
  }
}

const config = { apiBaseUrl: apiUrl.origin };
fs.writeFileSync(
  path.join(publishDirectory, "runtime_config.js"),
  `window.APP_CONFIG = ${JSON.stringify(config)};\n`,
  "utf8"
);

console.log(`Prepared Netlify static files for API origin ${apiUrl.origin}.`);
