/**
 * @jest-environment node
 */
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(__dirname, "../../..");

describe("el middleware está donde Next lo busca", () => {
  it("con directorio src/, el middleware vive en src/ y no en la raíz", () => {
    // Un middleware.ts en la raíz se ignora sin error: la protección de rutas
    // desaparecería del build con todos los tests unitarios en verde.
    expect(fs.existsSync(path.join(ROOT, "src/middleware.ts"))).toBe(true);
    expect(fs.existsSync(path.join(ROOT, "middleware.ts"))).toBe(false);
  });
});
