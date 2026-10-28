import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const pages = [
  { path: '/', heading: "One shopping list for every pedal you're building." },
  { path: '/404.html', heading: "This page doesn't exist." },
];

for (const { path, heading } of pages) {
  test.describe(path, () => {
    test.beforeEach(async ({ page }) => {
      await page.goto(path);
    });

    test('shows its heading', async ({ page }) => {
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(heading);
    });

    test('has no accessibility violations', async ({ page }) => {
      const results = await new AxeBuilder({ page }).analyze();
      expect(results.violations).toEqual([]);
    });

    test('fits the window without scrolling sideways', async ({ page }) => {
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
    });

    test('marks every standalone link with a house icon', async ({ page }) => {
      // House style: a link outside a sentence carries an icon saying where it goes. The header
      // bar's section links and the skip link are navigation chrome, not content links.
      const bare = await page
        .locator('main a, .hs-footer-links a')
        .filter({ hasNot: page.locator('svg') })
        .evaluateAll((links) =>
          links.filter((a) => !a.closest('p')).map((a) => a.textContent?.trim()),
        );
      expect(bare).toEqual([]);
    });
  });
}

test('the moving field loads only after the page has', async ({ page }) => {
  // House style: the first screen is text, and nothing heavy holds it up.
  // Waits for the field's script itself, not the pause button: a browser
  // without WebGL2 (headless Firefox on Linux) loads it but keeps the button
  // hidden.
  await page.goto('/');
  const timing = () =>
    page.evaluate(() => {
      const [navigation] = performance.getEntriesByType(
        'navigation',
      ) as PerformanceNavigationTiming[];
      const field = performance
        .getEntriesByType('resource')
        .filter((entry) => /\/field\.[\w-]+\.js$/.test(entry.name))
        .map((entry) => entry.startTime);
      return { loaded: navigation.loadEventEnd, field };
    });
  // WebKit records the resource entry a moment after the response arrives.
  await expect.poll(async () => (await timing()).field.length).toBeGreaterThan(0);
  const { loaded, field } = await timing();
  for (const start of field) expect(start).toBeGreaterThanOrEqual(loaded);
});

test('uses the house fonts', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const loaded = await page.evaluate(() =>
    [...document.fonts]
      .filter((f) => f.status === 'loaded')
      .map((f) => f.family.replaceAll('"', '')),
  );
  expect(loaded).toEqual(expect.arrayContaining(['DM Sans']));
});

test('section links reach their sections', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name.endsWith('-mobile'), 'Two section links hide on phones.');
  await page.goto('/');
  for (const name of ['How it works', 'Documents', 'Accuracy']) {
    await page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name }).click();
    await expect(page.locator(`section:target`)).toBeInViewport();
  }
});

test('the moving field survives a lost WebGL context', async ({ page }) => {
  // A GPU reset or a phone reclaiming memory drops the context. house-style's
  // runField asks for it back, and the board rebuilds its programs and buffers.
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    // Drawing with the old context's programs logs a WebGL warning, not an error.
    if (message.type() === 'error' || /WebGL.*INVALID/.test(message.text())) {
      errors.push(message.text());
    }
  });
  await page.goto('/');
  const motion = page.getByRole('button', { name: 'Play motion', exact: true });
  const running = await motion
    .waitFor({ timeout: 10_000 })
    .then(() => true)
    .catch(() => false);
  test.skip(!running, 'This browser has no WebGL2, so the field never starts');

  const restored = await page.evaluate(async () => {
    const canvas = document.querySelector<HTMLCanvasElement>('.hs-field')!;
    const lose = canvas.getContext('webgl2')?.getExtension('WEBGL_lose_context');
    if (!lose) return 'unsupported';
    const event = (name: string) =>
      new Promise((resolve) => canvas.addEventListener(name, resolve, { once: true }));
    // restoreContext() does nothing until the loss has been delivered and its
    // listeners, runField's among them, have finished.
    const lost = event('webglcontextlost');
    lose.loseContext();
    await lost;
    await new Promise((resolve) => setTimeout(resolve));
    const back = event('webglcontextrestored');
    lose.restoreContext();
    await back;
    return canvas.getContext('webgl2')?.isContextLost() ? 'lost' : 'restored';
  });
  test.skip(restored === 'unsupported', 'This engine has no WEBGL_lose_context');
  expect(restored).toBe('restored');
  // The button still drives the field, and nothing logged an error.
  await motion.click();
  await page.getByRole('button', { name: 'Pause motion', exact: true }).waitFor();
  expect(errors).toEqual([]);
});
