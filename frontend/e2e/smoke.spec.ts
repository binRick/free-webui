import { expect, test } from '@playwright/test';

// The keystone E2E: a real browser drives the whole stack (UI → backend →
// deterministic mock upstream). If this is green, the core chat path works.
test('chat: send a message and see the streamed reply', async ({ page }) => {
  await page.goto('/');
  // the root route creates a conversation and redirects into it
  await expect(page).toHaveURL(/\/chat\/.+/);

  const composer = page.getByPlaceholder(/message/);
  await expect(composer).toBeVisible();
  await composer.fill('hello e2e');
  await page.getByRole('button', { name: 'send', exact: true }).click();

  // the mock upstream echoes deterministically: "You said: <text>"
  await expect(page.getByText('You said: hello e2e')).toBeVisible({ timeout: 15_000 });
});
