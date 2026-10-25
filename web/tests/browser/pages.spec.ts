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
      // House style: a link outside a sentence carries an icon saying where it goes. Header
      // section links, the wordmark, and the skip link are navigation chrome, not content links.
      const bare = await page
        .locator('main a, footer .footer-bottom a')
        .filter({ hasNot: page.locator('svg.icon') })
        .evaluateAll((links) =>
          links.filter((a) => !a.closest('p')).map((a) => a.textContent?.trim()),
        );
      expect(bare).toEqual([]);
    });
  });
}

test('the landing page loads no JavaScript', async ({ page }) => {
  // House style: the first screen is text, and nothing heavy holds it up.
  const scripts: string[] = [];
  page.on('request', (request) => {
    if (request.resourceType() === 'script') scripts.push(request.url());
  });
  await page.goto('/');
  expect(scripts).toEqual([]);
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
  test.skip(testInfo.project.name.endsWith('-mobile'), 'The section links hide on phones.');
  await page.goto('/');
  for (const name of ['How it works', 'Documents', 'Accuracy']) {
    await page.getByRole('navigation', { name: 'Sections' }).getByRole('link', { name }).click();
    await expect(page.locator(`section:target`)).toBeInViewport();
  }
});
