/**
 * @jest-environment node
 */
import fs from "node:fs";
import path from "node:path";

import { isReservedSlug, RESERVED_SLUGS } from "@/lib/reservedSlugs";

/**
 * FASE-5-CONTRATO §3.8 (B12): el turnero público vive en `/<slug>`. Cada carpeta
 * de primer nivel de `src/app/` (dentro de los grupos, que no suman segmento) y
 * cada entrada de `public/` que podría ser un slug tiene que estar reservada. La
 * lista es espejo de la del backend: `test_slugs_reservados.py` las compara.
 */
const ROOT = path.resolve(__dirname, "../../..");
const SLUG = /^[a-z0-9-]{3,40}$/;

function carpetas(dir: string): string[] {
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name);
}

function segmentosDePrimerNivel(): string[] {
  const app = path.join(ROOT, "src/app");
  return carpetas(app)
    .flatMap((nombre) => (nombre.startsWith("(") ? carpetas(path.join(app, nombre)) : [nombre]))
    .filter((n) => !n.startsWith("[") && !n.startsWith("_") && !n.startsWith("("));
}

describe("slugs reservados", () => {
  it("cada carpeta de primer nivel de app/ es un slug reservado", () => {
    const segmentos = segmentosDePrimerNivel();
    expect(segmentos).toEqual(expect.arrayContaining(["login", "configuracion", "catalogo"]));
    expect(segmentos.filter((s) => !RESERVED_SLUGS.includes(s))).toEqual([]);
  });

  it("cada entrada de public/ que podría ser un slug es reservada", () => {
    const publicos = fs.readdirSync(path.join(ROOT, "public")).filter((n) => SLUG.test(n));
    expect(publicos).toContain("icons");
    expect(publicos.filter((s) => !RESERVED_SLUGS.includes(s))).toEqual([]);
  });

  it("isReservedSlug ignora mayúsculas y espacios", () => {
    expect(isReservedSlug(" Login ")).toBe(true);
    expect(isReservedSlug("lavadero-centro")).toBe(false);
  });
});
