import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';

// A link to another site opens in a new tab, so a builder never loses their
// place in the app (or the projects it holds in this browser) by following one.
// house-style's arrow-up-right marks such a link, so every link that shows it
// opens a new tab too. The site shell's own header and footer (house-style's
// SiteBar and SiteFooter) are left out: their links are house-style's to change.

const router = readFileSync(new URL('../../src/lib/router.svelte.ts', import.meta.url), 'utf8');
// The check and build pages need a project; review.spec.ts covers them.
const appPaths = [...router.matchAll(/path: '(\/app\/[^']*)'/g)]
  .map((m) => m[1])
  .filter((p) => p !== '/app/check/' && p !== '/app/build/');
const pages = [
  '/',
  '/catalog/',
  '/catalog/pedalpcb/theclap/',
  '/catalog/tonepad/tonepad-bigmuff/',
  ...appPaths,
];

// The arrow's path, read from house-style's icon so the test follows it.
const arrow = readFileSync(
  new URL('../../node_modules/house-style/icons/arrow-up-right.svg', import.meta.url),
  'utf8',
).match(/ d="([^"]+)"/)?.[1];

test('finds the arrow icon and the app pages', () => {
  expect(arrow).toBeTruthy();
  expect(appPaths).toContain('/app/troubleshoot/');
});

for (const path of pages) {
  test(`${path} opens outside links in a new tab`, async ({ page }) => {
    await page.goto(path);
    await expect(page.locator('main').first()).toBeVisible();
    const wrong = await page.locator('a[href]').evaluateAll(
      (links, d) =>
        links
          .filter((a) => !a.closest('.hs-bar, .hs-footer'))
          .filter((a) => {
            const url = new URL((a as HTMLAnchorElement).href);
            const outside = /^https?:$/.test(url.protocol) && url.origin !== location.origin;
            const marked = [...a.querySelectorAll('svg path')].some(
              (p) => p.getAttribute('d') === d,
            );
            return outside || marked;
          })
          .filter(
            (a) =>
              a.getAttribute('target') !== '_blank' ||
              !/\bnoopener\b/.test(a.getAttribute('rel') ?? ''),
          )
          .map((a) => a.outerHTML.slice(0, 120)),
      arrow,
    );
    expect(
      wrong,
      'links to another site, or with the arrow, need target="_blank" and noopener',
    ).toEqual([]);
  });
}
