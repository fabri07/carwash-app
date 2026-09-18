/* eslint-disable */
/**
 * Service worker de carwash.app — deliberadamente tonto (ADR-0011).
 *
 * - SIN precaching: no hay lista de URLs guardadas por adelantado. Un SW que
 *   precachea el shell deja a los usuarios clavados en un bundle viejo durante
 *   las seis fases en que la app cambia todas las semanas.
 * - cache-first SOLO para `/_next/static/**`: son rutas con hash, si el
 *   contenido cambia la URL cambia, así que nunca sirve algo viejo.
 * - network-first para navegaciones y `/_next/image`, con la caché como red de
 *   seguridad si no hay señal.
 * - Todo lo demás (incluida la API, en cualquier origen) pasa directo a la red
 *   y NUNCA se guarda. El offline de datos es `offlineQueueStore`, no esto.
 *
 * Este archivo se sirve tal cual (sin build) y con `Cache-Control: no-store`
 * (next.config.ts). Las funciones se exportan vía `module.exports` solo para
 * que Jest las pruebe; en el scope del SW `module` no existe.
 */

var CACHE_VERSION = "v1";
var STATIC_CACHE = "carwash-static-" + CACHE_VERSION;
var PAGES_CACHE = "carwash-pages-" + CACHE_VERSION;

function strategyFor(request, scopeOrigin) {
  if (request.method !== "GET") return "network-only";
  var url = new URL(request.url);
  if (url.origin !== scopeOrigin) return "network-only";
  if (url.pathname.indexOf("/_next/static/") === 0) return "cache-first";
  if (url.pathname.indexOf("/api/") === 0 || url.pathname === "/sw.js") return "network-only";
  if (request.mode === "navigate" || url.pathname.indexOf("/_next/image") === 0) {
    return "network-first";
  }
  return "network-only";
}

async function cacheFirst(request) {
  var cache = await caches.open(STATIC_CACHE);
  var hit = await cache.match(request);
  if (hit) return hit;
  var response = await fetch(request);
  if (response && response.ok) await cache.put(request, response.clone());
  return response;
}

async function networkFirst(request) {
  var cache = await caches.open(PAGES_CACHE);
  try {
    var response = await fetch(request);
    // Solo respuestas sanas; nunca un redirect (p. ej. el 307 a /login del
    // middleware), que serviría una sesión vieja desde la caché.
    if (response && response.ok && !response.redirected) {
      await cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    var hit = await cache.match(request);
    if (hit) return hit;
    throw err;
  }
}

async function handleFetch(request, scopeOrigin) {
  var origin =
    scopeOrigin || (typeof self !== "undefined" && self.location ? self.location.origin : "");
  var strategy = strategyFor(request, origin);
  if (strategy === "cache-first") return cacheFirst(request);
  if (strategy === "network-first") return networkFirst(request);
  return fetch(request);
}

async function cleanupOldCaches() {
  var keep = [STATIC_CACHE, PAGES_CACHE];
  var keys = await caches.keys();
  await Promise.all(
    keys
      .filter(function (k) {
        return k.indexOf("carwash-") === 0 && keep.indexOf(k) === -1;
      })
      .map(function (k) {
        return caches.delete(k);
      }),
  );
}

if (
  typeof self !== "undefined" &&
  typeof self.addEventListener === "function" &&
  typeof self.skipWaiting === "function"
) {
  self.addEventListener("install", function () {
    self.skipWaiting();
  });
  self.addEventListener("activate", function (event) {
    event.waitUntil(
      cleanupOldCaches().then(function () {
        return self.clients.claim();
      }),
    );
  });
  self.addEventListener("fetch", function (event) {
    var strategy = strategyFor(event.request, self.location.origin);
    if (strategy === "network-only") return; // el navegador lo maneja solo
    event.respondWith(handleFetch(event.request, self.location.origin));
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    strategyFor: strategyFor,
    handleFetch: handleFetch,
    cleanupOldCaches: cleanupOldCaches,
    STATIC_CACHE: STATIC_CACHE,
    PAGES_CACHE: PAGES_CACHE,
  };
}
