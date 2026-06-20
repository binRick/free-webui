import { getConfig } from './api';

// Instance branding + operator custom CSS, fetched from the public /api/config
// (readable before login). The layout injects customCss into a <style> element
// and components read instanceName for the visible brand.
class AppConfigStore {
  instanceName = $state('free-webui');
  customCss = $state('');
  loaded = $state(false);

  async load() {
    try {
      const c = await getConfig();
      this.instanceName = c.instance_name || 'free-webui';
      this.customCss = c.custom_css || '';
    } catch {
      // keep defaults; branding is non-critical
    } finally {
      this.loaded = true;
    }
  }
}

export const appConfig = new AppConfigStore();
