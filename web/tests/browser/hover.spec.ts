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
