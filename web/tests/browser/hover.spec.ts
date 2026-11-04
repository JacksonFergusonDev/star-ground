// Every page of the app answers the pointer the same way (see hover.ts and
// the rules in src/styles/global.css). The check page is covered in
// review.spec.ts, which has the document it needs.

import { expect, test } from '@playwright/test';
import type { MasterListResult, PresetCatalog } from '../../src/lib/api';
import { hoverProblems } from './hover';

// Hover is the pointer's, and the fades it checks are what reduced motion keeps.
test.use({ reducedMotion: 'no-preference' });
test.skip(
  ({ browserName, isMobile }) => browserName !== 'chromium' || isMobile,
  'Hovering is a desktop pointer; one engine checks the rules.',
);

const catalog: PresetCatalog = {
  sources: ['PedalPCB'],
  categories: { PedalPCB: ['Fuzz'] },
  presets: [{ key: 'fuzz', source: 'PedalPCB', category: 'Fuzz', name: 'Sunflower Fuzz' }],
};

const result: MasterListResult = {
  projects: [
    {
      label: 'Pasted list',
      name: 'Pasted list',
      reviews: [],
      ocr_reads: [],
      options: [],
      errors: [],
      parts: [
        { ref: 'R1', category: 'Resistors', value: '10k', location: null },
        { ref: 'VOLUME', category: 'Potentiometers', value: 'A100k', location: null },
      ],
      counted: [],
      build_order: [
        { stage: 'Resistors', refs: ['R1'], counted: [] },
        { stage: 'Potentiometers', refs: ['VOLUME'], counted: [] },
      ],
    },
  ],
  shopping_list: [
    {
      origin: 'Circuit Board',
      category: 'Resistors',
      part: '10k',
      bom_qty: 1,
      included: 0,
      in_stock: null,
      net_need: null,
      buy_qty: 10,
      notes: 'Use 1/4W metal film (1%). A long note folds to one line until it is opened.',
      search_term: '10k resistor',
      supplier_url: 'https://www.taydaelectronics.com/',
    },
    // Enough rows to scroll, and a pot whose taper has a screen-reader label.
    ...Array.from({ length: 40 }, (_, i) => ({
      origin: 'Circuit Board' as const,
      category: 'Potentiometers',
      part: `A${(i + 1) * 10}k`,
      bom_qty: 1,
      included: 0,
      in_stock: null,
      net_need: null,
      buy_qty: 1,
      notes: '',
      search_term: 'pot',
      supplier_url: 'https://www.taydaelectronics.com/',
    })),
  ],
  stats: { lines_read: 2, parts_found: 2 },
  ignored_lines: [],
  has_stock: false,
};

test.beforeEach(async ({ page }) => {
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }));
  await page.route('**/api/presets', (route) => route.fulfill({ json: catalog }));
  await page.route('**/api/master-list', (route) => route.fulfill({ json: result }));
});

test('only the page scrolls, never the window, so the sidebar stays put', async ({ page }) => {
  await page.goto('/app/');
  await page.getByRole('button', { name: 'Paste' }).click();
  await page.getByRole('textbox', { name: 'Parts list' }).fill('R1 10k');
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await page.getByRole('link', { name: /^To buy/ }).click();
  await expect(page.getByRole('row', { name: /A400k/ })).toBeAttached();

  // A wheel over the sidebar moves nothing; over the page, the page's column.
  const sidebar = page.getByRole('navigation', { name: 'App' });
  await sidebar.hover();
  await page.mouse.wheel(0, 2000);
  await page.getByRole('heading', { level: 1 }).hover();
  await page.mouse.wheel(0, 2000);
  await expect
    .poll(() => page.evaluate(() => document.querySelector('[data-app-scroll]')?.scrollTop))
    .toBeGreaterThan(0);
  const windowScroll = await page.evaluate(() => ({
    y: window.scrollY,
    extra: document.documentElement.scrollHeight - document.documentElement.clientHeight,
  }));
  expect(windowScroll).toEqual({ y: 0, extra: 0 });
  await expect(sidebar).toBeInViewport();
});

test('a field lights up under the pointer, not under its label', async ({ page }) => {
  await page.goto('/app/');
  const field = page.getByLabel('Build documents (PDF)');
  const border = () =>
    field.evaluate((el) => {
      document.getAnimations().forEach((a) => a.finish());
      const style = getComputedStyle(el);
      const button = getComputedStyle(el, '::file-selector-button');
      return [style.borderTopColor, button.borderTopColor];
    });
  const rest = await border();
  await page.getByText('Build documents (PDF)').hover();
  await page.waitForTimeout(300); // longer than a fade, so one would show
  expect(await border()).toEqual(rest);
  // The field and its button change together, with the same fade. The
  // button's fade isn't among the document's animations, so it's waited out.
  await field.hover();
  await expect
    .poll(async () => {
      const [box, button] = await border();
      return box !== rest[0] && button === box;
    })
    .toBe(true);
  const fades = await field.evaluate((el) =>
    [getComputedStyle(el), getComputedStyle(el, '::file-selector-button')].map(
      (s) => s.transitionDuration,
    ),
  );
  expect(fades).toEqual(['0.2s', '0.2s']);
});

test('every control in the app reacts to the pointer with a fade', async ({ page }) => {
  await page.goto('/app/');
  // Before any project: the empty workspace and the ways to add one.
  expect(await hoverProblems(page)).toEqual([]);

  await page.getByRole('button', { name: 'Paste' }).click();
  await page.getByRole('textbox', { name: 'Parts list' }).fill('R1 10k');
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(page.getByRole('link', { name: /^To buy/ })).toContainText('1');
  await page.getByText('Paint', { exact: true }).click();
  expect(await hoverProblems(page)).toEqual([]);

  for (const path of ['/app/list/', '/app/stock/', '/app/export/', '/catalog/']) {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    expect(await hoverProblems(page), path).toEqual([]);
  }

  await page.goto('/app/');
  await page.getByRole('link', { name: 'Build it, part by part' }).click();
  await expect(page.getByText('0 of 2 parts placed.')).toBeVisible();
  expect(await hoverProblems(page)).toEqual([]);
});
