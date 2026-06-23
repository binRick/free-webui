import { expect, test } from '@playwright/test';
import { newChat, send } from './helpers';

test('tool calls render as chips and survive a reload', async ({ page }) => {
  await newChat(page);

  // enable tools in the settings drawer, then SAVE (persists tools_enabled on
  // the conversation — the backend reads it from there for the tool loop)
  await page.locator('button.settings-toggle').click();
  await page
    .locator('label.toggle')
    .filter({ hasText: 'tools' })
    .getByRole('checkbox')
    .check();
  await page.locator('.settings-actions').getByRole('button', { name: 'save' }).click();

  // the mock asks the backend to run calculate(6*7), then answers
  await send(page, '[[tool]] what is 6 times 7');

  const chip = page.locator('.tool-chip');
  await expect(chip).toContainText('calculate');
  await expect(page.getByText('The calculator says 42.')).toBeVisible();

  // persisted: the 🔧 chip re-renders after a full reload (not just live)
  await page.reload();
  await expect(page.locator('.tool-chip')).toContainText('calculate');
});
