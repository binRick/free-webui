export type ThemeMode = 'system' | 'light' | 'dark';

const KEY = 'free-webui:theme';
const ACCENT_KEY = 'free-webui:accent';

// A small palette of accent presets the user can pick (null = theme default).
export const ACCENT_PRESETS = ['#22d3ee', '#a78bfa', '#34d399', '#f472b6', '#fbbf24', '#60a5fa'];

class ThemeStore {
  mode = $state<ThemeMode>('system');
  effective = $state<'light' | 'dark'>('dark');
  accent = $state<string | null>(null);

  init() {
    if (typeof window === 'undefined') return;
    const saved = localStorage.getItem(KEY);
    if (saved === 'light' || saved === 'dark' || saved === 'system') {
      this.mode = saved;
    }
    const a = localStorage.getItem(ACCENT_KEY);
    if (a && /^#[0-9a-fA-F]{6}$/.test(a)) this.accent = a;
    this.apply();
    this.applyAccent();
    window
      .matchMedia('(prefers-color-scheme: dark)')
      .addEventListener('change', () => this.apply());
  }

  setAccent(hex: string | null) {
    this.accent = hex && /^#[0-9a-fA-F]{6}$/.test(hex) ? hex : null;
    if (typeof localStorage !== 'undefined') {
      if (this.accent) localStorage.setItem(ACCENT_KEY, this.accent);
      else localStorage.removeItem(ACCENT_KEY);
    }
    this.applyAccent();
  }

  private applyAccent() {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    // override the theme's --accent (per-user), or clear to fall back to it.
    if (this.accent) root.style.setProperty('--accent', this.accent);
    else root.style.removeProperty('--accent');
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
