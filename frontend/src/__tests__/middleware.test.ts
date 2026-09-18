/**
 * @jest-environment node
 */
import { NextRequest } from "next/server";

import { config, cookieNames, hasSession, isJwtShaped, middleware, readJwtExp } from "@/middleware";

const APP = "https://app.carwash.app";

function jwt(claims: Record<string, unknown>): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b64({ alg: "HS256", typ: "JWT" })}.${b64(claims)}.firma-que-el-edge-no-verifica`;
}

function req(path: string, cookies: Record<string, string> = {}): NextRequest {
  const cookie = Object.entries(cookies)
    .map(([k, v]) => `${k}=${v}`)
    .join("; ");
  return new NextRequest(`${APP}${path}`, { headers: cookie ? { cookie } : {} });
}

const future = Math.floor(Date.now() / 1000) + 600;
const past = Math.floor(Date.now() / 1000) - 600;

describe("middleware (ADR-0009)", () => {
  it("redirige a /login cuando no hay cookie de sesión", () => {
    const res = middleware(req("/dashboard"));
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe(`${APP}/login?next=%2Fdashboard`);
  });

  it("conserva la query string en next", () => {
    const res = middleware(req("/dashboard/sub?x=1"));
    expect(res.headers.get("location")).toBe(`${APP}/login?next=%2Fdashboard%2Fsub%3Fx%3D1`);
  });

  it("deja pasar con cookie de sesión vigente", () => {
    const res = middleware(req("/dashboard", { access_token: jwt({ exp: future }) }));
    expect(res.headers.get("location")).toBeNull();
    expect(res.status).toBe(200);
  });

  it("con el access token vencido y sin refresh, corta", () => {
    const res = middleware(req("/dashboard", { access_token: jwt({ exp: past }) }));
    expect(res.status).toBe(307);
  });

  it("con el access vencido pero refresh presente, deja pasar (el 401 dispara el refresh)", () => {
    const res = middleware(
      req("/dashboard", { access_token: jwt({ exp: past }), refresh_token: jwt({ exp: future }) }),
    );
    expect(res.headers.get("location")).toBeNull();
  });

  it("saca de /login al que ya tiene sesión", () => {
    const res = middleware(req("/login", { access_token: jwt({ exp: future }) }));
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe(`${APP}/dashboard`);
  });

  it("no rebota desde /login?expired=1 aunque queden cookies (evita el loop)", () => {
    const res = middleware(req("/login?expired=1", { refresh_token: jwt({ exp: future }) }));
    expect(res.headers.get("location")).toBeNull();
  });

  it("no toca rutas públicas", () => {
    expect(middleware(req("/register")).headers.get("location")).toBeNull();
    expect(middleware(req("/")).headers.get("location")).toBeNull();
    // Un prefijo parecido no es una ruta protegida.
    expect(middleware(req("/dashboardx")).headers.get("location")).toBeNull();
  });

  it("el matcher excluye sw.js, manifest, íconos y estáticos", () => {
    const re = new RegExp(`^${config.matcher[0]}$`);
    expect(re.test("/dashboard")).toBe(true);
    for (const p of [
      "/sw.js",
      "/manifest.webmanifest",
      "/icons/icon-192.png",
      "/_next/static/a.js",
    ]) {
      expect(re.test(p)).toBe(false);
    }
  });
});

describe("forma de JWT (L4)", () => {
  it("una cookie cualquiera no cuenta como sesión", () => {
    const casos: Record<string, string>[] = [
      { access_token: "x" },
      { refresh_token: "x" },
      { refresh_token: "a.b" },
      { access_token: "a.b.c.d" },
      { access_token: "a..c" },
      { access_token: "a.b c.d" },
      { refresh_token: "cualquier-cosa-larga-y-opaca" },
    ];
    for (const cookies of casos) {
      expect(middleware(req("/dashboard", cookies)).status).toBe(307);
    }
  });

  it("tres segmentos base64url sin exp legible tampoco", () => {
    expect(hasSession(req("/", { access_token: "aaa.bbb.ccc" }))).toBe(false);
    expect(hasSession(req("/", { refresh_token: jwt({ sub: "x" }) }))).toBe(false);
  });

  it("un refresh vencido no sostiene la sesión", () => {
    expect(hasSession(req("/", { refresh_token: jwt({ exp: past }) }))).toBe(false);
  });

  it("isJwtShaped", () => {
    expect(isJwtShaped(jwt({ exp: 1 }))).toBe(true);
    expect(isJwtShaped(undefined)).toBe(false);
    expect(isJwtShaped("a.b")).toBe(false);
  });
});

describe("prefijo de cookie del contrato (COOKIE_NAME_PREFIX)", () => {
  const original = process.env.NEXT_PUBLIC_COOKIE_NAME_PREFIX;
  afterEach(() => {
    if (original === undefined) delete process.env.NEXT_PUBLIC_COOKIE_NAME_PREFIX;
    else process.env.NEXT_PUBLIC_COOKIE_NAME_PREFIX = original;
  });

  it("default sin prefijo (prod)", () => {
    delete process.env.NEXT_PUBLIC_COOKIE_NAME_PREFIX;
    expect(cookieNames()).toEqual({ access: "access_token", refresh: "refresh_token" });
  });

  it("en staging lee stg_access_token e ignora la cookie de prod", () => {
    process.env.NEXT_PUBLIC_COOKIE_NAME_PREFIX = "stg_";
    const token = jwt({ exp: future });
    expect(middleware(req("/dashboard", { stg_access_token: token })).status).toBe(200);
    expect(middleware(req("/dashboard", { access_token: token })).status).toBe(307);
    expect(middleware(req("/login", { stg_refresh_token: token })).status).toBe(307);
  });

  it("en prod una cookie de staging no abre sesión", () => {
    delete process.env.NEXT_PUBLIC_COOKIE_NAME_PREFIX;
    expect(middleware(req("/dashboard", { stg_access_token: jwt({ exp: future }) })).status).toBe(
      307,
    );
  });
});

describe("readJwtExp / hasSession", () => {
  it("lee exp sin verificar firma", () => {
    expect(readJwtExp(jwt({ exp: 123 }))).toBe(123);
  });

  it("devuelve null ante basura", () => {
    expect(readJwtExp("no-es-un-jwt")).toBeNull();
    expect(readJwtExp("a.%%%.c")).toBeNull();
    expect(readJwtExp("aaa.bbb.ccc")).toBeNull();
    expect(readJwtExp(jwt({ sub: "x" }))).toBeNull();
  });

  it("un token opaco no se da por sesión", () => {
    expect(hasSession(req("/", { access_token: "opaco" }))).toBe(false);
  });
});
