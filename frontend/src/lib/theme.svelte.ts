export type ThemeMode = 'system' | 'light' | 'dark';

const KEY = 'free-webui:theme';

class ThemeStore {
  mode = $state<ThemeMode>('system');
  effective = $state<'light' | 'dark'>('dark');

  init() {
    if (typeof window === 'undefined') return;
    const saved = localStorage.getItem(KEY);
    if (saved === 'light' || saved === 'dark' || saved === 'system') {
      this.mode = saved;
    }
    this.apply();
    window
      .matchMedia('(prefers-color-scheme: dark)')
      .addEventListener('change', () => this.apply());
  }

  set(mode: ThemeMode) {
    this.mode = mode;
    if (typeof localStorage !== 'undefined') localStorage.setItem(KEY, mode);
    this.apply();
  }

  cycle() {
    const order: ThemeMode[] = ['system', 'light', 'dark'];
    const next = order[(order.indexOf(this.mode) + 1) % order.length];
    this.set(next);
  }

  private apply() {
    if (typeof document === 'undefined') return;
    const sys = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    this.effective = this.mode === 'system' ? sys : this.mode;
    document.documentElement.setAttribute('data-theme', this.effective);
  }
}

export const theme = new ThemeStore();
