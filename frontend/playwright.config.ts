import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";
import path from "node:path";

const localPython = path.resolve(
  "..",
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
const python =
  process.env.RESOLVEAI_PYTHON ??
  (existsSync(localPython) ? localPython : "python");
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  timeout: 45000,
  expect: { timeout: 15000 },
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:3011",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 1000 },
  },
  webServer: [
    {
      command: `"${python}" scripts/e2e_backend.py --port 8011`,
      url: "http://127.0.0.1:8011/api/ready",
      reuseExistingServer: false,
      timeout: 90000,
    },
    {
      command: "node scripts/start.mjs --hostname 127.0.0.1 --port 3011",
      url: "http://127.0.0.1:3011",
      env: {
        BACKEND_URL: "http://127.0.0.1:8011",
        NEXT_TELEMETRY_DISABLED: "1",
      },
      reuseExistingServer: false,
      timeout: 90000,
    },
  ],
});
