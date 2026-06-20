// Lightweight, dependency-free i18n. English is the source catalog; other
// locales fall back to English per-key. `t()` reads the reactive `i18n.locale`,
// so any `{t('key')}` in a template re-renders when the locale changes.
//
// Adding a language = drop a `locales/<code>.json` (same keys as en.json),
// import it here, and add it to LOCALES. Adding a string = add the key to
// en.json (and translations where available).
import de from './locales/de.json';
import en from './locales/en.json';
import es from './locales/es.json';
import fr from './locales/fr.json';
import it from './locales/it.json';
import pt from './locales/pt.json';

type Catalog = Record<string, string>;

const CATALOGS: Record<string, Catalog> = { en, es, fr, de, it, pt };

export const LOCALES: { code: string; label: string }[] = [
  { code: 'en', label: 'English' },
  { code: 'es', label: 'Español' },
  { code: 'fr', label: 'Français' },
  { code: 'de', label: 'Deutsch' },
  { code: 'it', label: 'Italiano' },
  { code: 'pt', label: 'Português' }
];

const STORAGE_KEY = 'fw_locale';

class I18n {
  locale = $state('en');

  init(): void {
    let pick: string | null = null;
    try {
      pick = localStorage.getItem(STORAGE_KEY);
    } catch {
      pick = null;
    }
    if (!pick || !CATALOGS[pick]) {
      const browser = typeof navigator !== 'undefined' ? navigator.language?.slice(0, 2) : '';
      pick = browser && CATALOGS[browser] ? browser : 'en';
    }
    this.locale = pick;
  }

  set(code: string): void {
    if (!CATALOGS[code]) return;
    this.locale = code;
    try {
      localStorage.setItem(STORAGE_KEY, code);
    } catch {
      /* ignore */
    }
  }
}

export const i18n = new I18n();

export function t(key: string, params?: Record<string, string | number>): string {
  // Reads i18n.locale so this is reactive inside templates / $derived.
  const cat = CATALOGS[i18n.locale] ?? CATALOGS.en;
  let s = cat[key] ?? CATALOGS.en[key] ?? key;
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      s = s.replaceAll(`{${k}}`, String(v));
    }
  }
  return s;
}
