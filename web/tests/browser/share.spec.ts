// Share links: one browser copies a link to its projects, and another opens
// it. Each side is its own browser context, so they share no storage.

import { expect, test, type BrowserContext, type Page } from '@playwright/test';
import type { DocumentReading, MasterListRequest } from '../../src/lib/api';
import type { Card } from '../../src/lib/workspace.svelte';

const reading: DocumentReading = {
  document: null,
  title: 'Ungula',
  file_title: 'Ungula',
  parts_found: 40,
  builds: ['Stock', 'Cleft Mod'],
  ocr_builds: [],
  ocr: null,
  ocr_offered: false,
  options: [],
  errors: [],
};

const card = (over: Partial<Card>): Card => ({
  id: crypto.randomUUID(),
  name: 'Project',
  count: 1,
  source: { kind: 'paste', text: 'R1 10k' },
  variant: null,
  useOcr: false,
  reading: null,
  read: { state: 'ready' },
  checks: {},
  ...over,
});

const cards: Card[] = [
  card({ name: 'Fuzz board', count: 3, source: { kind: 'paste', text: 'R1 10k\nC1 100n' } }),
  card({
    name: 'Ungula',
    source: { kind: 'url', url: 'https://docs.pedalpcb.com/project/Ungula.pdf' },
    variant: 'Cleft Mod',
    reading,
    checks: {
      'Cleft Mod': { R4: { state: 'corrected', read: '1k', category: 'Resistors', value: '10k' } },
    },
  }),
  card({ name: 'My scan', source: { kind: 'upload', fileName: 'scan.pdf' } }),
];

/** A context whose API answers like the real one, recording master list requests. */
async function open(context: BrowserContext, sent: MasterListRequest[]): Promise<Page> {
  const page = await context.newPage();
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
  await page.route('**/api/master-list', (route) => {
    const body = route.request().postDataBuffer()?.toString() ?? '';
    const json = /name="request"\r\n\r\n(.*?)\r\n--/s.exec(body)?.[1] ?? '{}';
    sent.push(JSON.parse(json) as MasterListRequest);
    return route.abort('failed');
  });
  return page;
}

test('a share link adds the same projects in another browser', async ({ browser }) => {
  const mine = await browser.newContext();
  await mine.addInitScript((saved) => {
    if (!localStorage.getItem('star-ground.workspace.v1'))
      localStorage.setItem('star-ground.workspace.v1', saved);
    // Keep what's copied, in every engine, without asking for clipboard access.
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: (text: string) => ((window as { copied?: string }).copied = text) },
    });
  }, JSON.stringify({ cards }));
  const page = await open(mine, []);
  await page.goto('/app/');

  await expect(page.getByText("so 1 upload isn't in the link")).toBeVisible();
  await page.getByRole('button', { name: 'Copy a share link' }).click();
  await expect(page.getByRole('button', { name: 'Copied!' })).toBeVisible();
  const link = await page.evaluate(() => (window as { copied?: string }).copied ?? '');
  expect(link).toMatch(/\/app\/#share=[\w-]+$/);

  const theirs = await browser.newContext();
  const sent: MasterListRequest[] = [];
  const other = await open(theirs, sent);
  await other.goto(new URL(link).pathname + new URL(link).hash);

  await expect(other.getByRole('status')).toHaveText('Added 2 projects from a shared link.');
  await expect(other).toHaveURL(/\/app\/$/);
  await expect
    .poll(() => sent.at(-1)?.projects)
    .toEqual([
      expect.objectContaining({ name: 'Fuzz board', count: 3, method: 'paste' }),
      expect.objectContaining({
        name: 'Ungula',
        method: 'url',
        url: 'https://docs.pedalpcb.com/project/Ungula.pdf',
        variant: 'Cleft Mod',
        corrections: [{ ref: 'R4', category: 'Resistors', value: '10k' }],
      }),
    ]);

  await mine.close();
  await theirs.close();
});

test('a damaged share link says so and adds nothing', async ({ page }) => {
  await page.route('**/api/**', (route) => route.abort('failed'));
  await page.goto('/app/#share=not-a-real-link');
  await expect(page.getByRole('alert')).toHaveText(/couldn't be read/);
  await expect(page.getByRole('textbox', { name: 'Project name' })).toHaveCount(0);
});
