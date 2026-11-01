import { mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { defineConfig, devices } from '@playwright/test';

// Firefox's shared app-data lookup fails under macOS profile protections, so keep the test
// browser's preferences apart from installed Firefox (as house-style's tests do).
const firefoxHome = process.platform === 'darwin' ? join(tmpdir(), 'star-ground-firefox') : null;
if (firefoxHome) mkdirSync(firefoxHome, { recursive: true });
const firefoxLaunch = firefoxHome
  ? { launchOptions: { env: { ...process.env, CFFIXED_USER_HOME: firefoxHome } } }
  : {};

// SITE_URL points the browser tests at an already-running site; otherwise they serve the build.
const siteUrl = process.env.SITE_URL;
const baseURL = siteUrl ?? 'http://127.0.0.1:4503';
const desktop = { viewport: { width: 1440, height: 1000 } };

export default defineConfig({
  testDir: 'tests/browser',
  outputDir: 'test-results',
  timeout: 60_000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  // One retry absorbs a slow CI runner; a test that needed it is reported as flaky.
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  use: {
    baseURL,
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
  },
  // Desktop and phone in each engine. WebKit is Safari's engine, not Safari itself; Firefox has
  // no mobile emulation, so its phone project is a narrow, touch, high-density window.
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'], ...desktop } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'], ...desktop, ...firefoxLaunch } },
    { name: 'webkit', use: { ...devices['Desktop Safari'], ...desktop } },
    { name: 'chromium-mobile', use: devices['Pixel 7'] },
    {
      name: 'firefox-mobile',
      use: {
        browserName: 'firefox',
        ...firefoxLaunch,
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 3,
        hasTouch: true,
      },
    },
    { name: 'webkit-mobile', use: devices['iPhone 15'] },
  ],
  webServer: siteUrl
    ? undefined
    : {
        command: 'pnpm exec astro preview --ignore-lock --host 127.0.0.1 --port 4503',
        url: baseURL,
        reuseExistingServer: false,
        timeout: 30_000,
      },
});
