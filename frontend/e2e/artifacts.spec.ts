import { expect, test } from '@playwright/test';
import { lastAssistant, newChat, send } from './helpers';

test('open a generated HTML artifact in a sandboxed iframe panel', async ({ page }) => {
  await newChat(page);
  await send(page, '[[artifact]] make me a page');

  const a = lastAssistant(page);
  await expect(a).toContainText('tiny page');

  await a.hover();
  const open = a.getByRole('button', { name: /artifact/ });
  await expect(open).toBeVisible();
  await open.click();

  // the panel iframe carries the locked-down sandbox (no allow-same-origin)...
  const iframe = page.locator('.artifact-panel iframe');
  await expect(iframe).toHaveAttribute('sandbox', 'allow-scripts');
  // ...and renders the model's HTML
  await expect(
    page.frameLocator('.artifact-panel iframe').locator('#hi')
  ).toHaveText('Hello from the artifact');
});
