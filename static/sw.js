const CACHE_NAME = 'bookexpo-static-v7';
const OFFLINE_ASSETS = [
  '/static/site.webmanifest',
  '/static/images/exis-logo-mark.svg',
  '/static/images/exis-icon-192.png'
];

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

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
  if (url.origin !== self.location.origin) return;

  const isStatic = url.pathname.startsWith('/static/');
  const isStyleOrScript = url.pathname.startsWith('/static/css/') || url.pathname.startsWith('/static/js/');
  const isHtmlFragment = url.pathname.startsWith('/book_details/');
  const isApi = url.pathname.startsWith('/api/') || url.pathname.startsWith('/toggle') || url.pathname.startsWith('/replenish');

  // Never cache dynamic or state-changing endpoints.
  if (!isStatic || isHtmlFragment || isApi) {
    event.respondWith(fetch(request));
    return;
  }

  if (isStyleOrScript) {
    event.respondWith(fetch(request, { cache: 'no-store' }));
    return;
  }

  // Network-first for static media assets only. Form/API writes are never queued offline.
  event.respondWith(
    fetch(request, { cache: 'reload' })
      .then((resp) => {
        if (resp.ok) {
          const copy = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
        }
        return resp;
      })
      .catch(() => caches.match(request))
  );
});
