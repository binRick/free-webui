import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        // overridable so E2E can point the dev server at a throwaway backend
        target: process.env.FW_API_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
        ws: true
      }
    }
  }
});
