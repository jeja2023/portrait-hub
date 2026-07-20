import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

const projectRoot = fileURLToPath(new URL("../../", import.meta.url));
const runtimeRoot = fileURLToPath(new URL("../../.codex-tmp/e2e-runtime/", import.meta.url));
const e2ePort = Number(process.env.PLAYWRIGHT_PORT ?? "8766");

if (!Number.isInteger(e2ePort) || e2ePort < 1 || e2ePort > 65_535) {
  throw new Error("PLAYWRIGHT_PORT must be an integer between 1 and 65535");
}

const e2eBaseUrl = `http://127.0.0.1:${e2ePort}`;

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  timeout: 60_000,
  expect: { timeout: 8_000 },
  fullyParallel: true,
  workers: process.env.CI ? 4 : 5,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
  use: {
    baseURL: e2eBaseUrl,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium-desktop",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: "chromium-tablet",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1024, height: 768 },
      },
    },
    {
      name: "chromium-mobile",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 390, height: 844 },
      },
    },
    {
      name: "firefox-desktop",
      use: {
        ...devices["Desktop Firefox"],
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: "webkit-desktop",
      use: {
        ...devices["Desktop Safari"],
        viewport: { width: 1440, height: 900 },
      },
    },
  ],
  webServer: {
    command: `python -m uvicorn main:app --host 127.0.0.1 --port ${e2ePort}`,
    cwd: projectRoot,
    env: {
      AUTH_REQUIRED: "true",
      RBAC_ENABLED: "false",
      TENANT_HEADER_REQUIRED: "false",
      ENABLE_API_DOCS: "true",
      TRUSTED_HOSTS: "*",
      REQUIRE_ENCRYPTION: "false",
      HSTS_ENABLED: "false",
      ANALYSIS_ARCHIVE_ENABLED: "false",
      ENV_PATH: fileURLToPath(new URL("../../.codex-tmp/e2e.env", import.meta.url)),
      MODEL_CONFIG_PATH: fileURLToPath(new URL("../../models.yml", import.meta.url)),
      MODELS_ROOT: fileURLToPath(new URL("../../models/", import.meta.url)),
      MODEL_CONFIG_READ_FAIL_CLOSED: "false",
      RUNTIME_STATE_DIR: runtimeRoot,
      PORTRAIT_RUNTIME_PROFILE: "development",
      PRODUCTION_EXTERNAL_SERVICES_REQUIRED: "false",
      PORTRAIT_STORAGE_BACKEND: "json",
      PORTRAIT_VECTOR_BACKEND: "local",
      PORTRAIT_OBJECT_STORAGE_BACKEND: "local",
      TASK_QUEUE_BACKEND: "local",
    },
    url: `${e2eBaseUrl}/health`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
