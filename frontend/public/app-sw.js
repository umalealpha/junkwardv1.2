/* Omni staff app /app/ offline service worker v1.0
 * Scoped to /app/ only — does NOT affect the rest of omni (nor Nexus at /m/).
 * Caches the app shell (HTML, CSS, JS, icons) for offline access.
 * Never caches API responses or user data.
 * Kill-switch: set CACHE_VERSION to a new value to purge all caches. */

var CACHE_VERSION = 'omni-app-v2';  // v2: Prism Nexus icon refresh (purges the old cached icon)
var SHELL_CACHE = 'omni-app-shell-' + CACHE_VERSION;
var OFFLINE_PAGE = '/app';

/* Static assets to pre-cache on install */
var PRE_CACHE = [
  '/app',
  '/icon-192.png',
  '/icon-512.png',
  '/apple-touch-icon.png',
  '/app.webmanifest'
];

/* Install: pre-cache the app shell */
self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(SHELL_CACHE).then(function (cache) {
      return cache.addAll(PRE_CACHE);
    }).then(function () {
      return self.skipWaiting();
    })
  );
});

/* Activate: purge old caches (kill-switch) */
self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys.filter(function (k) {
          return k.startsWith('omni-app-') && k !== SHELL_CACHE;
        }).map(function (k) {
          return caches.delete(k);
        })
      );
    }).then(function () {
      return self.clients.claim();
    })
  );
});

/* Fetch: network-first for navigations, cache-first for static assets.
 * NEVER cache API calls (/api/, /trpc/, /_next/data/). */
self.addEventListener('fetch', function (event) {
  var url = new URL(event.request.url);

  /* Skip: non-GET, API calls, auth, external origins */
  if (event.request.method !== 'GET') return;
  if (url.pathname.startsWith('/api/')) return;
  if (url.pathname.startsWith('/trpc/')) return;
  if (url.pathname.startsWith('/_next/data/')) return;
  if (url.origin !== self.location.origin) return;

  /* Navigation requests (HTML pages under /app/) — network first, fallback to cache */
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).then(function (response) {
        if (response && response.status === 200) {
          var clone = response.clone();
          caches.open(SHELL_CACHE).then(function (cache) {
            cache.put(event.request, clone);
          });
        }
        return response;
      }).catch(function () {
        return caches.match(event.request).then(function (cached) {
          return cached || caches.match(OFFLINE_PAGE);
        });
      })
    );
    return;
  }

  /* Static assets (_next/static/, icons, fonts, CSS) — cache first, network fallback */
  if (url.pathname.startsWith('/_next/static/') ||
      url.pathname.match(/\.(png|jpg|jpeg|svg|ico|woff2?|css|js)$/)) {
    event.respondWith(
      caches.match(event.request).then(function (cached) {
        if (cached) return cached;
        return fetch(event.request).then(function (response) {
          if (response && response.status === 200) {
            var clone = response.clone();
            caches.open(SHELL_CACHE).then(function (cache) {
              cache.put(event.request, clone);
            });
          }
          return response;
        });
      })
    );
    return;
  }
});

/* Forward push events to the root sw (this sw only handles offline caching) */
self.addEventListener('push', function (event) {
  /* Let the root /sw.js handle push notifications */
});
