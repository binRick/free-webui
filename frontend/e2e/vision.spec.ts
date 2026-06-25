import { expect, test } from '@playwright/test';
import { newChat } from './helpers';

// a 1x1 transparent PNG
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64'
);

test('warns when attaching an image to a text-only model, clears for a vision model', async ({
  page
}) => {
  await newChat(page); // default model is "e2e-model" (text-only)

  await page
    .locator('input[type=file][accept="image/*"]')
    .setInputFiles({ name: 'pic.png', mimeType: 'image/png', buffer: PNG });

  // the warning calls out the text model
  const warn = page.locator('.vision-warn');
  await expect(warn).toBeVisible();
  await expect(warn).toContainText('e2e-model');

  // switch to the vision-named model -> warning clears
  await page.locator('.header-controls .trigger').click();
  await page.getByRole('option', { name: 'moondream' }).click();
  await expect(page.locator('.vision-warn')).toHaveCount(0);
});
