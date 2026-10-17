// No API, session or receipt caching. Financial data is fetched from the server.
const CACHE = 'rota-shell-v1';
self.addEventListener('install', event => {self.skipWaiting(); event.waitUntil(caches.open(CACHE).then(c => c.addAll(['/offline.html','/offline.css','/icon-192.png'])));});
self.addEventListener('activate', event => event.waitUntil(Promise.all([self.clients.claim(),caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))])));
self.addEventListener('fetch', event => {
  if(new URL(event.request.url).pathname === '/offline.css') {event.respondWith(caches.match('/offline.css'));return;}
  if(event.request.mode === 'navigate') event.respondWith(fetch(event.request).catch(()=>caches.match('/offline.html')));
});
