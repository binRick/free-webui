import { goto } from '$app/navigation';

export interface User {
  id: number;
  username: string;
  role: string;
}

export interface AuthStatus {
  user: User | null;
  setup_required: boolean;
}

class AuthStore {
  user = $state<User | null>(null);
  setupRequired = $state<boolean>(false);
  loaded = $state<boolean>(false);

  async refresh(): Promise<AuthStatus> {
    const res = await fetch('/api/auth/status');
    const status: AuthStatus = res.ok
      ? await res.json()
      : { user: null, setup_required: false };
    this.user = status.user;
    this.setupRequired = status.setup_required;
    this.loaded = true;
    return status;
  }

  async login(username: string, password: string): Promise<User> {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ?? `login failed: ${res.status}`);
    }
    this.user = await res.json();
    this.setupRequired = false;
    return this.user!;
  }

  async setup(username: string, password: string): Promise<User> {
    const res = await fetch('/api/auth/setup', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ?? `setup failed: ${res.status}`);
    }
    this.user = await res.json();
    this.setupRequired = false;
    return this.user!;
  }

  async logout(): Promise<void> {
    await fetch('/api/auth/logout', { method: 'POST' });
    this.user = null;
    await goto('/login', { replaceState: true });
  }

  async logoutEverywhere(): Promise<void> {
    await fetch('/api/auth/logout_all', { method: 'POST' });
    this.user = null;
    await goto('/login', { replaceState: true });
  }
}

export const auth = new AuthStore();
