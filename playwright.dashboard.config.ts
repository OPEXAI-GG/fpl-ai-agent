import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/browser',
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:8772', browserName: 'chromium' },
  webServer: {
    command: process.platform === 'win32'
      ? '.venv\\Scripts\\python -u -m tests.dashboard_fixture_server'
      : '.venv/bin/python -u -m tests.dashboard_fixture_server',
    url: 'http://127.0.0.1:8772',
    reuseExistingServer: false,
    timeout: 60000,
  },
});
