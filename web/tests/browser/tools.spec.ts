import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.goto('/app/tools/');
});

test('links every tool to an AliExpress search', async ({ page }) => {
  const tools = page.locator('.tool');
  await expect(tools).toHaveCount(11);
  for (const tool of await tools.all()) {
    await expect(tool.getByRole('link', { name: /AliExpress/ })).toHaveAttribute(
      'href',
      /^https:\/\/www\.aliexpress\.com\/w\/wholesale-[a-z0-9.-]+\.html$/,
    );
  }
});

test('adds up what each list costs', async ({ page }) => {
  await expect(page.getByText('About $151 with my picks')).toBeVisible();
  await expect(page.getByText('About $42 more')).toBeVisible();
});

test('links the soldering station buying guide', async ({ page }) => {
  await expect(page.getByRole('link', { name: /station buying guide/ })).toHaveAttribute(
    'href',
    /reddit\.com\/r\/soldering\//,
  );
});
