const CACHE_VERSION = 'vowpic-pwa-v2';
const APP_SHELL_CACHE = `${CACHE_VERSION}-shell`;
const RUNTIME_CACHE = `${CACHE_VERSION}-runtime`;
const APP_SHELL = ['/', '/offline.html', '/manifest.webmanifest', '/icons/pwa-icon.svg'];
const MAX_RUNTIME_ITEMS = 80;

function isApiRequest(url) {
  return url.pathname.startsWith('/api') || url.pathname.startsWith('/static/styles');
}

function isPaymentOrAuthNavigation(url) {
  return ['payment', 'purchase_id', 'checkout_id', 'subscription', 'code', 'state'].some((key) => url.searchParams.has(key));
}

function isCacheableAsset(request, url) {
  if (request.method !== 'GET') return false;
  if (isApiRequest(url)) return false;
  if (url.origin !== self.location.origin) return false;
  return ['script', 'style', 'image', 'font'].includes(request.destination)
    || /\.(js|css|svg|png|jpg|jpeg|webp|woff2?)$/i.test(url.pathname);
}

async function trimCache(cacheName, maxItems) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length <= maxItems) return;
  await cache.delete(keys[0]);
  await trimCache(cacheName, maxItems);
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(APP_SHELL_CACHE)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => !key.startsWith(CACHE_VERSION)).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (isApiRequest(url)) return;

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (!isPaymentOrAuthNavigation(url) && response.ok) {
            const copy = response.clone();
            caches.open(APP_SHELL_CACHE).then((cache) => cache.put('/', copy));
          }
          return response;
        })
        .catch(async () => (await caches.match('/')) || caches.match('/offline.html'))
    );
    return;
  }

  if (!isCacheableAsset(request, url)) return;

  event.respondWith(
    caches.match(request).then((cached) => {
      const networkFetch = fetch(request)
        .then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(RUNTIME_CACHE)
              .then((cache) => cache.put(request, copy))
              .then(() => trimCache(RUNTIME_CACHE, MAX_RUNTIME_ITEMS));
          }
          return response;
        })
        .catch(() => cached);
      return cached || networkFetch;
    })
  );
});
