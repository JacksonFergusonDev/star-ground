import { expect, test } from '@playwright/test';

// Other sites fetch this page and show it in a frame, so it must stand alone:
// everything it draws comes in the one file, and nothing runs.
// jacksonferguson.me's frame lays it out at this size, on phones too.
test.use({ viewport: { width: 1280, height: 840 } });

test('the projects snapshot is one file that runs nothing', async ({ page }) => {
  const requests: string[] = [];
  page.on('request', (request) => requests.push(request.url()));

  await page.goto('/embed/projects/');

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Projects');
  // The sidebar lists the projects and their counts, as the app's does once it has loaded.
  const nav = page.getByRole('navigation', { name: 'App' });
  await expect(nav.getByRole('link', { name: 'Aberration Fuzz' })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Shopping list 62' })).toBeVisible();
  await expect(page.getByRole('img', { name: /^Aberration Fuzz, painted orange/ })).toBeVisible();
  await expect(page.getByRole('img', { name: /^Angry Charles, painted yellow/ })).toBeVisible();
  await expect(page.locator('script')).toHaveCount(0);
  expect(requests).toEqual([page.url()]);
  // The logo it carries is drawn, and the fonts are the ones it draws with.
  expect(
    await page.locator('.mark img').evaluate((img: HTMLImageElement) => img.naturalWidth),
  ).toBeGreaterThan(0);
  expect(await page.evaluate(() => document.fonts.check('16px "DM Sans"'))).toBe(true);
  expect(await page.evaluate(() => document.fonts.check('16px "JetBrains Mono"'))).toBe(true);
});
