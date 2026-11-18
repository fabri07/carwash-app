/**
 * @jest-environment node
 */
/**
 * T3 (6.2) — El middleware corta de verdad las rutas nuevas: sin sesión, /agenda y /operacion/* van
 * a /login (con `next` sin datos del cliente). Un prefijo parecido no se confunde con uno protegido.
 */
import fs from "node:fs";
import path from "node:path";
import { NextRequest } from "next/server";

import { config, middleware, PROTECTED_PREFIXES } from "@/middleware";
import { RESERVED_SLUGS } from "@/lib/reservedSlugs";

const APP = "https://app.carwash.app";
const req = (p: string, cookie?: string) =>
  new NextRequest(`${APP}${p}`, { headers: cookie ? { cookie } : {} });

const jwt = (exp: number) => {
  const b = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b({ alg: "HS256" })}.${b({ exp })}.firma`;
};
const live = jwt(Math.floor(Date.now() / 1000) + 600);

const ROUTES = [
  "/agenda",
  "/agenda?date=2030-11-25",
  "/operacion",
  "/operacion/nuevo",
  "/operacion/turno-a-mano",
  "/operacion/trabajo-hecho",
  "/operacion/00000000-0000-0000-0000-000000000001",
  "/operacion/00000000-0000-0000-0000-000000000001/recepcion",
  "/operacion/turno/00000000-0000-0000-0000-0000000000b1?date=2030-11-25",
];

describe("middleware: rutas de operación", () => {
  it.each(ROUTES)("sin sesión %s redirige a /login con next", (route) => {
    const res = middleware(req(route));
    expect(res.status).toBe(307);
    const loc = new URL(res.headers.get("location")!);
    expect(loc.pathname).toBe("/login");
    expect(loc.searchParams.get("next")).toBe(route);
  });

  it.each(ROUTES)("con sesión vigente %s pasa", (route) => {
    const res = middleware(req(route, `access_token=${live}`));
    expect(res.status).toBe(200);
    expect(res.headers.get("location")).toBeNull();
  });

  it("con cookie basura (no es JWT) o vencida corta igual", () => {
    expect(middleware(req("/agenda", "access_token=x")).status).toBe(307);
    expect(middleware(req("/operacion/nuevo", "access_token=a.b.c")).status).toBe(307);
    const expired = jwt(Math.floor(Date.now() / 1000) - 5);
    expect(middleware(req("/operacion/x", `access_token=${expired}`)).status).toBe(307);
  });

  it("un prefijo parecido (/agendas, /operaciones) no es una ruta protegida ni se redirige por error", () => {
    expect(middleware(req("/agendas")).headers.get("location")).toBeNull();
    expect(middleware(req("/operaciones/x")).headers.get("location")).toBeNull();
  });

  it("el matcher alcanza a las rutas nuevas", () => {
    const re = new RegExp(`^${config.matcher[0]!.replace(/\(\?!/, "(?!")}$`);
    for (const r of ["/agenda", "/operacion/abc", "/operacion/turno/b1"]) expect(r).toMatch(re);
  });

  it("agenda y operación están en PROTECTED_PREFIXES, en RESERVED_SLUGS y cada carpeta de (protected) está cubierta", () => {
    expect(PROTECTED_PREFIXES).toEqual(expect.arrayContaining(["/agenda", "/operacion"]));
    expect(RESERVED_SLUGS).toEqual(expect.arrayContaining(["agenda", "operacion"]));
    const root = path.join(process.cwd(), "src/app/(protected)");
    for (const dir of fs.readdirSync(root, { withFileTypes: true })) {
      if (dir.isDirectory()) expect(PROTECTED_PREFIXES).toContain(`/${dir.name}`);
    }
  });

  it("el next de la redirección no lleva nombre, teléfono ni patente (solo ids y fecha)", () => {
    const res = middleware(req("/operacion/turno/b1?date=2030-11-25"));
    const next = new URL(res.headers.get("location")!).searchParams.get("next")!;
    expect(next).toBe("/operacion/turno/b1?date=2030-11-25");
    expect(next).not.toMatch(/AA000AA|Cliente|%2B54|\+54/);
  });
});
