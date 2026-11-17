// Making a stock without a CSV: counting parts into one, ticking off what an
// order needs, and identifying a part to count. Each answers the API's routes
// itself, as app.spec.ts does.

import { expect, test, type Page } from '@playwright/test';
import type { MasterListResult, ShoppingRow } from '../../src/lib/api';

const row = (category: string, part: string, bom_qty: number): ShoppingRow => ({
  origin: 'Circuit Board',
  category,
  part,
  bom_qty,
  included: 0,
  in_stock: null,
  net_need: null,
  buy_qty: bom_qty,
  notes: '',
  search_term: part,
  supplier_url: '',
});

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
    row('Resistors', '4.7k', 3),
    row('ICs', 'TL072', 1),
    row('Potentiometers', 'B100K', 2),
  ],
  stats: { lines_read: 3, parts_found: 6 },
  ignored_lines: [],
  has_stock: false,
};

/**
 * Answers the API, and counts the master list requests that carried a stock
 * file. (WebKit leaves a file's contents out of the request it shows, so the
 * stock itself is read from the workspace, as `saved` does.)
 */
async function stubMasterList(page: Page): Promise<{ stocked: number }> {
  const sent = { stocked: 0 };
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }));
  await page.route('**/api/master-list', (route) => {
    if (route.request().postDataBuffer()?.toString().includes('name="stock"')) sent.stocked += 1;
    return route.fulfill({ json: result });
  });
  return sent;
}

/** The stock CSV the workspace holds, which each master list request sends; '' for none. */
const saved = (page: Page): Promise<string> =>
  page.evaluate(() => {
    const workspace = JSON.parse(localStorage.getItem('star-ground.workspace.v1') ?? '{}') as {
      stock?: { text: string } | null;
    };
    return workspace.stock?.text ?? '';
  });

async function addPastedProject(page: Page): Promise<void> {
  await page.goto('/app/');
  await page.getByRole('button', { name: 'Paste' }).click();
  await page.getByRole('textbox', { name: 'Parts list' }).fill('R1 4k7');
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(page.getByRole('link', { name: /^To buy/ })).toContainText('3');
}

test('counting parts makes a stock the shopping list subtracts, and downloads it', async ({
  page,
}) => {
  const sent = await stubMasterList(page);
  await addPastedProject(page);
  await page.goto('/app/stock/');
  await page.getByRole('link', { name: 'Count your parts' }).first().click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Count your parts');

  await page.getByLabel('Resistors: 4.7k').fill('25');
  await page.getByLabel('Resistors: 4.7k').blur();
  await page.getByLabel('Chips: TL072').fill('1');
  await page.getByLabel('Chips: TL072').blur();
  await page.getByLabel('Kind').selectOption('Switches');
  await page.getByLabel('Part', { exact: true }).fill('3PDT footswitch');
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText(/3 different parts, 27 in all/);

  const csv = 'Category,Part,Qty\nResistors,4.7k,25\nICs,TL072,1\nSwitches,3PDT footswitch,1\n';
  await expect.poll(() => saved(page)).toBe(csv);
  await expect.poll(() => sent.stocked).toBeGreaterThan(0);

  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download the stock CSV' }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe('stock.csv');
  const stream = await file.createReadStream();
  let text = '';
  for await (const chunk of stream) text += String(chunk);
  expect(text).toBe(csv);

  // The stock page saves the same file, so counts typed in this browser can be kept.
  await page.goto('/app/stock/');
  const saving = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download your stock CSV' }).click();
  expect((await saving).suggestedFilename()).toBe('stock.csv');
  await page.goto('/app/stock/count/');

  // A count set to nothing takes the part out of the stock.
  await page.getByRole('button', { name: 'Remove 3PDT footswitch' }).click();
  await page.getByLabel('Chips: TL072').fill('');
  await page.getByLabel('Chips: TL072').blur();
  await expect.poll(() => saved(page)).toBe('Category,Part,Qty\nResistors,4.7k,25\n');
});

test('a counted value matches the drawer however it was written', async ({ page }) => {
  await stubMasterList(page);
  await page.goto('/app/stock/');
  await page.getByLabel('Stock CSV', { exact: true }).setInputFiles({
    name: 'mine.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('Category,Part,Qty\nResistors,4K7,10\nResistors,4700,2\nICs,tl072,3\n'),
  });
  await page.goto('/app/stock/count/');
  await expect(page.getByLabel('Resistors: 4.7k')).toHaveValue('12');
  await expect(page.getByLabel('Chips: TL072')).toHaveValue('3');
  // Changing the count replaces both rows it means, keeping the file's name.
  await page.getByLabel('Resistors: 4.7k').fill('5');
  await page.getByLabel('Resistors: 4.7k').blur();
  await page.goto('/app/stock/');
  await expect(page.getByText('Subtracting mine.csv from what to buy.')).toBeVisible();
  await page.goto('/app/stock/count/');
  await expect(page.getByRole('status')).toHaveText(/2 different parts, 8 in all/);
});

test("ticking off what you have of an order's parts writes only those to the stock", async ({
  page,
}) => {
  await stubMasterList(page);
  await addPastedProject(page);
  await page.goto('/app/list/');
  await page.getByRole('link', { name: 'Tick off what you have' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('What you have');

  const part = (name: string) => page.getByRole('group', { name: new RegExp(`How many ${name}`) });
  await part('TL072').getByText('All 1').click();
  await part('4.7k').getByText('Some').click();
  await page.getByLabel('How many 4.7k you have').fill('2');
  await page.getByLabel('How many 4.7k you have').blur();
  await expect.poll(() => saved(page)).toBe('Category,Part,Qty\nICs,TL072,1\nResistors,4.7k,2\n');

  // A part needed once has no 'some'.
  await expect(part('TL072').getByText('Some')).toHaveCount(0);
  await part('TL072').getByText('None').click();
  await expect.poll(() => saved(page)).toBe('Category,Part,Qty\nResistors,4.7k,2\n');
});

test('the identifier reads bands and capacitor codes, and adds what it read', async ({ page }) => {
  await stubMasterList(page);
  await addPastedProject(page);
  await page.goto('/app/stock/identify/');

  const resistors = page.getByRole('region', { name: 'Resistors' });
  // Each colour is a cell of the code table, picked by its label.
  const pick = (name: string) => resistors.locator('label', { has: page.getByLabel(name) }).click();
  await resistors.getByRole('radio', { name: '4', exact: true }).check();
  await pick('1st digit: Yellow,');
  await pick('2nd digit: Violet,');
  await pick('Multiplier: Red,');
  await pick('Tolerance: Gold,');
  // The reading is announced as it changes.
  await expect(resistors.locator('[aria-live]')).toHaveText(/4\.7k Ω\s*±5%/);
  await resistors.getByLabel('How many').fill('10');
  await resistors.getByRole('button', { name: 'Add 4.7k to your stock' }).click();
  await expect(page.getByRole('status')).toHaveText('Added 10 × 4.7k to your stock.');

  const capacitors = page.getByRole('region', { name: 'Capacitors' });
  await capacitors.getByLabel("What's printed on it").fill('2A473J');
  await expect(capacitors.getByText('47,000 pF = 47 nF = 0.047 µF')).toBeVisible();
  await expect(capacitors.getByText(/Tolerance ±5%\. Rated 100 V\./)).toBeVisible();
  await capacitors.getByRole('button', { name: 'Add 47n to your stock' }).click();

  await expect
    .poll(() => saved(page))
    .toBe('Category,Part,Qty\nResistors,4.7k,10\nCapacitors,47n,1\n');
  // Beside each add button, a way to see what was added.
  await capacitors.getByRole('link', { name: 'See your stock' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Stock');
});
