/**
 * @jest-environment node
 */
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(__dirname, "../../..");

interface Icon {
  src: string;
  sizes: string;
  purpose?: string;
}
const m = JSON.parse(fs.readFileSync(path.join(ROOT, "public/manifest.webmanifest"), "utf8")) as {
  name: string;
  short_name: string;
  display: string;
  start_url: string;
  scope: string;
  theme_color: string;
  background_color: string;
  icons: Icon[];
};

describe("manifest (ADR-0011)", () => {
  it("es instalable según los criterios mínimos", () => {
    expect(m.name && m.short_name).toBeTruthy();
    expect(m.display).toBe("standalone");
    expect(m.start_url).toBe("/dashboard");
    expect(m.scope).toBe("/");
    expect(m.theme_color).toMatch(/^#/);
    expect(m.background_color).toMatch(/^#/);
    const tam = m.icons.map((i) => i.sizes);
    expect(tam).toEqual(expect.arrayContaining(["192x192", "512x512"]));
    expect(m.icons.some((i) => i.purpose?.includes("maskable"))).toBe(true);
  });

  it("los íconos que declara existen de verdad y son PNG", () => {
    for (const i of m.icons) {
      const file = path.join(ROOT, "public", i.src);
      expect(fs.existsSync(file)).toBe(true);
      const head = fs.readFileSync(file).subarray(0, 8);
      expect(head.equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))).toBe(true);
    }
  });

  it("el layout enlaza el manifiesto, el apple-touch-icon y viewport-fit=cover", () => {
    const layout = fs.readFileSync(path.join(ROOT, "src/app/layout.tsx"), "utf8");
    expect(layout).toMatch(/manifest:\s*"\/manifest\.webmanifest"/);
    expect(layout).toMatch(/apple-touch-icon\.png/);
    expect(layout).toMatch(/viewportFit:\s*"cover"/);
    expect(layout).toMatch(/export const viewport/);
    expect(fs.existsSync(path.join(ROOT, "public/icons/apple-touch-icon.png"))).toBe(true);
  });
});
