/**
 * @jest-environment node
 */
import nextConfig from "../../../next.config";

type HeaderRule = { source: string; headers: { key: string; value: string }[] };

describe("cabeceras de next.config.ts", () => {
  let rules: HeaderRule[];

  beforeAll(async () => {
    const cfg = nextConfig as { headers?: () => Promise<HeaderRule[]> };
    expect(cfg.headers).toBeDefined();
    rules = await cfg.headers!();
  });

  const get = (source: string, key: string) =>
    rules
      .find((r) => r.source === source)
      ?.headers.find((h) => h.key.toLowerCase() === key.toLowerCase())?.value;

  it("/sw.js se sirve con Cache-Control: no-store (ADR-0011 §5)", () => {
    expect(get("/sw.js", "Cache-Control")).toMatch(/no-store/);
  });

  it("toda la app lleva CSP sin framing ni plugins", () => {
    const csp = get("/:path*", "Content-Security-Policy");
    expect(csp).toMatch(/default-src 'self'/);
    expect(csp).toMatch(/frame-ancestors 'none'/);
    expect(csp).toMatch(/object-src 'none'/);
    expect(csp).toMatch(/connect-src 'self' \S+/);
    expect(get("/:path*", "X-Content-Type-Options")).toBe("nosniff");
  });
});
