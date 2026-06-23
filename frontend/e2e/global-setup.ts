import { mkdirSync } from 'node:fs';
import { request, type FullConfig } from '@playwright/test';

// Ensure an admin exists and capture its session cookie as storageState, so the
// specs start authenticated. Idempotent: first run hits first-run /setup, later
// runs (DB reused) just log in with the same credentials.
export const ADMIN = { username: 'admin', password: 'adminpass123' };
const AUTH_FILE = 'e2e/.auth/admin.json';

export default async function globalSetup(_config: FullConfig) {
  mkdirSync('e2e/.auth', { recursive: true });
  const ctx = await request.newContext({ baseURL: 'http://localhost:5173' });
  const status = await (await ctx.get('/api/auth/status')).json();
  if (status.setup_required) {
    await ctx.post('/api/auth/setup', { data: ADMIN });
  } else {
    await ctx.post('/api/auth/login', { data: ADMIN });
  }
  await ctx.storageState({ path: AUTH_FILE });
  await ctx.dispose();
}
