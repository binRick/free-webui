import { expect, test, type Page } from '@playwright/test';

// Shared helpers ------------------------------------------------------------

async function newChat(page: Page) {
  await page.goto('/');
  await expect(page).toHaveURL(/\/chat\/.+/);
  await expect(page.getByPlaceholder(/message/)).toBeVisible();
}

async function send(page: Page, text: string) {
  await page.getByPlaceholder(/message/).fill(text);
  await page.getByRole('button', { name: 'send', exact: true }).click();
}

const lastAssistant = (page: Page) => page.locator('.msg.assistant').last();
const lastUser = (page: Page) => page.locator('.msg.user').last();

// Specs ---------------------------------------------------------------------

test('edit an assistant reply in place — no new turn', async ({ page }) => {
  await newChat(page);
  await send(page, 'hello');
  await expect(page.getByText('You said: hello')).toBeVisible();
  const before = await page.locator('.msg').count();

  const a = lastAssistant(page);
  await a.hover();
  await a.getByRole('button', { name: 'edit', exact: true }).click();
  const editor = page.locator('textarea.edit');
  await editor.fill('hand-corrected reply');
  await page.getByRole('button', { name: 'save', exact: true }).click();

  await expect(page.getByText('hand-corrected reply')).toBeVisible();
  await expect(page.getByText('You said: hello')).toHaveCount(0);
  // in-place edit must not append a turn
  expect(await page.locator('.msg').count()).toBe(before);
});

test('edit a user message re-runs and truncates the thread', async ({ page }) => {
  await newChat(page);
  await send(page, 'first');
  await expect(page.getByText('You said: first')).toBeVisible();

  const u = lastUser(page);
  await u.hover();
  await u.getByRole('button', { name: 'edit', exact: true }).click();
  await page.locator('textarea.edit').fill('second');
  await page.getByRole('button', { name: 'save & rerun' }).click();

  await expect(page.getByText('You said: second')).toBeVisible();
  await expect(page.getByText('You said: first')).toHaveCount(0);
});

test('regenerate creates a navigable variant', async ({ page }) => {
  await newChat(page);
  await send(page, 'hello');
  await expect(page.getByText('You said: hello')).toBeVisible();

  const a = lastAssistant(page);
  await a.hover();
  await a.getByRole('button', { name: 'regenerate' }).click();

  // a second variant now exists: the ◀ n/m ▶ counter shows two
  await expect(page.locator('.vcount')).toContainText('/2');
});

test('queue a message typed while the model is streaming', async ({ page }) => {
  await newChat(page);
  // a slow reply gives time to queue the next turn
  await send(page, '[[slow]] one');
  // mid-stream the composer is usable and a "queue" button appears
  const composer = page.getByPlaceholder(/message/);
  await composer.fill('second turn');
  await page.getByRole('button', { name: /queue/ }).click();

  // a queued chip shows the pending turn...
  await expect(page.locator('.queued-chip')).toContainText('second turn');
  // ...and it auto-sends once the slow reply finishes
  await expect(page.getByText('You said: second turn')).toBeVisible({ timeout: 20_000 });
});
