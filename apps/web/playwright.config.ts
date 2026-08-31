import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:3100";

/** Start a deterministic local app by default; an explicit URL only reuses an existing app server. */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  outputDir: "../../output/playwright/test-results",
  reporter: [["list"], ["html", { outputFolder: "../../output/playwright/report", open: "never" }]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] } },
  ],
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : {
        command: "npm run dev -- --hostname 127.0.0.1 --port 3100",
        url: baseURL,
        reuseExistingServer: true,
        timeout: 120_000,
        env: {
          NEXUSPILOT_API_KEY: "deterministic-browser-test-key",
          NEXUSPILOT_API_URL: "http://127.0.0.1:65534",
        },
      },
});
