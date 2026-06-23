import { expect, test } from '@playwright/test';
import { newChat } from './helpers';

test('a saved tag becomes an autocomplete suggestion in a new chat', async ({ page }) => {
  const tag = `e2etag${Date.now()}`;

  // chat A: add a distinctive tag and save
  await newChat(page);
  await page.locator('button.settings-toggle').click();
  await page.getByPlaceholder('e.g. work, research').fill(tag);
  await page.locator('.settings-actions').getByRole('button', { name: 'save' }).click();
  // saveSettings closes the drawer when the save (incl. tags POST) completes —
  // wait for that before navigating, else the in-flight request is aborted
  await expect(page.locator('section.settings')).toHaveCount(0);

  // chat B: the tag shows as a clickable suggestion chip; clicking adds it
  await newChat(page);
  await page.locator('button.settings-toggle').click();
  const chip = page.locator('.tag-chip', { hasText: tag });
  await expect(chip).toBeVisible();
  await chip.click();
  await expect(page.getByPlaceholder('e.g. work, research')).toHaveValue(tag);
});
