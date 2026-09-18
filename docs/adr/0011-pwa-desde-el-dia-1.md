# ADR-0011 · PWA desde el día 1, con el service worker acotado a propósito

**Estado:** aceptada · **Fecha:** 2026-09-15 · **Fase:** 2

## Contexto

En Véktor no hay nada de PWA para copiar, y esa es toda la evidencia disponible:

- `find frontend -iname "manifest*.json*" -o -iname "*service-worker*" -o -iname "sw.ts" -o -iname "sw.js"`
  (excluyendo `node_modules`) → **cero resultados**.
- `grep -n "next-pwa\|workbox" frontend/package.json` → cero.
- `frontend/public/` contiene solo `.gitkeep`, `doodles/` y `screenshots/`. Ningún ícono, ningún manifiesto.

La app no es instalable, no tiene ícono en la pantalla de inicio, no tiene pantalla de arranque y en iOS se
abre siempre dentro de Safari con la barra de direcciones comiéndose la altura útil.

Para Véktor —que se usa sentado, en una compu, para mirar métricas— es una ausencia tolerable. Para este
proyecto no: la Fase 6 es "operación rápida" y el usuario es alguien **de pie en el playón, con el celular
en una mano, mojado, con señal intermitente**. Que la app se abra desde un ícono y a pantalla completa no
es cosmética; es la diferencia entre una herramienta y una página web.

Y hay una razón de secuencia: retrofitear una PWA sobre una app desplegada es más caro que empezar con
ella. El `start_url`, el `scope` y la estrategia de caché condicionan el ruteo y el deploy. Decidirlo en la
Fase 6, con usuarios reales y una base de código grande, es rehacer cosas.

## Objeción

**"PWA desde el día 1" mete dos decisiones muy distintas en una sola frase, y una de las dos es peligrosa
en la Fase 2.**

*Instalabilidad* (manifiesto, íconos, `theme-color`, meta de iOS) es barata, sin estado, sin riesgo, y
efectivamente conviene resolverla ahora.

*Un service worker con precaching* es otra cosa. Durante seis fases más el shell de la app va a cambiar
todas las semanas, y un SW que precachea es el mecanismo clásico por el cual un usuario queda clavado en un
bundle viejo sin saberlo — el síntoma es "arreglamos el bug y el lavadero lo sigue viendo", y es
notoriamente difícil de diagnosticar porque no se reproduce en la máquina de quien lo arregló. Encima
ensucia el diagnóstico del pipeline de deploy justo en la fase donde el pipeline es lo que se está
construyendo.

Así que la decisión separa las dos mitades: instalabilidad completa desde el día 1, y un service worker
**deliberadamente tonto** —network-first para todo lo que no sea un asset con hash— con interruptor de
apagado y camino de desinstalación probado. El offline de verdad ya lo resuelve `offlineQueueStore` +
`useOfflineSubmit` (la pieza #2 del roadmap), que funciona **sin** service worker: encola en
`localStorage`, distingue red de 4xx de 5xx y trata el 409 `DUPLICATE_IDEMPOTENT` como éxito. Meterle
caché de datos al SW sería un segundo mecanismo de offline compitiendo con el que ya sabemos que funciona.

## Decisión

1. **Instalable desde el primer deploy.** `frontend/public/manifest.webmanifest` con `name`, `short_name`,
   `start_url: "/dashboard"`, `scope: "/"`, `display: "standalone"`, `theme_color`, `background_color` e
   íconos de 192 y 512 px (uno de ellos `purpose: "maskable"`). Más `apple-touch-icon` y
   `apple-mobile-web-app-*` en el `<head>`, porque iOS ignora el manifiesto para eso.
2. **Service worker propio, escrito a mano, sin `next-pwa` ni Workbox.** Son ~60 líneas y no quiero una
   capa de generación entre la estrategia de caché y el archivo que se sirve.
3. **Una sola estrategia, doble:** `cache-first` **solo** para `/_next/static/**` (rutas con hash: si el
   contenido cambia, la URL cambia, así que nunca sirve algo viejo), y `network-first` para navegaciones y
   para todo `/_next/image` y `/api`. Ninguna respuesta de la API se cachea.
4. **Nada de precaching de rutas.** El SW no tiene lista de URLs a guardar por adelantado. Cachea lo que el
   usuario ya pidió, y nada más.
5. **Actualización inmediata:** `skipWaiting()` + `clients.claim()`, y `/sw.js` se sirve con
   `Cache-Control: no-store` (si el propio SW se cachea, el interruptor de apagado deja de funcionar).
6. **Interruptor de apagado probado.** `NEXT_PUBLIC_SW_ENABLED`; en `false`, el código no registra el SW
   **y además desregistra el que hubiera** y limpia sus cachés. El camino de desinstalación se prueba en
   CI, no se descubre durante un incidente.
7. **El offline funcional no vive en el SW.** Es `offlineQueueStore` + `useOfflineSubmit`, portados tal
   cual (ver manifiesto). El SW no guarda datos de negocio.
8. **Sin notificaciones push en la Fase 2.** Es otra decisión, con permisos, backend y consentimiento
   propios.

## Consecuencias

- **Gana:** ícono en la pantalla de inicio y pantalla completa desde el primer día en que alguien de Sola
  CleanCars abre la app. El `start_url` y el `scope` quedan fijados antes de que haya rutas que romper.
- **Gana:** la app arranca visualmente aunque la conexión esté para atrás — el shell con hash sale de la
  caché, y el contenido lo espera.
- **Cuesta:** un service worker más que entender, y la regla mental de que **el SW es parte del deploy**:
  un cambio ahí se prueba con la app instalada, no solo en una pestaña.
- **Cuesta:** íconos y capturas que alguien tiene que dibujar. Placeholder en la Fase 2, definitivos cuando
  haya marca.
- **Recordar:** iOS impone su propio techo (sin push hasta que se instala, caché acotada, el estado se
  purga si la app no se usa por semanas). El `offlineQueueStore` vive en `localStorage`, que iOS **también**
  puede purgar. Eso ya está contemplado: la cola es best-effort y avisa cuando no pudo encolar.
- **Recordar:** un SW mal desplegado es el bug más caro de esta lista, porque el usuario afectado no puede
  arreglarlo y quien lo arregla no lo reproduce. Por eso el punto 6 es parte de la decisión y no una nota.

## Cómo se verifica

`frontend/src/__tests__/pwa/manifest.test.ts`:

```ts
const m = JSON.parse(fs.readFileSync("public/manifest.webmanifest", "utf8"));

it("es instalable según los criterios mínimos", () => {
  expect(m.name && m.short_name).toBeTruthy();
  expect(m.display).toBe("standalone");
  expect(m.start_url).toBe("/dashboard");
  const tam = m.icons.map((i: any) => i.sizes);
  expect(tam).toEqual(expect.arrayContaining(["192x192", "512x512"]));
  expect(m.icons.some((i: any) => i.purpose?.includes("maskable"))).toBe(true);
});

it("los íconos que declara existen de verdad", () => {
  for (const i of m.icons) expect(fs.existsSync(`public${i.src}`)).toBe(true);
});
```

`frontend/src/__tests__/pwa/sw.test.ts` — la estrategia se testea, no se confía:

```ts
it("una navegación va a la red primero", async () => { /* … expect(fetch).toHaveBeenCalled() */ });
it("un asset con hash sale de la caché sin tocar la red", async () => { /* … */ });
it("una respuesta de /api nunca se guarda en caché", async () => {
  await handleFetch(new Request("https://api.carwash.app/v1/dummy-resources"));
  expect(await caches.match("https://api.carwash.app/v1/dummy-resources")).toBeUndefined();
});
it("con el interruptor apagado, desregistra el SW existente", async () => {
  process.env.NEXT_PUBLIC_SW_ENABLED = "false";
  await registrarSW();
  expect(navigator.serviceWorker.register).not.toHaveBeenCalled();
  expect(unregisterSpy).toHaveBeenCalled();      // el camino de salida, probado
});
```

`frontend/src/__tests__/pwa/headers.test.ts`: `next.config.ts` manda `Cache-Control: no-store` para
`/sw.js`.

Contra la URL desplegada, en el checkpoint:

```bash
curl -fsS https://app.carwash.app/manifest.webmanifest \
  | jq -e '.name and .short_name and .start_url and (.display=="standalone")
           and ([.icons[].sizes] | index("192x192")) and ([.icons[].sizes] | index("512x512"))'
curl -sI https://app.carwash.app/sw.js | grep -qi 'cache-control: .*no-store'
```
