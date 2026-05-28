// Minimal pass-through service worker — exists so the manifest is install-
// able. We deliberately skip any caching: the chat UI is dynamic and
// stale-cache bugs are worse than no-cache.
self.addEventListener('install', (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', () => {
  // Default network fetch — no interception.
});
