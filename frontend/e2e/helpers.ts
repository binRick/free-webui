import { expect, type Page } from '@playwright/test';

export async function newChat(page: Page) {
  await page.goto('/');
  await expect(page).toHaveURL(/\/chat\/.+/);
  await expect(page.getByPlaceholder(/message/)).toBeVisible();
}

export async function send(page: Page, text: string) {
  await page.getByPlaceholder(/message/).fill(text);
  await page.getByRole('button', { name: 'send', exact: true }).click();
}

export const lastAssistant = (page: Page) => page.locator('.msg.assistant').last();
