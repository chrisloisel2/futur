/* FUTUR // COMMAND CENTER — service worker PWA
   - shell (HTML, icônes, echarts CDN) : cache-first, versionné
   - /api/* GET : network-first avec repli cache (dernières données connues hors-ligne)
   Incrémenter VERSION à chaque déploiement du front pour invalider l'ancien shell. */
const VERSION = "cc-v3";
const SHELL_CACHE = `shell-${VERSION}`;
const DATA_CACHE = `data-${VERSION}`;
const SHELL = [
  "/",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL_CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL_CACHE && k !== DATA_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

async function networkFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(req);
    if (hit) return hit;
    throw err;
  }
}

async function cacheFirst(req) {
  const hit = await caches.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok || res.type === "opaque") {
    const cache = await caches.open(SHELL_CACHE);
    cache.put(req, res.clone());
  }
  return res;
}

// clic sur une notification (gros gain/perte) → focus/ouverture de l'app
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((cs) => {
      for (const c of cs) if ("focus" in c) return c.focus();
      return self.clients.openWindow("/");
    })
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return; // POST/DELETE (forecasts, init portefeuille) : réseau direct
  const url = new URL(req.url);
  if (url.pathname.startsWith("/api/")) {
    e.respondWith(networkFirst(req, DATA_CACHE));
  } else if (req.mode === "navigate" || url.pathname === "/") {
    e.respondWith(networkFirst(req, SHELL_CACHE));
  } else {
    e.respondWith(cacheFirst(req));
  }
});
