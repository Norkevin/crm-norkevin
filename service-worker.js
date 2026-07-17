/*
 * Flow CRM service worker -- Fase 1 (instalabilidad, sin cache de datos privados).
 *
 * Regla de seguridad (Kevin): el service worker NUNCA debe cachear
 * respuestas autenticadas, datos de clientes, contratos, facturas, pagos ni
 * endpoints privados -- solo el shell visual y recursos estaticos
 * versionados. En vez de mantener una lista negra de rutas privadas (que se
 * desactualiza cada vez que se agrega una ruta nueva en app.py), se usa una
 * ALLOWLIST de lo unico que se cachea: /static/* y el manifest. Todo lo
 * demas pasa directo a la red, sin caches.put() de ningun tipo.
 */

const CACHE_VERSION = 'v1';
const CACHE_NAME = 'flowcrm-static-' + CACHE_VERSION;
const OFFLINE_URL = '/offline.html';

function isCacheableStaticAsset(url) {
  if (url.origin !== self.location.origin) return false;
  if (url.pathname === '/manifest.webmanifest') return true;
  if (url.pathname.startsWith('/static/')) return true;
  return false;
}

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      // Precarga minima -- solo el shell offline y el manifest. El resto de
      // /static/* se va cacheando solicitud por solicitud (ver fetch()) para
      // no tener que mantener una lista manual de cada archivo estatico.
      return cache.addAll([OFFLINE_URL, '/manifest.webmanifest']);
    }).catch(function () {
      // Si algo falla en la precarga (p.ej. sin red durante el install), no
      // se rompe el registro del service worker.
    })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(
        names
          .filter(function (name) { return name !== CACHE_NAME; })
          .map(function (name) { return caches.delete(name); })
      );
    }).then(function () {
      return self.clients.claim();
    })
  );
});

self.addEventListener('message', function (event) {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

self.addEventListener('fetch', function (event) {
  const request = event.request;
  if (request.method !== 'GET') return; // nunca interceptar POST/PUT/DELETE

  const url = new URL(request.url);

  // Navegacion (carga de una pagina HTML completa): network-first, sin
  // cachear NUNCA el HTML (podria ser una pagina autenticada). Si la red
  // falla, se muestra la pagina offline -- nunca una version vieja cacheada.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(function () {
        return caches.match(OFFLINE_URL);
      })
    );
    return;
  }

  // Unicamente recursos estaticos versionados van cache-first. Todo lo
  // demas (rutas de la API, autenticacion, el portal del cliente,
  // cotizaciones, contratos, cuestionarios, facturas, archivos,
  // configuracion, Recurrente, Gmail OAuth, etc.) pasa directo a la red
  // sin tocar el cache -- esto cubre automaticamente cualquier ruta
  // privada nueva que se agregue despues en app.py, sin tener que
  // mantener una lista negra a mano en este archivo.
  if (isCacheableStaticAsset(url)) {
    event.respondWith(
      caches.match(request).then(function (cached) {
        if (cached) return cached;
        return fetch(request).then(function (response) {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then(function (cache) { cache.put(request, copy); });
          }
          return response;
        });
      })
    );
    return;
  }

  // Pass-through: no respondWith() -> el navegador maneja la solicitud
  // normalmente contra la red, sin que el service worker la toque.
});
