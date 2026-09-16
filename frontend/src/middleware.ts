import { NextResponse, type NextRequest } from "next/server";

/**
 * Middleware que corta de verdad (ADR-0009 §4).
 *
 * Vive en `src/middleware.ts`, no en la raíz: con directorio `src/`, Next
 * IGNORA en silencio un `middleware.ts` en la raíz del proyecto (el build no lo
 * compila y `middleware-manifest.json` queda vacío). En Véktor está en la raíz
 * y nadie lo notó porque era un no-op. `meta/middleware-compilado.test.ts`
 * evita que vuelva a pasar.
 *
 * El de Véktor era un no-op declarado porque el token vivía en el storage del
 * navegador y el edge no lo veía. Acá la sesión viaja en cookie `HttpOnly`, así
 * que se puede decidir antes de renderizar: el anónimo nunca recibe el HTML de
 * una ruta protegida.
 *
 * ADR-0009 §5 — ESTO NO ES LA FRONTERA DE SEGURIDAD. Solo mira que la cookie
 * tenga forma de JWT y que su `exp` no haya pasado; no verifica la firma (el secreto no se
 * reparte al edge). Un token falsificado pasa este filtro y lo rechaza el
 * backend, que es la autoridad en cada request.
 */

/**
 * Nombres de cookie del contrato con el backend: `COOKIE_NAME_PREFIX` +
 * `access_token` / `refresh_token` ("" en prod, "stg_" en staging, para que una
 * sesión de staging no se confunda con la de prod si comparten dominio padre).
 * `NEXT_PUBLIC_*` se inlinea en el build: cada ambiente compila con el suyo.
 */
export function cookieNames(prefix = process.env.NEXT_PUBLIC_COOKIE_NAME_PREFIX ?? "") {
  return { access: `${prefix}access_token`, refresh: `${prefix}refresh_token` };
}

const PROTECTED_PREFIXES = ["/dashboard"];
const AUTH_PAGES = ["/login", "/register"];

function matches(pathname: string, prefixes: string[]): boolean {
  return prefixes.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

/** Forma de JWT: tres segmentos base64url no vacíos. No dice nada de la firma. */
const JWT_SHAPE = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

export function isJwtShaped(token: string | undefined): token is string {
  return typeof token === "string" && JWT_SHAPE.test(token);
}

/** Lee el `exp` del payload sin verificar la firma. `null` si no es un JWT legible. */
export function readJwtExp(token: string): number | null {
  if (!isJwtShaped(token)) return null;
  const payload = token.split(".")[1]!;
  try {
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    const claims = JSON.parse(atob(padded)) as { exp?: unknown };
    return typeof claims.exp === "number" ? claims.exp : null;
  } catch {
    return null;
  }
}

function isLiveJwt(token: string | undefined, nowSeconds: number): boolean {
  if (!isJwtShaped(token)) return false;
  const exp = readJwtExp(token);
  return exp !== null && exp > nowSeconds;
}

/**
 * ¿Parece haber sesión? Un access o un refresh con forma de JWT, `exp`
 * legible y no vencido. `access_token=x` o un refresh cualquiera ya no pasan.
 *
 * Ojo con lo que el edge realmente ve: el backend emite el refresh con
 * `Path=/v1/auth` y el access con `max_age` = su vencimiento, así que en la
 * práctica a una ruta de la app solo llega el access token mientras está vivo.
 * Vencido el access, el middleware manda a /login y es /login quien intenta un
 * refresh silencioso (`LoginPageClient`) antes de pedir credenciales. La rama
 * del refresh queda por si el backend algún día lo emite con `Path=/`.
 */
export function hasSession(
  request: NextRequest,
  nowSeconds = Date.now() / 1000,
  names = cookieNames(),
): boolean {
  return (
    isLiveJwt(request.cookies.get(names.access)?.value, nowSeconds) ||
    isLiveJwt(request.cookies.get(names.refresh)?.value, nowSeconds)
  );
}

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const session = hasSession(request);

  if (matches(pathname, PROTECTED_PREFIXES) && !session) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = `?next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(url, 307);
  }

  // `?expired=1` lo pone `lib/api.ts` cuando el refresh falló: las cookies
  // pueden seguir presentes (revocadas del lado del servidor) y rebotar de
  // /login a /dashboard armaría un loop login → dashboard → 401 → login.
  const expired = request.nextUrl.searchParams.has("expired");
  if (matches(pathname, AUTH_PAGES) && session && !expired) {
    const url = request.nextUrl.clone();
    url.pathname = "/dashboard";
    url.search = "";
    return NextResponse.redirect(url, 307);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|sw.js|manifest.webmanifest|icons/).*)"],
};
