/**
 * @jest-environment node
 */
import fs from "node:fs";
import path from "node:path";
import { globSync } from "glob";

const ROOT = path.resolve(__dirname, "../../..");
const read = (f: string) => fs.readFileSync(path.join(ROOT, f), "utf8");

describe("ADR-0010: formularios", () => {
  it("ningún componente llama a .safeParse() a mano", () => {
    const culpables = globSync("src/**/*.tsx", { cwd: ROOT }).filter((f) =>
      read(f).includes(".safeParse("),
    );
    expect(culpables).toEqual([]);
  });

  it("todo formulario usa useForm con zodResolver", () => {
    const forms = globSync("src/**/*Form.tsx", { cwd: ROOT, nocase: false });
    expect(forms.length).toBeGreaterThanOrEqual(2);
    for (const f of forms) {
      const src = read(f);
      expect(src).toMatch(/useForm/);
      expect(src).toMatch(/zodResolver/);
    }
  });

  it("las dependencias de la ADR están declaradas", () => {
    const pkg = JSON.parse(read("package.json")) as { dependencies: Record<string, string> };
    for (const p of [
      "react-hook-form",
      "@hookform/resolvers",
      "class-variance-authority",
      "tailwind-merge",
      "zod",
    ]) {
      expect(pkg.dependencies[p]).toBeDefined();
    }
    expect(fs.existsSync(path.join(ROOT, "components.json"))).toBe(true);
  });
});
