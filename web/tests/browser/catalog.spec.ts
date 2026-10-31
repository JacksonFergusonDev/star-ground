// The circuit catalog's pages, built from the fixed entries in
// tests/fixtures/catalog (CATALOG_DIR): PedalPCB's The Clap, verified, and
// Tonepad's Big Muff, unverified, with three builds.

import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import type { DocumentReading } from '../../src/lib/api';

for (const path of [
  '/catalog/',
  '/catalog/pedalpcb/theclap/',
  '/catalog/tonepad/tonepad-bigmuff/',
]) {
  test(`${path} has no accessibility violations and fits the window`, async ({ page }) => {
    await page.goto(path);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
}

test('the catalog lists every build, with its mark', async ({ page }) => {
  await page.goto('/catalog/');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Circuit catalog.');
  await expect(page.getByText('2 builds, 1 verified')).toBeVisible();
  await expect(page.getByRole('link', { name: /The Clap.*Verified/ })).toBeVisible();
  await expect(page.getByRole('link', { name: /Gran Mango 3\.1416.*Unverified/ })).toBeVisible();
});

test('searching and filtering hide what does not match', async ({ page }) => {
  await page.goto('/catalog/');
  const rows = page.locator('[data-entries] li:visible');

  // Build names are searchable: the Big Muff document is titled 'Gran Mango'.
  await page.getByRole('searchbox', { name: 'Search' }).fill('russian muff');
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText('Gran Mango');
  await expect(page.getByText('1 of 2 builds')).toBeVisible();

  await page.getByRole('searchbox', { name: 'Search' }).fill('');
  await page.getByRole('checkbox', { name: 'Verified only' }).check();
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText('The Clap');

  await page.getByRole('combobox', { name: 'Publisher' }).selectOption('Tonepad');
  await expect(rows).toHaveCount(0);
  await expect(page.getByText('No builds match.')).toBeVisible();
});

test("a build's page shows its parts, its mark and the publisher's links", async ({ page }) => {
  await page.goto('/catalog/pedalpcb/theclap/');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('The Clap');
  await expect(page.getByText('Verified: checked by hand against this document.')).toBeVisible();
  await expect(page.getByRole('rowheader', { name: 'R1', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Buy the PCB from PedalPCB' })).toHaveAttribute(
    'href',
    'https://www.pedalpcb.com/product/pcb157/',
  );

  await page.goto('/catalog/tonepad/tonepad-bigmuff/');
  await expect(page.getByText('Unverified: read automatically')).toBeVisible();
  await expect(page.getByRole('link', { name: "Tonepad's project page" })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Stock Big Muff' })).toBeVisible();
  await expect(page.getByText('This document describes 3 builds.')).toBeVisible();
  // A part counted by value has no designator, so its count stands in.
  await expect(page.getByRole('rowheader', { name: '2 ×', exact: true })).toBeVisible();
});

test('Add to build adds the document to the workspace once', async ({ page }) => {
  const reading: DocumentReading = {
    document: null,
    title: 'The Clap',
    file_title: 'TheClap',
    parts_found: 30,
    builds: [],
    ocr_builds: [],
    ocr: null,
    ocr_offered: false,
    options: [],
    errors: [],
  };
  const read: string[] = [];
  await page.route('**/api/health', (route) => route.fulfill({ json: { status: 'ok' } }));
  await page.route('**/api/presets', (route) =>
    route.fulfill({ json: { sources: [], categories: {}, presets: [] } }),
  );
  await page.route('**/api/master-list', (route) => route.abort('failed'));
  await page.route('**/api/documents/read', (route) => {
    read.push(route.request().postDataBuffer()?.toString() ?? '');
    return route.fulfill({
      contentType: 'text/event-stream',
      body: `data: ${JSON.stringify({ event: 'result', reading })}\n\n`,
    });
  });

  await page.goto('/catalog/pedalpcb/theclap/');
  await page.getByRole('link', { name: 'Add to build' }).click();

  await expect(page).toHaveURL(/\/app\/$/);
  await expect(page.getByRole('textbox', { name: 'Project name' })).toHaveValue('The Clap');
  await expect(page.getByRole('link', { name: 'catalog', exact: true })).toHaveAttribute(
    'href',
    '/catalog/pedalpcb/theclap/',
  );
  expect(read.join()).toContain('https://docs.pedalpcb.com/project/TheClap.pdf');

  await page.reload();
  await expect(page.getByRole('textbox', { name: 'Project name' })).toHaveCount(1);
});

test('a build no longer in the catalog says so', async ({ page }) => {
  await page.route('**/api/**', (route) => route.abort('failed'));
  await page.goto('/app/?catalog=pedalpcb/gone');
  await expect(page.getByRole('alert')).toHaveText(/couldn't be found/);
});

test("a build's board positions are published by its document's hash", async ({ request }) => {
  const response = await request.get(
    '/catalog/boards/57d37c0c204c70904fa1cdf6d7e73457445f648f1e063f2f8a5ffa4da6054fb2.json',
  );
  expect(response.ok()).toBe(true);
  expect(await response.json()).toEqual({
    marks: { R1: [{ page: 2, x0: 120.5, top: 300, x1: 131, bottom: 308 }] },
  });
  // The Big Muff has none yet, so it has no file.
  const muff = await request.get(
    '/catalog/boards/7f117935d37e1b85c2904710987d50f36db616f62294619e0087605a913b890a.json',
  );
  expect(muff.status()).toBe(404);
});
