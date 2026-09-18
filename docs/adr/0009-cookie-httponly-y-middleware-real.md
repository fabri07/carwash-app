# ADR-0009 · Auth por cookie httpOnly y un middleware de Next que realmente corta

**Estado:** aceptada · **Fecha:** 2026-09-15 · **Fase:** 2

## Contexto

El `middleware.ts` de Véktor (31 líneas, leído entero) declara rutas protegidas y después deja pasar
todo. `frontend/middleware.ts:4-12` arma `PROTECTED_PATHS`, `:16` calcula `isProtected`, `:18` deja pasar
lo no protegido… y `:26` deja pasar lo protegido también:

```ts
14  export function middleware(request: NextRequest) {
16    const isProtected = PROTECTED_PATHS.some((p) => pathname.startsWith(p));
18    if (!isProtected) return NextResponse.next();
20    // NOTE: Véktor actualmente usa localStorage para el token JWT, no cookies httpOnly.
21    // La protección real de rutas es client-side (ProtectedLayout + AuthHydrationBoundary).
24    // TODO post-MVP: migrar token a cookie httpOnly y agregar verificación aquí.
26    return NextResponse.next();
```

El propio comentario (`middleware.ts:20-24`) admite que es un no-op deliberado, y explica la causa: el
token está en `localStorage` y el edge no lo ve.

Los tokens efectivamente viven ahí. `frontend/src/stores/authStore.ts:2-3` usa `persist` de
`zustand/middleware` sin `storage` custom —el default es `localStorage`— y `authStore.ts:63-69` persiste
con `partialize` el **access token, el refresh token y el objeto `user` completo** bajo la clave
`vektor_auth`. `frontend/src/lib/api.ts:19-21` lee ese token del store y lo manda como `Bearer`.

No hay cookies de auth en ninguna parte: `grep -rn "Set-Cookie\|withCredentials\|document.cookie\|httpOnly"
frontend/src/` devuelve cero resultados.

Por qué no se hereda, en dos consecuencias concretas:

1. **Un XSS se lleva la sesión completa.** Cualquier script que corra en la página lee
   `localStorage.vektor_auth` y se lleva el access token, el refresh token y los datos del usuario. Con el
   refresh token, el atacante renueva la sesión indefinidamente aunque el usuario cierre el navegador.
2. **Toda ruta protegida se renderiza primero y se protege después.** Sin verificación en el edge, el
   usuario no autenticado recibe el HTML del dashboard y recién el cliente, ya hidratado, lo redirige. Es
   un parpadeo de contenido y, peor, obliga a que cada página se acuerde de estar adentro de
   `ProtectedLayout`.

## Decisión

1. **El token viaja en cookie `HttpOnly`, y el frontend nunca lo ve.** El backend responde al login y al
   registro con `Set-Cookie` para `access_token` y `refresh_token`, con
   `HttpOnly; Secure; SameSite=Lax; Path=/; Domain=.carwash.app`. El cuerpo de la respuesta lleva el
   usuario, **nunca los tokens**.
2. **Un solo dominio registrable.** El frontend en `app.carwash.app` (Vercel) y la API en
   `api.carwash.app` (Railway). Es lo que permite `SameSite=Lax` — si la API quedara en
   `*.up.railway.app`, la cookie sería cross-site y habría que usar `SameSite=None`, que es exactamente el
   modo que reabre CSRF. Los dominios son requisito de la Fase 2, no un "después": son propiedad del
   agente `deploy` en el manifiesto de portado.
3. **`localStorage` queda prohibido para credenciales.** `authStore` guarda el usuario (para pintar la UI),
   nunca un token. `lib/api.ts` deja de poner el header `Authorization` y pasa a `withCredentials: true`.
4. **El middleware de Next corta de verdad.** Sin cookie de sesión en una ruta protegida → `307` a
   `/login?next=<ruta>`. Con cookie en `/login` → `307` a `/dashboard`.
5. **El middleware no es la frontera de seguridad y se dice por escrito.** Solo mira presencia y `exp` del
   token; no verifica la firma (el secreto no se reparte al edge). La autoridad es el backend, que valida
   la firma en cada request. El middleware evita el parpadeo y el HTML filtrado; la autorización la hace la
   API.
6. **CSRF, que es lo que la cookie trae de regalo.** Pasar de `Bearer` a cookie hace que el navegador
   mande la credencial sola, así que el backend rechaza con **403** todo `POST`/`PATCH`/`PUT`/`DELETE`
   cuyo `Origin` no esté en la lista permitida. `SameSite=Lax` es la primera red; la comprobación de
   `Origin` es la segunda, y es la que funciona aunque un navegador viejo ignore `SameSite`.
7. **Logout invalida del lado del servidor**, no borrando la cookie del lado del cliente: `POST /auth/logout`
   revoca el refresh token y responde con `Set-Cookie` vencidas.

## Consecuencias

- **Gana:** un XSS deja de ser robo de sesión persistente. Sigue siendo grave (el script puede actuar como
  el usuario mientras la página está abierta), pero no se lleva el refresh token.
- **Gana:** las rutas protegidas nunca se renderizan para un anónimo, y las páginas dejan de tener que
  acordarse de estar adentro de un layout protegido.
- **Cuesta:** CSRF entra al modelo de amenaza. Se paga con el punto 6, que hay que sostener en cada
  endpoint mutador — está en el test, no en la memoria.
- **Cuesta:** el frontend ya no puede leer el `exp` para decidir cuándo refrescar. El refresh single-flight
  de `lib/api.ts` pasa a dispararse por el **401** de la API en vez de por el reloj; la lógica de
  single-flight (`lib/api.ts:15,117-131`) se conserva tal cual y es justamente lo que evita la tormenta de
  refresh cuando varias queries fallan a la vez.
- **Cuesta:** los dominios custom pasan a ser bloqueantes de la fase. Sin `api.carwash.app` no hay
  `SameSite=Lax`.
- **Recordar:** en desarrollo local (`http://localhost`) `Secure` impide que la cookie se setee. La config
  emite `Secure` solo fuera de `local`, y hay un test que verifica que **en producción siempre está**.

## Cómo se verifica

**Backend** — `backend/app/tests/api/test_auth_cookies.py`:

```python
async def test_el_login_devuelve_la_cookie_y_no_el_token_en_el_body(client):
    r = await client.post("/v1/auth/login", json={...})
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=Lax" in cookie
    assert "access_token" not in r.text and "refresh_token" not in r.text  # el body no filtra el token

async def test_sin_cookie_es_401(client):
    assert (await client.get("/v1/dummy-resources")).status_code == 401

async def test_origin_ajeno_en_un_metodo_mutador_es_403(client, cookies):
    r = await client.post("/v1/dummy-resources", json={...},
                          headers={"Origin": "https://evil.example"}, cookies=cookies)
    assert r.status_code == 403        # CSRF: la cookie viaja sola, el Origin no miente

async def test_en_produccion_la_cookie_siempre_es_secure(monkeypatch, client):
    monkeypatch.setenv("APP_ENV", "production")
    assert "Secure" in (await client.post("/v1/auth/login", json={...})).headers["set-cookie"]
```

**Frontend** — `frontend/src/__tests__/middleware.test.ts` (el test que Véktor no puede escribir, porque
su middleware no tiene comportamiento que testear):

```ts
it("redirige a /login cuando no hay cookie de sesión", () => {
  const res = middleware(new NextRequest("https://app.carwash.app/dashboard"));
  expect(res.status).toBe(307);
  expect(res.headers.get("location")).toBe("https://app.carwash.app/login?next=%2Fdashboard");
});

it("deja pasar con cookie de sesión", () => { /* … expect(res.headers.get("location")).toBeNull() */ });

it("saca de /login al que ya tiene sesión", () => { /* … 307 a /dashboard */ });
```

**Guarda contra la regresión** — `frontend/src/__tests__/meta/sin-token-en-localstorage.test.ts`:

```ts
it("ningún archivo de auth toca localStorage", () => {
  const sospechosos = glob.sync("src/{stores,lib,features/auth}/**/*.ts*")
    .filter((f) => /localStorage|sessionStorage/.test(fs.readFileSync(f, "utf8")));
  expect(sospechosos).toEqual([]);
});
```

Más una regla de ESLint (`no-restricted-globals` sobre `localStorage` en esas carpetas), para que el error
aparezca en el editor y no recién en el CI.

**Contra la URL desplegada**, en el checkpoint:

```bash
curl -si https://api.carwash.app/v1/auth/login -H 'Content-Type: application/json' \
     -d '{"email":"…","password":"…"}' | grep -i '^set-cookie:' | grep -q 'HttpOnly.*Secure.*SameSite=Lax'
curl -s -o /dev/null -w '%{http_code}\n' https://app.carwash.app/dashboard   # 307
```
