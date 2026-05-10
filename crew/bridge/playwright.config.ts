import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 300_000, // 5 minutes — VS Code launch + response can be slow
  retries: 1,
  use: {
    trace: 'on-first-retry',
  },
});
