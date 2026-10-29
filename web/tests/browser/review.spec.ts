// Checking a project against its document, against a stand-in API and a PDF
// made here: two parts-list rows at known places on one page.

import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import type {
  DocumentReading,
  MasterListRequest,
  MasterListResult,
  PartReportIn,
  ProjectResult,
} from '../../src/lib/api';

const PAGE_HEIGHT = 792;

/** A one-page PDF printing each line's text at its place, in points from the top-left. */
function pdf(lines: { text: string; x: number; top: number }[]): Buffer {
  const content = lines
    .map((l) => `BT /F1 10 Tf ${l.x} ${PAGE_HEIGHT - l.top - 10} Td (${l.text}) Tj ET`)
    .join('\n');
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 ${PAGE_HEIGHT}] /Contents 4 0 R ` +
      '/Resources << /Font << /F1 5 0 R >> >> >>',
    `<< /Length ${content.length} >>\nstream\n${content}\nendstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ];
  let out = '%PDF-1.4\n';
  const offsets: number[] = [];
  objects.forEach((object, i) => {
    offsets.push(out.length);
    out += `${i + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = out.length;
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  out += offsets.map((o) => `${String(o).padStart(10, '0')} 00000 n \n`).join('');
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, 'latin1');
}

const DOCUMENT = pdf([
  { text: 'R1   1k', x: 72, top: 100 },
  { text: 'R2   100k', x: 72, top: 400 },
]);
const URL_ = 'https://docs.pedalpcb.com/project/Fuzz.pdf';

const project = (over: Partial<ProjectResult> = {}): ProjectResult => ({
  label: 'Fuzz',
  name: 'Fuzz',
  reviews: [
    {
      ref: 'R2',
      warnings: [{ kind: 'Unparsed value', ref: 'R2', message: 'Check its value.' }],
    },
  ],
  ocr_reads: [],
  options: [],
  errors: [],
  parts: [
    {
      ref: 'R1',
      category: 'Resistors',
      value: '1k',
      location: { page: 0, x0: 72, top: 100, x1: 110, bottom: 110 },
    },
    {
      ref: 'R2',
      category: 'Resistors',
      value: '100k',
      location: { page: 0, x0: 72, top: 400, x1: 120, bottom: 410 },
    },
  ],
  ...over,
});

const listOf = (p: ProjectResult): MasterListResult => ({
  projects: [p],
  shopping_list: [],
  stats: { lines_read: 2, parts_found: 2 },
  ignored_lines: [],
  has_stock: false,
});

const reading: DocumentReading = {
  document: 'file:Fuzz.pdf:0123456789abcdef',
  title: 'Fuzz',
  file_title: 'Fuzz',
  parts_found: 2,
  builds: [],
  ocr_builds: [],
  ocr: null,
  ocr_offered: false,
  options: [],
  errors: [],
};

function requestOf(body: string): MasterListRequest {
  const json = /name="request"\r\n\r\n(.*?)\r\n--/s.exec(body)?.[1] ?? '{}';
  return JSON.parse(json) as MasterListRequest;
}

/** Answers the master list with the project, applying the request's corrections. */
async function stubMasterList(page: Page): Promise<MasterListRequest[]> {
  const sent: MasterListRequest[] = [];
  await page.route('**/api/master-list', (route) => {
    const request = requestOf(route.request().postDataBuffer()?.toString() ?? '');
    sent.push(request);
    const corrections = request.projects[0]?.corrections ?? [];
    const base = project();
    const parts = base.parts.map((p) => {
      const c = corrections.find((x) => x.ref === p.ref);
      return c ? { ...p, value: c.value } : p;
    });
    return route.fulfill({ json: listOf({ ...base, parts }) });
  });
  return sent;
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }));
  await page.route('**/api/presets', (route) =>
    route.fulfill({ json: { sources: [], categories: {}, presets: [] } }),
  );
  await page.route('**/api/documents/read', (route) =>
    route.fulfill({
      contentType: 'text/event-stream',
      body: `data: ${JSON.stringify({ event: 'result', reading })}\n\n`,
    }),
  );
});

async function upload(page: Page): Promise<void> {
  await page.goto('/app/');
  await page.getByRole('button', { name: 'Upload' }).click();
  await page.getByLabel('Build documents (PDF)').setInputFiles({
    name: 'Fuzz.pdf',
    mimeType: 'application/pdf',
    buffer: DOCUMENT,
  });
  await page.getByRole('button', { name: 'Add', exact: true }).click();
}

const row = (page: Page, ref: string) => page.locator(`li[data-ref="${ref}"]`);

test('a warning shows its part where the linked document prints it', async ({ page }) => {
  await stubMasterList(page);
  await page.route('**/api/documents/pdf?*', (route) => {
    expect(new URL(route.request().url()).searchParams.get('url')).toBe(URL_);
    return route.fulfill({ contentType: 'application/pdf', body: DOCUMENT });
  });
  await page.goto('/app/');
  await page.getByRole('button', { name: 'Link' }).click();
  await page.getByLabel('Link to a build document').fill(URL_);
  await page.getByRole('button', { name: 'Add', exact: true }).click();

  await page.getByRole('button', { name: 'Show in the document' }).click();

  await expect(page.getByRole('heading', { name: 'Check Fuzz' })).toBeVisible();
  await expect(row(page, 'R2').getByRole('button', { name: /R2/ }).first()).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  const box = page.getByRole('button', { name: 'Show R2 in the parts list' });
  await expect(box).toHaveClass(/selected/);
  await expect(box).toBeInViewport();
  // The page is drawn: pdf.js filled the canvas.
  await expect
    .poll(() => page.getByLabel('Page 1').evaluate((c: HTMLCanvasElement) => c.width))
    .toBeGreaterThan(0);

  // Choosing a part on the page chooses it in the list.
  await page.getByRole('button', { name: 'Show R1 in the parts list' }).click();
  await expect(row(page, 'R1').getByRole('button', { name: /R1/ }).first()).toHaveAttribute(
    'aria-pressed',
    'true',
  );

  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});

test('a correction changes the master list and is reported', async ({ page }) => {
  const sent = await stubMasterList(page);
  await page.route('**/api/parts/read', (route) =>
    route.fulfill({ json: { category: 'Resistors', value: '10k' } }),
  );
  const reports: PartReportIn[] = [];
  await page.route('**/api/part-reports', (route) => {
    reports.push(route.request().postDataJSON() as PartReportIn);
    return route.fulfill({ status: 204 });
  });
  await upload(page);

  await page.getByRole('button', { name: 'Check the parts against the document' }).click();
  await row(page, 'R1').getByRole('button', { name: 'Wrong?' }).click();
  await page.getByLabel('What the document prints for R1').fill('10K');
  await page.getByLabel('Note (optional)').fill('The 0 is faint');
  await page.getByRole('button', { name: 'Save' }).click();

  await expect(row(page, 'R1')).toContainText('Corrected; Star Ground read 1k.');
  await expect(row(page, 'R1')).toContainText('Report sent.');
  expect(reports).toEqual([
    {
      document: 'Fuzz.pdf (file:Fuzz.pdf:0123456789abcdef)',
      ref: 'R1',
      read: '1k',
      correction: '10K',
      location: { page: 0, x0: 72, top: 100, x1: 110, bottom: 110 },
      comment: 'The 0 is faint',
    },
  ]);
  await expect
    .poll(() => sent.at(-1)?.projects[0]?.corrections)
    .toEqual([{ ref: 'R1', category: 'Resistors', value: '10k' }]);

  // Undoing it puts the reading back.
  await row(page, 'R1').getByRole('button', { name: 'Undo' }).click();
  await expect.poll(() => sent.at(-1)?.projects[0]?.corrections).toEqual([]);
});

test('a part marked right is counted, and the workspace remembers it', async ({ page }) => {
  await stubMasterList(page);
  await upload(page);
  await page.getByRole('button', { name: 'Check the parts against the document' }).click();
  await expect(page.getByText('0 of 2 parts checked; 1 flagged')).toBeVisible();

  await row(page, 'R2').getByRole('button', { name: 'Looks right' }).click();

  await expect(page.getByText('1 of 2 parts checked.')).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'Check the parts against the document' }).click();
  await expect(row(page, 'R2').getByRole('button', { name: 'Checked' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
});

test("a value that isn't a part is explained where it was typed", async ({ page }) => {
  await stubMasterList(page);
  await page.route('**/api/parts/read', (route) =>
    route.fulfill({
      status: 422,
      json: {
        kind: 'unreadable_part',
        message: "Star Ground couldn't read 'zz' as R1's value.",
        hint: 'Type it as the document prints it, for example 10k, 100n or TL072.',
      },
    }),
  );
  await upload(page);
  await page.getByRole('button', { name: 'Check the parts against the document' }).click();
  await row(page, 'R1').getByRole('button', { name: 'Wrong?' }).click();
  await page.getByLabel('What the document prints for R1').fill('zz');
  await page.getByRole('checkbox', { name: /Tell Star Ground/ }).uncheck();
  await page.getByRole('button', { name: 'Save' }).click();

  await expect(row(page, 'R1').getByRole('alert')).toHaveText(
    "Star Ground couldn't read 'zz' as R1's value. Type it as the document prints it, for " +
      'example 10k, 100n or TL072.',
  );
});
