// The workspace against a stand-in API: the browser tests serve the static
// site alone, so each test answers the API's routes itself.

import { expect, test, type Page, type Route } from '@playwright/test';
import type {
  DocumentReading,
  MasterListRequest,
  MasterListResult,
  PresetCatalog,
} from '../../src/lib/api';

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

const reading = (over: Partial<DocumentReading> = {}): DocumentReading => ({
  document: 'file:Ungula.pdf:0123456789abcdef',
  title: 'Ungula',
  file_title: 'Ungula',
  parts_found: 40,
  builds: [],
  ocr_builds: [],
  ocr: null,
  ocr_offered: false,
  options: [],
  errors: [],
  ...over,
});

const sse = (...events: object[]): string =>
  events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');

/** The `request` field of a multipart master list body. */
function requestOf(body: string): MasterListRequest {
  const json = /name="request"\r\n\r\n(.*?)\r\n--/s.exec(body)?.[1] ?? '{}';
  return JSON.parse(json) as MasterListRequest;
}

/** The requests the page sent to the master list, parsed. */
async function stubMasterList(page: Page): Promise<MasterListRequest[]> {
  const sent: MasterListRequest[] = [];
  await page.route('**/api/master-list', (route) => {
    const body = route.request().postDataBuffer()?.toString() ?? '';
    sent.push(requestOf(body));
    return route.fulfill({ json: result });
  });
  return sent;
}

async function paste(page: Page, text: string): Promise<void> {
  await page.getByRole('button', { name: 'Paste' }).click();
  await page.getByRole('textbox', { name: 'Parts list' }).fill(text);
  await page.getByRole('button', { name: 'Add', exact: true }).click();
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }));
  await page.route('**/api/presets', (route) => route.fulfill({ json: catalog }));
});

test('a pasted list appears on the master list without a Generate button', async ({ page }) => {
  const sent = await stubMasterList(page);
  await page.goto('/app/');
  await paste(page, 'R1 10k\nC1 100x');

  const row = page.getByRole('row', { name: /10k/ });
  await expect(row).toContainText('Resistors');
  await expect(row.getByRole('link', { name: 'Tayda' })).toBeVisible();
  await expect(page.getByText('Check its value.')).toBeVisible();
  expect(sent.at(-1)?.projects[0]).toMatchObject({ method: 'paste', text: 'R1 10k\nC1 100x' });
});

test('changing a count updates the master list', async ({ page }) => {
  const sent = await stubMasterList(page);
  await page.goto('/app/');
  await paste(page, 'R1 10k');
  await expect(page.getByRole('row', { name: /10k/ })).toBeVisible();

  await page.getByRole('spinbutton', { name: 'Pedals' }).fill('3');
  await page.getByRole('spinbutton', { name: 'Pedals' }).blur();

  await expect.poll(() => sent.at(-1)?.projects[0]?.count).toBe(3);
});

test('a preset is added by name', async ({ page }) => {
  const sent = await stubMasterList(page);
  await page.goto('/app/');
  await page.getByRole('button', { name: 'Preset' }).click();
  await page.getByRole('combobox', { name: 'Preset' }).selectOption('fuzz');
  await page.getByRole('button', { name: 'Add', exact: true }).click();

  await expect(page.getByRole('textbox', { name: 'Project name' })).toHaveValue('Sunflower Fuzz');
  await expect
    .poll(() => sent.at(-1)?.projects[0])
    .toMatchObject({ method: 'preset', preset: 'fuzz' });
});

test('the workspace survives a reload', async ({ page }) => {
  await stubMasterList(page);
  await page.goto('/app/');
  await paste(page, 'R1 10k');
  await page.getByRole('textbox', { name: 'Project name' }).fill('Bench fuzz');
  await expect(page.getByRole('row', { name: /10k/ })).toBeVisible();

  await page.reload();

  await expect(page.getByRole('textbox', { name: 'Project name' })).toHaveValue('Bench fuzz');
  await expect(page.getByRole('row', { name: /10k/ })).toBeVisible();
});

test('a removed project leaves the master list', async ({ page }) => {
  await stubMasterList(page);
  await page.goto('/app/');
  await paste(page, 'R1 10k');
  await expect(page.getByRole('row', { name: /10k/ })).toBeVisible();

  await page.getByRole('button', { name: /^Remove/ }).click();

  await expect(page.getByRole('table')).toHaveCount(0);
});

async function upload(page: Page, name: string): Promise<void> {
  await page.getByRole('button', { name: 'Upload' }).click();
  await page.getByLabel('Build documents (PDF)').setInputFiles({
    name,
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF-1.4 stand-in'),
  });
  await page.getByRole('button', { name: 'Add', exact: true }).click();
}

test('a multi-build document waits for its build, then joins by reference', async ({ page }) => {
  const sent = await stubMasterList(page);
  await page.route('**/api/documents/read', (route) =>
    route.fulfill({
      contentType: 'text/event-stream',
      body: sse(
        { event: 'reading' },
        { event: 'result', reading: reading({ builds: ['Stock', 'Cleft Mod'] }) },
      ),
    }),
  );
  await page.goto('/app/');
  await upload(page, 'Ungula.pdf');

  await expect(page.getByText('Choose a build to add it.')).toBeVisible();
  expect(sent).toHaveLength(0);
  await page.getByRole('combobox', { name: 'Build' }).selectOption('Cleft Mod');

  await expect
    .poll(() => sent.at(-1)?.projects[0])
    .toMatchObject({
      method: 'upload',
      document: 'file:Ungula.pdf:0123456789abcdef',
      variant: 'Cleft Mod',
    });
});

test('an image parts list is read by OCR only when accepted', async ({ page }) => {
  const sent = await stubMasterList(page);
  const reads: string[] = [];
  await page.route('**/api/documents/read', async (route: Route) => {
    const ocr = /name="ocr"/.test(route.request().postDataBuffer()?.toString() ?? '');
    reads.push(ocr ? 'ocr' : 'text');
    const ocrReading = {
      verdict: 'Complete',
      message: 'OCR read 25 parts from it.',
      parts_read: 25,
      suspect: 0,
      pages: [1],
      has_parts: true,
    };
    await route.fulfill({
      contentType: 'text/event-stream',
      body: sse({
        event: 'result',
        reading: ocr
          ? reading({ ocr: ocrReading as DocumentReading['ocr'] })
          : reading({ ocr_offered: true }),
      }),
    });
  });
  await page.goto('/app/');
  await upload(page, 'Image.pdf');

  await expect(page.getByText('This parts list is an image.')).toBeVisible();
  expect(reads).toEqual(['text']);
  await page.getByRole('button', { name: 'Read it by OCR' }).click();

  await expect(page.getByRole('checkbox', { name: 'Add the 25 parts OCR read' })).toBeChecked();
  expect(reads).toEqual(['text', 'ocr']);
  await expect.poll(() => sent.at(-1)?.projects[0]?.use_ocr).toBe(true);
});

test('an upload the server no longer holds is sent again as a file', async ({ page }) => {
  const bodies: string[] = [];
  await page.route('**/api/documents/read', (route) =>
    route.fulfill({
      contentType: 'text/event-stream',
      body: sse({ event: 'result', reading: reading() }),
    }),
  );
  await page.route('**/api/master-list', (route) => {
    const body = route.request().postDataBuffer()?.toString() ?? '';
    bodies.push(body);
    return bodies.length === 1
      ? route.fulfill({
          status: 409,
          json: { message: 'Send them as files.', documents: ['file:Ungula.pdf:0123456789abcdef'] },
        })
      : route.fulfill({ json: result });
  });
  await page.goto('/app/');
  await upload(page, 'Ungula.pdf');

  await expect(page.getByRole('row', { name: /10k/ })).toBeVisible();
  expect(bodies[0]).not.toContain('filename=');
  expect(bodies[1]).toContain('filename="Ungula.pdf"');
});

test('says the server is busy when a request fails after the API answered', async ({ page }) => {
  await page.route('**/api/master-list', (route) => route.abort('failed'));
  const warm = page.waitForResponse('**/api/health');
  await page.goto('/app/');
  await warm;
  await paste(page, 'R1 10k');

  await expect(page.getByRole('alert')).toHaveText(
    'Star Ground is busy right now. Try again in a minute.',
  );
});

test("says the server can't be reached when it never answered", async ({ page }) => {
  await page.unroute('**/api/health');
  await page.route('**/api/health', (route) => route.abort('failed'));
  await page.route('**/api/master-list', (route) => route.abort('failed'));
  await page.goto('/app/');
  await paste(page, 'R1 10k');

  await expect(page.getByRole('alert')).toContainText("Couldn't reach Star Ground's server");
});

test('says the server is busy when the platform refuses without a message', async ({ page }) => {
  await page.route('**/api/master-list', (route) =>
    route.fulfill({
      status: 503,
      contentType: 'text/html',
      body: '<html>Service Unavailable</html>',
    }),
  );
  await page.goto('/app/');
  await paste(page, 'R1 10k');

  await expect(page.getByRole('alert')).toHaveText(
    'Star Ground is busy right now. Try again in a minute.',
  );
});

test('a stock CSV goes with each request and adds stock columns', async ({ page }) => {
  const bodies: string[] = [];
  await page.route('**/api/master-list', (route) => {
    const body = route.request().postDataBuffer()?.toString() ?? '';
    bodies.push(body);
    const stocked = body.includes('name="stock"');
    return route.fulfill({
      json: stocked
        ? {
            ...result,
            has_stock: true,
            shopping_list: result.shopping_list.map((r) => ({
              ...r,
              in_stock: 40,
              net_need: 0,
              buy_qty: 0,
            })),
          }
        : result,
    });
  });
  await page.goto('/app/');
  await paste(page, 'R1 10k');
  await page.getByLabel('Stock CSV').setInputFiles({
    name: 'stock.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('Category,Part,Qty\nResistors,10k,40\n'),
  });

  await expect(page.getByRole('columnheader', { name: 'In stock' })).toBeVisible();
  await expect(page.getByText('0 to buy after your stock')).toBeVisible();
  expect(bodies.at(-1)).toContain('filename="stock.csv"');

  await page.reload();
  await expect(page.getByText('Subtracting stock.csv from what to buy.')).toBeVisible();
});

test('the list options go with the request', async ({ page }) => {
  const sent = await stubMasterList(page);
  await page.goto('/app/');
  await paste(page, 'R1 10k');
  await expect(page.getByRole('row', { name: /10k/ })).toBeVisible();

  await page.getByRole('checkbox', { name: 'Hardware kit' }).uncheck();

  await expect.poll(() => sent.at(-1)?.show_hardware).toBe(false);
  expect(sent.at(-1)?.show_extras).toBe(true);
});

test('a download saves the file the API exports', async ({ page }) => {
  await stubMasterList(page);
  let url = '';
  await page.route('**/api/exports/**', (route) => {
    url = route.request().url();
    return route.fulfill({
      body: 'Part,Qty\n10k,10\n',
      contentType: 'text/csv',
      headers: { 'content-disposition': 'attachment; filename="shopping-list.csv"' },
    });
  });
  await page.goto('/app/');
  await paste(page, 'R1 10k');
  await expect(page.getByRole('row', { name: /10k/ })).toBeVisible();

  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Shopping list', exact: true }).click();

  expect((await download).suggestedFilename()).toBe('shopping-list.csv');
  expect(url).toContain('/api/exports/shopping-list?links=excel');
});
