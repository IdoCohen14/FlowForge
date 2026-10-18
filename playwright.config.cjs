const {defineConfig, devices} = require('@playwright/test');
// The Python harness owns a generated database, queue, uploads, and ephemeral URL.
// Never silently reuse a developer's running server for destructive UI fixtures.
if (process.env.FLOWFORGE_BROWSER_TESTS !== '1' || !/^http:\/\/127\.0\.0\.1:\d+$/.test(process.env.FLOWFORGE_BROWSER_URL || '')) {
  throw new Error('Run browser tests through python scripts/run_browser_tests.py');
}
module.exports = defineConfig({
  testDir: './tests/browser', fullyParallel: false, workers: 1,
  forbidOnly: !!process.env.CI, retries: 0, timeout: 45000,
  expect: {timeout: 15000}, reporter: [['list'], ['html', {open: 'never'}]],
  use: {baseURL: process.env.FLOWFORGE_BROWSER_URL, trace: 'retain-on-failure', screenshot: 'only-on-failure'},
  projects: [
    {name: 'desktop', use: {...devices['Desktop Chrome'], viewport: {width: 1440, height: 1000}}},
    {name: 'mobile', use: {...devices['Pixel 7'], browserName: 'chromium'}},
  ],
});
