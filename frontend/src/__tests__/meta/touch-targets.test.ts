/**
 * @jest-environment node
 */
import fs from "node:fs";
import path from "node:path";
import { globSync } from "glob";

const ROOT = path.resolve(__dirname, "../../..");

/**
 * Piso de 44px para targets táctiles (la Fase 6 se opera con las manos mojadas).
 * Véktor usa `h-8 w-8` (32px) en el app shell; esto atrapa que vuelva a pasar
 * en layout, auth y la base de componentes.
 */
describe("targets táctiles ≥ 44px", () => {
  const archivos = globSync("src/{components/layout,components/ui,features/auth,app}/**/*.tsx", {
    cwd: ROOT,
  });

  it("ningún control usa alturas o anchos fijos por debajo de 44px (h-8/h-9/h-10, w-8…)", () => {
    const culpables: string[] = [];
    for (const f of archivos) {
      const src = fs.readFileSync(path.join(ROOT, f), "utf8");
      // Solo en líneas de <button>/<Link>/<a>/Button/Trigger/Item: los íconos
      // y avatares decorativos pueden medir menos.
      src.split("\n").forEach((line, i) => {
        if (/<(button|Link|a|Button)\b/.test(line) && /\b[hw]-(6|7|8|9|10)\b/.test(line)) {
          culpables.push(`${f}:${i + 1}`);
        }
      });
    }
    expect(culpables).toEqual([]);
  });

  it("Button, Input y los ítems de menú declaran el piso", () => {
    const read = (f: string) => fs.readFileSync(path.join(ROOT, f), "utf8");
    expect(read("src/components/ui/button.tsx")).toMatch(/min-h-touch/);
    expect(read("src/components/ui/input.tsx")).toMatch(/min-h-touch/);
    expect(read("src/components/ui/dropdown-menu.tsx")).toMatch(/min-h-touch/);
    expect(read("tailwind.config.ts")).toMatch(/touch:\s*"2\.75rem"/);
  });
});
