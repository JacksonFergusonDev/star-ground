import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const pages = [
  { path: '/', heading: "One shopping list for every pedal you're building." },
  { path: '/app/', heading: 'Projects' },
  { path: '/app/list/', heading: 'Shopping list' },
  { path: '/app/stock/', heading: 'Stock' },
  { path: '/app/export/', heading: 'Export' },
  { path: '/app/check/?project=gone', heading: "This project isn't here" },
  { path: '/catalog/', heading: 'Circuit catalog' },
  { path: '/catalog/pedalpcb/theclap/', heading: 'The Clap' },
  { path: '/404.html', heading: 'No continuity.' },
];

for (const { path, heading } of pages) {
  test.describe(path, () => {
    test.beforeEach(async ({ page }) => {
      await page.goto(path);
    });

    test('shows its heading', async ({ page }) => {
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(heading);
    });

    test('has no accessibility violations', async ({ page }) => {
      const results = await new AxeBuilder({ page }).analyze();
      expect(results.violations).toEqual([]);
    });

    test('fits the window without scrolling sideways', async ({ page }) => {
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
    });

    test('marks every standalone link with a house icon', async ({ page }) => {
      // House style: a link outside a sentence carries an icon saying where it goes. The header
      // bar, the app's sidebar, breadcrumbs, tabs and the skip link are navigation chrome, not
      // content links.
      const bare = await page
        .locator('main a, .hs-footer-links a')
        .filter({ hasNot: page.locator('svg') })
        .evaluateAll((links) =>
          links.filter((a) => !a.closest('p, nav')).map((a) => a.textContent?.trim()),
        );
      expect(bare).toEqual([]);
    });
  });
}

test('the moving field starts after load without fetching more scripts, including on refresh', async ({
  page,
}) => {
  // Prepare the code with the page, but leave GPU work until after load.
  // Record the attempt even in browsers without WebGL2.
  await page.addInitScript(() => {
    const getContext = HTMLCanvasElement.prototype.getContext;
    Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
      value(this: HTMLCanvasElement, type: string, options?: unknown) {
        if (type === 'webgl2') performance.mark('circuit-start');
        return Reflect.apply(getContext, this, [type, options]);
      },
    });
  });
  for (const refresh of [false, true]) {
    if (refresh) await page.reload();
    else await page.goto('/');
    await page.waitForFunction(() => performance.getEntriesByName('circuit-start').length > 0);
    const { loaded, started, scripts } = await page.evaluate(() => {
      const [navigation] = performance.getEntriesByType(
        'navigation',
      ) as PerformanceNavigationTiming[];
      const scripts = performance
        .getEntriesByType('resource')
        .filter((entry) => /\.js(?:\?|$)/.test(entry.name))
        .map((entry) => entry.startTime);
      return {
        loaded: navigation.loadEventEnd,
        started: performance.getEntriesByName('circuit-start')[0]!.startTime,
        scripts,
      };
    });
    expect(started).toBeGreaterThanOrEqual(loaded);
    expect(scripts.length).toBeGreaterThan(0);
    for (const start of scripts) expect(start).toBeLessThan(loaded);
  }
});

test('uses the house fonts', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const loaded = await page.evaluate(() =>
    [...document.fonts]
      .filter((f) => f.status === 'loaded')
      .map((f) => f.family.replaceAll('"', '')),
  );
  expect(loaded).toEqual(expect.arrayContaining(['DM Sans']));
});

test('jump links reach their sections', async ({ page }) => {
  await page.goto('/');
  for (const name of ['How it works', 'Documents', 'Accuracy']) {
    await page
      .getByRole('navigation', { name: 'On this page' })
      .getByRole('link', { name: new RegExp(name, 'i') })
      .click();
    await expect(page.locator(`section:target`)).toBeInViewport();
  }
});

test("the app's pages move without reloading, and the browser's back button works", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name.endsWith('-mobile'), 'The sidebar folds into a menu on phones.');
  await page.goto('/app/');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Projects');
  const nav = page.getByRole('navigation', { name: 'App' });
  await nav.getByRole('link', { name: /Shopping list/ }).click();
  await expect(page).toHaveURL(/\/app\/list\/$/);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Shopping list');
  await expect(page.getByRole('heading', { level: 1 })).toBeFocused();
  await expect(nav.getByRole('link', { name: /Shopping list/ })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(await page.evaluate(() => performance.getEntriesByType('navigation').length)).toBe(1);

  await page.goBack();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Projects');
  await expect(page).toHaveTitle('Projects | Star Ground');
});

test('the menu opens the app on phones', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.endsWith('-mobile'), 'The sidebar is always open on laptops.');
  await page.goto('/app/');
  const nav = page.getByRole('navigation', { name: 'App' });
  await expect(nav).toBeHidden();
  await page.getByRole('button', { name: 'Menu' }).click();
  await nav.getByRole('link', { name: /Stock/ }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Stock');
  await expect(nav).toBeHidden();
});

test('the moving field survives a lost WebGL context', async ({ page }) => {
  // A GPU reset or a phone reclaiming memory drops the context. house-style's
  // runField asks for it back, and the board rebuilds its programs and buffers.
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    // Drawing with the old context's programs logs a WebGL warning, not an error.
    if (message.type() === 'error' || /WebGL.*INVALID/.test(message.text())) {
      errors.push(message.text());
    }
  });
  await page.goto('/');
  const motion = page.getByRole('button', { name: 'Play motion', exact: true });
  const running = await motion
    .waitFor({ timeout: 10_000 })
    .then(() => true)
    .catch(() => false);
  test.skip(!running, 'This browser has no WebGL2, so the field never starts');

  const restored = await page.evaluate(async () => {
    const canvas = document.querySelector<HTMLCanvasElement>('.hs-field')!;
    const lose = canvas.getContext('webgl2')?.getExtension('WEBGL_lose_context');
    if (!lose) return 'unsupported';
    const event = (name: string) =>
      new Promise((resolve) => canvas.addEventListener(name, resolve, { once: true }));
    // restoreContext() does nothing until the loss has been delivered and its
    // listeners, runField's among them, have finished.
    const lost = event('webglcontextlost');
    lose.loseContext();
    await lost;
    await new Promise((resolve) => setTimeout(resolve));
    const back = event('webglcontextrestored');
    lose.restoreContext();
    await back;
    return canvas.getContext('webgl2')?.isContextLost() ? 'lost' : 'restored';
  });
  test.skip(restored === 'unsupported', 'This engine has no WEBGL_lose_context');
  expect(restored).toBe('restored');
  // The button still drives the field, and nothing logged an error.
  await motion.click();
  await page.getByRole('button', { name: 'Pause motion', exact: true }).waitFor();
  expect(errors).toEqual([]);
});
