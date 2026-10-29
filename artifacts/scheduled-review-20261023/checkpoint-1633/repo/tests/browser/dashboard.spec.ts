import { test, expect } from '@playwright/test';

async function ready(page) {
  await expect(page.locator('#view')).toHaveAttribute('aria-busy', 'false');
  await expect(page.getByText('Could not load this section')).toHaveCount(0);
}

test('all pages render at desktop and mobile widths without page overflow or browser errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', e => { if (e.type() === 'error') errors.push(e.text()); });
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    for (const route of ['overview', 'team', 'recommendations', 'players', 'planning', 'activity', 'controls', 'health']) {
      await page.goto('/#' + route);
      await ready(page);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await expect(page.locator('.nav a[aria-current="page"]')).toHaveAttribute('href', '#' + route);
    }
  }
  expect(errors).toEqual([]);
});

test('pitch retains XI, formation, bench and captain; details work by keyboard', async ({ page }) => {
  await page.goto('/#team');
  await ready(page);
  await expect(page.getByRole('heading', { name: /3-5-2/ })).toBeVisible();
  await expect(page.locator('.pitch .player')).toHaveCount(11);
  await expect(page.locator('.bench .player')).toHaveCount(4);
  await expect(page.locator('.pitch .arm[aria-label="captain"]')).toHaveCount(1);
  const player = page.locator('.pitch .player').first();
  await player.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Close player details' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(player).toBeFocused();
  await page.screenshot({ path: 'artifacts/dashboard-team-fixture.png', fullPage: true });
  await page.setViewportSize({ width: 320, height: 844 });
  await page.screenshot({ path: 'artifacts/dashboard-team-mobile-fixture.png', fullPage: true });
  expect(await page.locator('.pitch').evaluate(pitch => {
    const box = pitch.getBoundingClientRect();
    return [...pitch.querySelectorAll('.player')].every(player => {
      const r = player.getBoundingClientRect();
      return r.left >= box.left && r.right <= box.right;
    });
  })).toBe(true);
});

test('search, position, paging and comparison use API-backed players', async ({ page }) => {
  await page.goto('/#players');
  await ready(page);
  await page.getByLabel('Search players').fill('Saka');
  await expect(page.locator('tbody tr')).toHaveCount(1);
  await expect(page.locator('tbody')).toContainText('Saka');
  await page.getByLabel('Search players').fill('');
  await expect(page.locator('tbody tr')).toHaveCount(25);
  await page.getByLabel('Position', { exact: true }).selectOption('DEF');
  await ready(page);
  await expect(page.locator('tbody tr').first()).toContainText('DEF');
  await page.getByLabel('Position', { exact: true }).selectOption('');
  await page.getByLabel('Per page', { exact: true }).selectOption('10');
  await expect(page.locator('tbody tr')).toHaveCount(10);
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.locator('.pager')).toContainText('page 2');
  await page.locator('tbody input[type="checkbox"]').nth(0).check();
  await page.locator('tbody input[type="checkbox"]').nth(1).check();
  await expect(page.getByRole('heading', { name: 'Comparison', exact: true })).toBeVisible();
  await page.getByLabel('Search players').fill('Saka');
  await page.locator('.nav a[href="#team"]').click();
  await expect(page.locator('#title')).toHaveText('My Team');
  await page.waitForTimeout(350); // Pass the search debounce boundary after navigation.
  await expect(page.locator('#title')).toHaveText('My Team');
});

test('settings enable dry planning and dismissed confirmation never starts a job', async ({ page }) => {
  await page.goto('/#controls');
  await ready(page);
  await expect(page.getByRole('button', { name: 'Dry plan', exact: true })).toBeDisabled();
  await page.getByLabel('FPL team id').fill('123');
  await page.getByRole('button', { name: 'Save settings' }).click();
  await expect(page.getByRole('button', { name: 'Dry plan', exact: true })).toBeEnabled();
  page.once('dialog', d => d.dismiss());
  await page.getByRole('button', { name: 'Refresh data', exact: true }).click();
  expect((await (await page.request.get('/api/jobs')).json()).items).toEqual([]);
});

test('fixture calendar filters by club and gameweek', async ({ page }) => {
  await page.goto('/#planning');
  await ready(page);
  const clubs = await page.getByLabel('Club', { exact: true }).locator('option').allTextContents();
  expect(clubs.length).toBeGreaterThan(1);
  await page.getByLabel('Club', { exact: true }).selectOption({ label: clubs[1] });
  await ready(page);
  for (const row of await page.locator('.cols tbody tr').allTextContents()) expect(row).toContain(clubs[1]);
  await page.getByLabel('Gameweek', { exact: true }).selectOption({ index: 1 });
  await ready(page);
  await expect(page.locator('.cols h2')).toHaveCount(1);
});

test('stale, missing and network error states are explicit; stored text cannot inject HTML', async ({ page }) => {
  await page.goto('/#overview');
  await ready(page);
  await expect(page.getByText('Refresh recommended')).toBeVisible();
  await page.goto('/#recommendations');
  await ready(page);
  await expect(page.getByText("<script>alert('unsafe')</script>", { exact: true })).toBeVisible();
  await expect(page.locator('#view script')).toHaveCount(0);
  await page.route('**/api/overview', r => r.fulfill({ json: { meta: { state: 'missing' }, data: null } }));
  await page.goto('/#overview');
  await expect(page.getByText('No database yet', { exact: true })).toBeVisible();
  await page.unroute('**/api/overview');
  await page.route('**/api/team', r => r.fulfill({ status: 503, json: { error: 'Temporarily unavailable' } }));
  await page.goto('/#team');
  await expect(page.getByText('Could not load this section')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});
