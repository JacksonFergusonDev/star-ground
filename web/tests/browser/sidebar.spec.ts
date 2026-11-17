import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';

// Every page of the app, read from the router that builds them, so a new page
// is checked without anyone remembering to list it here.
const router = readFileSync(new URL('../../src/lib/router.svelte.ts', import.meta.url), 'utf8');
const paths = [...router.matchAll(/path: '(\/app\/[^']*)'/g)].map((m) => m[1]);

// A project's own pages need a project, so the sidebar shows them under it;
// review.spec.ts checks they appear there.
const perProject = new Set(['/app/check/', '/app/build/']);

test('finds the app pages', () => {
  expect(paths).toContain('/app/stock/identify/');
});

for (const path of paths.filter((p) => !perProject.has(p))) {
  test(`${path} is in the sidebar, marked as the current page`, async ({ page, isMobile }) => {
    await page.goto(path);
    if (isMobile || (page.viewportSize()?.width ?? 1000) <= 900) {
      await page.getByRole('button', { name: 'Menu' }).click();
    }
    const nav = page.getByRole('navigation', { name: 'App' });
    const link = nav.locator(`a[href="${path}"]`);
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute('aria-current', 'page');
  });
}

test('Stock opens to show its own pages', async ({ page }) => {
  await page.goto('/app/stock/identify/');
  const nav = page.getByRole('navigation', { name: 'App' });
  if ((page.viewportSize()?.width ?? 1000) <= 900) {
    await page.getByRole('button', { name: 'Menu' }).click();
  }
  await expect(nav.getByRole('link', { name: /^Stock/ })).toHaveAttribute('aria-current', 'true');
  await expect(nav.getByRole('link', { name: 'Count your parts' })).toBeVisible();

  await nav.getByRole('link', { name: 'Projects' }).click();
  await expect(nav.getByRole('link', { name: 'Count your parts' })).toHaveCount(0);
});
