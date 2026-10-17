import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: { ...devices['Desktop Chrome'],
    channel: process.env.BRIDGE_MONITOR_PLAYWRIGHT_CHANNEL || undefined,
    baseURL: 'http://127.0.0.1:8874', trace: 'retain-on-failure' },
  webServer: {
    command: '..\\.venv\\Scripts\\python.exe tests\\server.py',
    url: 'http://127.0.0.1:8874/',
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
