import { defineConfig, devices } from '@playwright/test';

// E2E drives the REAL app: vite dev (5173) → backend (8788, throwaway SQLite DB)
// → a deterministic stdlib mock upstream (8910). Playwright boots all three.
const CI = !!process.env.CI;
// Local dev uses the backend virtualenv; CI installs the backend into its own
// interpreter, so it sets E2E_PYTHON=python.
const PY = process.env.E2E_PYTHON ?? '.venv/bin/python';

export default defineConfig({
  testDir: './e2e',
  // The three servers share one backend DB, so run serially for determinism.
  workers: 1,
  fullyParallel: false,
  forbidOnly: CI,
  retries: CI ? 1 : 0,
  reporter: 'list',
  timeout: 30_000,
  globalSetup: './e2e/global-setup.ts',
  use: {
    baseURL: 'http://localhost:5173',
    storageState: 'e2e/.auth/admin.json',
    trace: 'on-first-retry'
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: 'python3 e2e/mock_upstream.py 8910',
      url: 'http://127.0.0.1:8910/v1/models',
      reuseExistingServer: !CI,
      stdout: 'ignore'
    },
    {
      // fresh DB each boot; the real backend, pointed at the mock upstream.
      // Port 8788 (not 8000) to dodge anything already squatting the usual port.
      command: `sh -c "rm -f .e2e.db && exec ${PY} -m uvicorn app.main:app --port 8788"`,
      cwd: '../backend',
      url: 'http://127.0.0.1:8788/api/health',
      reuseExistingServer: !CI,
      timeout: 60_000,
      env: {
        FREE_WEBUI_UPSTREAM_BASE_URL: 'http://127.0.0.1:8910/v1',
        FREE_WEBUI_DB_PATH: '.e2e.db',
        FREE_WEBUI_DEFAULT_MODEL: 'e2e-model'
      }
    },
    {
      command: 'npm run dev',
      url: 'http://localhost:5173',
      reuseExistingServer: !CI,
      timeout: 120_000,
      env: { FW_API_TARGET: 'http://127.0.0.1:8788' }
    }
  ]
});
