import { expect, test } from '@playwright/test';

// Other sites fetch this page and show it in a frame, so it must stand alone:
// everything it draws comes in the one file, and nothing runs.
test('the shopping list snapshot is one file that runs nothing', async ({ page }) => {
  const requests: string[] = [];
  page.on('request', (request) => requests.push(request.url()));

  await page.goto('/embed/shopping-list/');

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Shopping list');
  await expect(page.getByRole('row')).toHaveCount(32);
  await expect(page.locator('script')).toHaveCount(0);
  expect(requests).toEqual([page.url()]);
  // The fonts it carries are the ones it draws with.
  expect(await page.evaluate(() => document.fonts.check('16px "DM Sans"'))).toBe(true);
  expect(await page.evaluate(() => document.fonts.check('16px "JetBrains Mono"'))).toBe(true);
});
