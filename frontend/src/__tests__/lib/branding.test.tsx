import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  AxiosError,
  AxiosHeaders,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import { api } from "@/lib/api";
import {
  applyBranding,
  BRAND_VARS,
  brandingVariables,
  findPalette,
  fontStack,
  hexToHsl,
  logoFileError,
  MAX_LOGO_BYTES,
  readImageSize,
  SYSTEM_FONT_STACK,
  useTenantBranding,
  validateLogo,
} from "@/lib/branding";
import { brandingError } from "@/lib/errors";
import { FONT_VARIABLES, FONT_VARS } from "@/lib/fonts";
import {
  deleteLogo,
  getBrandingCatalog,
  suggestPalette,
  uploadLogo,
} from "@/services/branding.service";
import type { BrandCatalog, BrandPalette } from "@/types/api";

const color = (hex: string) => ({ hex, oklch: `oklch(${hex})` });
const palette = (code: string, primary = "#176a4e"): BrandPalette => ({
  code,
  name: code,
  background: color("#f9f3e8"),
  primary: color(primary),
  secondary: color("#e77b60"),
  on_primary: color("#ffffff"),
});
const catalog: BrandCatalog = {
  palettes: [palette("default", "#0a7db2"), palette("bosque")],
  fonts: [
    { code: "default", name: "Sistema", family: null, description: "" },
    { code: "inter", name: "Inter", family: "Inter", description: "" },
  ],
  default_palette: "default",
  default_font: "default",
};

function file(type: string, size: number, name = "logo.png"): File {
  const f = new File(["x"], name, { type });
  Object.defineProperty(f, "size", { value: size });
  return f;
}

describe("conversión de color al tema", () => {
  it("hex → triplet HSL de shadcn", () => {
    expect(hexToHsl("#0a7db2")).toBe("199 89% 37%");
    expect(hexToHsl("ffffff")).toBe("0 0% 100%");
    expect(hexToHsl("#000000")).toBe("0 0% 0%");
    expect(hexToHsl("#ff0000")).toBe("0 100% 50%");
    expect(hexToHsl("#00ff00")).toBe("120 100% 50%");
    expect(hexToHsl("#0000ff")).toBe("240 100% 50%");
    expect(hexToHsl("#ff00ff")).toBe("300 100% 50%");
    expect(hexToHsl("rojo")).toBeNull();
    expect(hexToHsl("#fff")).toBeNull();
  });
});

describe("tipografías", () => {
  it("default o desconocida → pila del sistema; del catálogo → su variable", () => {
    expect(fontStack("default")).toBe(SYSTEM_FONT_STACK);
    expect(fontStack(null)).toBe(SYSTEM_FONT_STACK);
    expect(fontStack("comic_sans")).toBe(SYSTEM_FONT_STACK);
    expect(fontStack("inter")).toBe(`var(--font-inter), ${SYSTEM_FONT_STACK}`);
  });

  it("declara una variable por cada fuente descargable", () => {
    expect(Object.keys(FONT_VARS).sort()).toEqual(["inter", "manrope", "outfit", "plus_jakarta"]);
    expect(typeof FONT_VARIABLES).toBe("string");
  });
});

describe("variables de marca", () => {
  it("default no pisa nada (queda el tema de globals.css)", () => {
    expect(brandingVariables(palette("default"), "default")).toEqual({});
    expect(brandingVariables(undefined, undefined)).toEqual({});
  });

  it("una paleta propia pisa fondo, primario, anillo y texto sobre el primario", () => {
    const vars = brandingVariables(palette("bosque"), "inter");
    expect(vars).toEqual({
      "--background": hexToHsl("#f9f3e8"),
      "--primary": hexToHsl("#176a4e"),
      "--ring": hexToHsl("#176a4e"),
      "--primary-foreground": "0 0% 100%",
      "--brand-secondary": "oklch(#e77b60)",
      "--font-brand": fontStack("inter"),
    });
  });

  it("un color roto deja el tema entero, nunca media paleta", () => {
    expect(brandingVariables(palette("rota", "nope"), "default")).toEqual({});
  });

  it("aplicar borra la marca anterior", () => {
    const root = document.createElement("div");
    applyBranding(root, brandingVariables(palette("bosque"), "inter"));
    expect(root.style.getPropertyValue("--primary")).toBe(hexToHsl("#176a4e"));
    applyBranding(root, {});
    for (const name of BRAND_VARS) expect(root.style.getPropertyValue(name)).toBe("");
  });

  it("findPalette por código", () => {
    expect(findPalette(catalog, "bosque")?.code).toBe("bosque");
    expect(findPalette(catalog, "rosa")).toBeUndefined();
    expect(findPalette(undefined, "bosque")).toBeUndefined();
  });
});

describe("validación del logo en el cliente", () => {
  it("tipo y peso", () => {
    expect(logoFileError(file("image/png", 1000))).toBeNull();
    expect(logoFileError(file("image/webp", MAX_LOGO_BYTES))).toBeNull();
    expect(logoFileError(file("image/svg+xml", 10))).toMatch(/PNG, JPG o WebP/);
    expect(logoFileError(file("image/gif", 10))).toMatch(/PNG, JPG o WebP/);
    expect(logoFileError(file("image/jpeg", MAX_LOGO_BYTES + 1))).toMatch(/2 MB/);
  });

  it("un tipo vacío no se rechaza (decide el servidor), pero el peso sí", () => {
    expect(logoFileError(file("", 1000, "logo"))).toBeNull();
    expect(logoFileError(file("", MAX_LOGO_BYTES + 1, "logo.png"))).toMatch(/2 MB/);
  });

  describe("tamaño en píxeles", () => {
    const original = (globalThis as { createImageBitmap?: unknown }).createImageBitmap;
    afterEach(() => {
      (globalThis as { createImageBitmap?: unknown }).createImageBitmap = original;
    });

    function bitmap(width: number, height: number) {
      (globalThis as { createImageBitmap?: unknown }).createImageBitmap = jest.fn(async () => ({
        width,
        height,
        close: jest.fn(),
      }));
    }

    it("sin createImageBitmap no se sabe (lo valida el servidor)", async () => {
      (globalThis as { createImageBitmap?: unknown }).createImageBitmap = undefined;
      expect(await readImageSize(file("image/png", 10))).toBeNull();
      expect(await validateLogo(file("image/png", 10))).toBeNull();
    });

    it("rechaza menos de 200 de lado", async () => {
      bitmap(199, 400);
      expect(await validateLogo(file("image/png", 10))).toMatch(/200×200/);
      bitmap(200, 200);
      expect(await validateLogo(file("image/png", 10))).toBeNull();
    });

    it("rechaza más de 4096 de lado", async () => {
      bitmap(4097, 300);
      expect(await validateLogo(file("image/png", 10))).toMatch(/4096 píxeles de lado/);
      bitmap(4096, 300);
      expect(await validateLogo(file("image/png", 10))).toBeNull();
    });

    it("si no decodifica, decide el servidor", async () => {
      (globalThis as { createImageBitmap?: unknown }).createImageBitmap = jest.fn(async () => {
        throw new Error("no");
      });
      expect(await readImageSize(file("image/png", 10))).toBeNull();
    });

    it("el tipo y el peso se miran antes de decodificar", async () => {
      bitmap(500, 500);
      expect(await validateLogo(file("text/plain", 10))).toMatch(/PNG/);
    });
  });
});

describe("errores del logo", () => {
  const config = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
  const err = (status: number, code: string) =>
    new AxiosError("x", "E", config, null, {
      status,
      data: { detail: { code, message: "x" } },
      statusText: "",
      headers: {},
      config,
    } as AxiosResponse);

  it("cada código tiene su mensaje", () => {
    expect(brandingError(err(422, "INVALID_IMAGE"))).toMatch(/no es una imagen/);
    expect(brandingError(err(422, "IMAGE_TOO_SMALL"))).toMatch(/200×200/);
    expect(brandingError(err(413, "FILE_TOO_LARGE"))).toMatch(
      /2 MB y medir hasta 4096 píxeles de lado/,
    );
    expect(brandingError(err(503, "STORAGE_UNAVAILABLE"))).toMatch(/no se pueden guardar/);
    expect(brandingError(err(403, "FORBIDDEN"))).toMatch(/permiso/);
  });
});

describe("branding.service (FASE-4-CONTRATO §4)", () => {
  const seen: InternalAxiosRequestConfig[] = [];
  beforeAll(() => {
    api.defaults.adapter = async (config) => {
      seen.push(config);
      return { data: { ok: true }, status: 200, statusText: "OK", headers: {}, config };
    };
  });

  it("habla con los endpoints y manda el archivo como multipart", async () => {
    const f = file("image/png", 10);
    await getBrandingCatalog();
    await uploadLogo(f);
    await suggestPalette(f);
    await deleteLogo();
    expect(seen.map((c) => `${c.method} ${c.url}`)).toEqual([
      "get /branding/catalog",
      "post /branding/logo",
      "post /branding/suggest-palette",
      "delete /branding/logo",
    ]);
    for (const c of seen.slice(1, 3)) {
      expect(c.data).toBeInstanceOf(FormData);
      expect((c.data as FormData).get("file")).toBe(f);
    }
  });
});

describe("useTenantBranding", () => {
  const root = document.documentElement;

  function wrapper(qc: QueryClient) {
    return function Wrapper({ children }: { children: React.ReactNode }) {
      return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
    };
  }

  function client(settings: { palette: string; font: string }) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(["branding", "catalog"], catalog);
    qc.setQueryData(["config", "settings"], settings);
    return qc;
  }

  afterEach(() => applyBranding(root, {}));

  it("pinta la marca del negocio en <html> y la saca al desmontar", async () => {
    const qc = client({ palette: "bosque", font: "inter" });
    const { unmount } = renderHook(() => useTenantBranding(true), { wrapper: wrapper(qc) });
    await waitFor(() => expect(root.style.getPropertyValue("--primary")).toBe(hexToHsl("#176a4e")));
    expect(root.style.getPropertyValue("--font-brand")).toBe(fontStack("inter"));
    unmount();
    expect(root.style.getPropertyValue("--primary")).toBe("");
  });

  it("repinta cuando cambia la configuración guardada", async () => {
    const qc = client({ palette: "bosque", font: "default" });
    renderHook(() => useTenantBranding(true), { wrapper: wrapper(qc) });
    await waitFor(() => expect(root.style.getPropertyValue("--primary")).not.toBe(""));
    qc.setQueryData(["config", "settings"], { palette: "default", font: "default" });
    await waitFor(() => expect(root.style.getPropertyValue("--primary")).toBe(""));
  });

  it("sin sesión no pide nada y deja el tema", () => {
    const qc = new QueryClient();
    renderHook(() => useTenantBranding(false), { wrapper: wrapper(qc) });
    expect(root.style.getPropertyValue("--primary")).toBe("");
    expect(qc.getQueryState(["config", "settings"])?.fetchStatus ?? "idle").toBe("idle");
  });
});
