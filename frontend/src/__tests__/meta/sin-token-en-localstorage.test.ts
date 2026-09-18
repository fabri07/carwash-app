/**
 * @jest-environment node
 */
import fs from "node:fs";
import path from "node:path";
import { globSync } from "glob";

const ROOT = path.resolve(__dirname, "../../..");

describe("ADR-0009: credenciales fuera del storage del navegador", () => {
  it("ningún archivo de auth toca localStorage", () => {
    const archivos = globSync("src/{stores,lib,features/auth}/**/*.{ts,tsx}", { cwd: ROOT });
    expect(archivos.length).toBeGreaterThan(0);
    const sospechosos = archivos.filter((f) =>
      /localStorage|sessionStorage/.test(fs.readFileSync(path.join(ROOT, f), "utf8")),
    );
    expect(sospechosos).toEqual([]);
  });

  it("authStore persiste solo usuario y tenant", () => {
    const src = fs.readFileSync(path.join(ROOT, "src/stores/authStore.ts"), "utf8");
    expect(src).toMatch(
      /partialize:\s*\(state\)\s*=>\s*\(\{\s*user:\s*state\.user,\s*tenant:\s*state\.tenant\s*\}\)/,
    );
    expect(src).not.toMatch(/refreshToken|accessToken|\btoken:/);
  });

  it("lib/api.ts no arma un header Authorization", () => {
    const src = fs.readFileSync(path.join(ROOT, "src/lib/api.ts"), "utf8");
    expect(src).not.toMatch(/headers\.Authorization|Bearer /);
    expect(src).toMatch(/withCredentials:\s*true/);
  });
});
