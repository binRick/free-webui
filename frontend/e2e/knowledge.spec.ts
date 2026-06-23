import { expect, test } from '@playwright/test';
import { newChat } from './helpers';

test('upload a document and enable full-context RAG', async ({ page }) => {
  await newChat(page);
  await page.locator('button.settings-toggle').click();

  // upload a .txt through the (hidden) document file input
  await page.locator('input[type=file][accept*="docx"]').setInputFiles({
    name: 'kb.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('penguins huddle together to stay warm')
  });
  await expect(page.locator('.doc-list')).toContainText('kb.txt');

  // the full-context toggle appears once a doc is attached; enable + save
  await page
    .locator('label.toggle')
    .filter({ hasText: 'full context' })
    .getByRole('checkbox')
    .check();
  await page.locator('.settings-actions').getByRole('button', { name: 'save' }).click();

  // the RAG badge (📎) now shows in the header
  await expect(page.locator('.rag-badge').first()).toBeVisible();
});
