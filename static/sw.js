const CACHE_NAME = 'bookexpo-offline-v3';
const OFFLINE_ASSETS = [
  '/',
  '/static/css/main.css',
  '/static/js/base.js',
  '/static/js/admin_dashboard.js'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(OFFLINE_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  const isStatic = url.pathname.startsWith('/static/');
  const isHtmlFragment = url.pathname.startsWith('/book_details/');
  const isApi = url.pathname.startsWith('/api/') || url.pathname.startsWith('/toggle') || url.pathname.startsWith('/replenish');

  // Never cache dynamic or state-changing endpoints.
  if (!isStatic || isHtmlFragment || isApi) {
    event.respondWith(fetch(request));
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          return resp;
        })
        .catch(() => caches.match('/'));
    })
  );
});
