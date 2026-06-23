import { expect, test } from '@playwright/test';

test('admin can create and suspend a user', async ({ page }) => {
  page.on('dialog', (d) => d.accept()); // the disable confirm()
  // unique so local re-runs (which reuse the DB) don't collide
  const uname = `e2e-suspend-${Date.now()}`;

  await page.goto('/admin/users');
  await expect(page.getByRole('heading', { name: 'users', exact: true })).toBeVisible();

  await page.getByPlaceholder('username').fill(uname);
  await page.getByPlaceholder(/password/).fill('userpass123');
  await page.getByRole('button', { name: 'create', exact: true }).click();

  const row = page.locator('tr', { hasText: uname });
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: 'disable', exact: true }).click();
  await expect(row.locator('.dis-pill')).toHaveText('disabled');
});

test('admin analytics page loads with token usage', async ({ page }) => {
  await page.goto('/admin/analytics');
  await expect(page.getByRole('heading', { name: 'analytics' })).toBeVisible();
  await expect(page.getByText('tokens · total')).toBeVisible();
});
