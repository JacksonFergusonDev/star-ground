import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.goto('/app/troubleshoot/');
});

test('points beginners to the friendliest forums first', async ({ page }) => {
  const help = page.getByRole('region', { name: 'Ask for help' });
  const forums = help.getByRole('link');
  await expect(forums.first()).toHaveText(/r\/diypedals/);
  await expect(forums.nth(1)).toHaveText(/PedalPCB/);
  await expect(forums).toHaveCount(5);
});

test('opens every outside link in a new tab', async ({ page }) => {
  const outside = page.locator('.trouble a[href^="http"]');
  await expect(outside).toHaveCount(6);
  for (const link of await outside.all()) {
    await expect(link).toHaveAttribute('target', '_blank');
    await expect(link).toHaveAttribute('rel', /noopener/);
  }
  await expect(page.getByRole('link', { name: /audio probe/ })).toHaveAttribute(
    'href',
    /youtube\.com/,
  );
});

test('keeps a bulleted list in one column', async ({ page }) => {
  // The landing page's global .facts grid once split these into columns.
  const items = page.getByRole('region', { name: 'Ask for help' }).locator('ul:not(.links) > li');
  await expect(items).toHaveCount(4);
  const lefts = await items.evaluateAll((all) =>
    all.map((item) => Math.round(item.getBoundingClientRect().left)),
  );
  expect(new Set(lefts).size).toBe(1);
});
