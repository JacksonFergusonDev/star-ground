// The (i) markers beside the app's terms: what each opens, and that it opens
// for a pointer, a tap and the keyboard, inside the window.

import { expect, test, type Page } from '@playwright/test';
import type { MasterListResult } from '../../src/lib/api';

const result: MasterListResult = {
  projects: [
    {
      label: 'Fuzz',
      name: 'Fuzz',
      reviews: [],
      ocr_reads: [],
      options: [],
      errors: [],
      parts: [],
      counted: [],
      build_order: [],
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
      notes: '',
      search_term: '10k resistor',
      supplier_url: '',
    },
  ],
  stats: { lines_read: 1, parts_found: 1 },
  ignored_lines: [],
  has_stock: false,
};

async function openList(page: Page): Promise<void> {
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }));
  await page.route('**/api/master-list', (route) => route.fulfill({ json: result }));
  await page.goto('/app/');
  await page.getByRole('button', { name: 'Paste' }).click();
  await page.getByRole('textbox', { name: 'Parts list' }).fill('R1 10k');
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await page.getByRole('link', { name: /^To buy/ }).click();
}

/** Whether a box lies wholly inside the window. */
async function inWindow(page: Page, name: string): Promise<boolean> {
  const box = await page.getByRole('note').filter({ hasText: name }).boundingBox();
  const size = page.viewportSize();
  return (
    !!box &&
    !!size &&
    box.x >= 0 &&
    box.y >= 0 &&
    box.x + box.width <= size.width &&
    box.y + box.height <= size.height
  );
}

test('a tap or click opens what a term means, and Escape closes it', async ({ page }) => {
  await openList(page);
  const marker = page.getByRole('button', { name: 'What "Hardware kit" means' });
  const note = page
    .getByRole('note')
    .filter({ hasText: 'the enclosure, the input and output jacks' });
  await expect(note).toBeHidden();

  await marker.click();
  await expect(marker).toHaveAttribute('aria-expanded', 'true');
  await expect(note).toBeVisible();
  expect(await inWindow(page, 'the enclosure')).toBe(true);
  // Tapping the term doesn't tick its checkbox.
  await expect(page.getByRole('checkbox', { name: 'Hardware kit' })).toBeChecked();

  await page.keyboard.press('Escape');
  await expect(note).toBeHidden();
  await expect(marker).toBeFocused();

  // A click anywhere else closes it too.
  await marker.click();
  await expect(note).toBeVisible();
  await page.getByRole('heading', { name: 'Shopping list', level: 1 }).click();
  await expect(note).toBeHidden();
});

test('a pointer opens the explanation by hovering', async ({ page, isMobile }) => {
  test.skip(isMobile, 'Phones have no hover.');
  await openList(page);
  const marker = page.getByRole('button', { name: 'What "Buy" means' });
  await marker.hover();
  await expect(page.getByRole('note').filter({ hasText: 'come with spares' })).toBeVisible();
  await page.getByRole('heading', { name: 'Shopping list', level: 1 }).hover();
  await expect(page.getByRole('note').filter({ hasText: 'come with spares' })).toBeHidden();
});

test('the keyboard opens the explanation', async ({ page }) => {
  await openList(page);
  await page.getByRole('button', { name: 'What "Values" means' }).focus();
  await page.keyboard.press('Enter');
  await expect(
    page.getByRole('note').filter({ hasText: '4.7k and 4k7 are the same value' }),
  ).toBeVisible();
});
