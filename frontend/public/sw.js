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

/**
 * Ruta normalizada para decidir si es API: decodifica (`/%76%31` = `/v1`,
 * `/v1%2Fjobs`) y colapsa barras repetidas (`//v1/x`).
 */
function normalizedPath(url) {
  var path = url.pathname;
  try {
    path = decodeURIComponent(path);
  } catch (e) {
    // porcentaje mal formado: se usa tal cual
  }
  return path.replace(/\/{2,}/g, "/");
}

function isApiPath(path) {
  return (
    path === "/api" || path.indexOf("/api/") === 0 || path === "/v1" || path.indexOf("/v1/") === 0
  );
}

function strategyFor(request, scopeOrigin) {
  if (request.method !== "GET") return "network-only";
  var url = new URL(request.url);
  if (url.origin !== scopeOrigin) return "network-only";
  if (url.pathname.indexOf("/_next/static/") === 0) return "cache-first";
  // La API y cualquier ruta autenticada nunca se guardan, aunque un proxy la
  // sirva desde el mismo origen y aunque sea una "navegación" (6.3).
  var path = normalizedPath(url);
  if (isApiPath(path) || path === "/sw.js") return "network-only";
  if (request.mode === "navigate" || url.pathname.indexOf("/_next/image") === 0) {
    return "network-first";
  }
  return "network-only";
}

/** Tope de páginas guardadas: las más viejas se descartan. */
var MAX_PAGES = 50;

/** Una navegación se guarda por ruta, sin query: `?page=N` no multiplica entradas. */
function pageKey(request) {
  var url = new URL(request.url);
  if (request.mode !== "navigate") return request;
  return new Request(url.origin + url.pathname);
}

/** Una respuesta con datos de sesión o de usuario no se guarda jamás. */
function isCacheable(response) {
  if (!response || !response.ok || response.redirected) return false;
  var h = response.headers;
  var cc = (h.get("cache-control") || "").toLowerCase();
  if (cc.indexOf("no-store") !== -1 || cc.indexOf("private") !== -1) return false;
  if (h.get("set-cookie")) return false;
  if ((h.get("vary") || "").toLowerCase().indexOf("cookie") !== -1) return false;
  return true;
}

/** Guardar es best-effort: una cuota llena no puede romper la respuesta de la red. */
async function safePut(cache, key, response, limit) {
  try {
    await cache.put(key, response.clone());
    if (limit && typeof cache.keys === "function") {
      var keys = await cache.keys();
      for (var i = 0; i < keys.length - limit; i++) await cache.delete(keys[i]);
    }
  } catch (e) {
    // sin espacio o caché rota: se sirve igual, sin guardar
  }
}

async function cacheFirst(request) {
  var cache = await caches.open(STATIC_CACHE);
  var hit = await cache.match(request);
  if (hit) return hit;
  var response = await fetch(request);
  if (response && response.ok) await safePut(cache, request, response);
  return response;
}

async function networkFirst(request) {
  var cache = await caches.open(PAGES_CACHE);
  var key = pageKey(request);
  var response;
  try {
    response = await fetch(request);
  } catch (err) {
    var hit = await cache.match(key);
    if (hit) return hit;
    throw err;
  }
  // Solo respuestas sanas y no personales; nunca un redirect (p. ej. el 307 a
  // /login del middleware), que serviría una sesión vieja desde la caché.
  if (isCacheable(response)) await safePut(cache, key, response, MAX_PAGES);
  return response;
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
