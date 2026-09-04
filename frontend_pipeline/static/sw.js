/* FUTUR ▮ TERMINAL — service worker PWA
   - HTML (navigations), /static/js|css : network-first (les déploiements se propagent), repli cache
   - /api/* GET : network-first avec repli cache (dernières données connues hors-ligne)
   - jamais mis en cache : /login, /logout, /api/me, toute requête non-GET
   Incrémenter VERSION à chaque déploiement du front pour invalider l'ancien shell. */
const VERSION = "cc-v4";
const SHELL_CACHE = `shell-${VERSION}`;
const DATA_CACHE = `data-${VERSION}`;
const SHELL = [
  "/",
  "/static/css/terminal.css",
  "/static/js/core.js",
  "/static/js/views/portfolio.js",
  "/static/js/views/lab.js",
  "/static/js/views/tournament.js",
  "/static/js/views/cryptos.js",
  "/static/js/views/forecasts.js",
  "/static/js/views/world.js",
  "/static/js/views/edgelab.js",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js",
];
const NEVER_CACHE = ["/login", "/logout", "/api/me"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(SHELL_CACHE)
      // "/" exige la session : un 302 vers /login ne doit pas faire échouer l'installation
      .then((c) => Promise.all(SHELL.map((u) => c.add(u).catch(() => null))))
      .then(() => self.skipWaiting())
  );
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
    // seules les réponses 200 sont conservées (jamais une redirection vers /login ni une erreur)
    if (res.ok && res.status === 200 && !res.redirected) cache.put(req, res.clone());
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

// clic sur une notification → focus/ouverture de l'app
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
  if (req.method !== "GET") return;                       // POST/DELETE : réseau direct
  const url = new URL(req.url);
  const path = url.pathname;
  if (url.origin === self.location.origin && NEVER_CACHE.some((p) => path === p || path.startsWith(p + "?"))) return; // réseau direct, jamais en cache
  if (path.startsWith("/api/")) {
    e.respondWith(networkFirst(req, DATA_CACHE));
  } else if (req.mode === "navigate" || path === "/" || path.endsWith(".html")
             || path.startsWith("/static/js/") || path.startsWith("/static/css/") || path === "/sw.js") {
    e.respondWith(networkFirst(req, SHELL_CACHE));
  } else {
    e.respondWith(cacheFirst(req));                        // icônes, manifest, echarts CDN, polices
  }
});
