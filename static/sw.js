// Service worker mínimo: permite instalar Moto Speed como app y muestra un aviso si no hay internet.
const CACHE = 'motospeed-v1';
const OFFLINE = '/static/offline.html';
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll([OFFLINE, '/static/logo-192.png'])));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch', e => {
  // Siempre va al servidor (los datos de ventas e inventario deben ser los reales); solo sin internet muestra el aviso.
  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request).catch(() => caches.match(OFFLINE)));
  }
});
