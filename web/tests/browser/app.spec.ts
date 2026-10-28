// The app page against a stand-in API: the browser tests serve the static
// site alone, so each test answers the API's routes itself.

import { expect, test } from '@playwright/test';
import type { MasterListRequest, MasterListResult } from '../../src/lib/api';

const result: MasterListResult = {
  projects: [
    {
      label: 'Pasted list',
      name: 'Pasted list',
      reviews: [
        {
          ref: 'C1',
          warnings: [{ kind: 'Unparsed value', ref: 'C1', message: 'Check its value.' }],
        },
      ],
      ocr_reads: [],
      options: [],
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
      notes: 'Use 1/4W Metal Film (1%)',
      search_term: '10k resistor',
      supplier_url: 'https://www.taydaelectronics.com/',
    },
  ],
  stats: { lines_read: 2, parts_found: 2, errors: [] },
  ignored_lines: [],
  has_stock: false,
};

test.beforeEach(async ({ page }) => {
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }));
});

test('pastes a parts list and shows its shopping list', async ({ page }) => {
  let sent: MasterListRequest | undefined;
  await page.route('**/api/master-list', (route) => {
    sent = JSON.parse(
      route
        .request()
        .postDataBuffer()
        ?.toString()
        .match(/\{.*\}/s)?.[0] ?? '{}',
    );
    return route.fulfill({ json: result });
  });
  await page.goto('/app/');
  await page.getByRole('textbox', { name: 'Parts list' }).fill('R1 10k\nC1 100x');
  await page.getByRole('button', { name: 'Make the shopping list' }).click();

  const row = page.getByRole('row', { name: /10k/ });
  await expect(row).toContainText('Resistors');
  await expect(row).toContainText('10');
  await expect(page.getByText('Check its value.')).toBeVisible();
  expect(sent?.projects[0]).toMatchObject({ method: 'paste', text: 'R1 10k\nC1 100x' });
});

test("shows the API's message when it refuses", async ({ page }) => {
  await page.route('**/api/master-list', (route) =>
    route.fulfill({ status: 429, json: { message: 'Too many requests. Try again in a minute.' } }),
  );
  await page.goto('/app/');
  await page.getByRole('textbox', { name: 'Parts list' }).fill('R1 10k');
  await page.getByRole('button', { name: 'Make the shopping list' }).click();

  await expect(page.getByRole('alert')).toHaveText('Too many requests. Try again in a minute.');
  await expect(page.getByRole('table')).toHaveCount(0);
});
