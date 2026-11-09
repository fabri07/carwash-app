import type { InternalAxiosRequestConfig } from "axios";

import { api } from "@/lib/api";
import {
  applyTemplate,
  getTemplate,
  serviceCategoriesApi,
  servicesApi,
} from "@/services/catalog.service";

describe("catalog.service (FASE-4-CONTRATO §4 y §11.5)", () => {
  const seen: InternalAxiosRequestConfig[] = [];
  beforeAll(() => {
    api.defaults.adapter = async (config) => {
      seen.push(config);
      return { data: { ok: true }, status: 200, statusText: "OK", headers: {}, config };
    };
  });
  beforeEach(() => {
    seen.length = 0;
  });

  it("habla con los endpoints del contrato", async () => {
    await serviceCategoriesApi.list({ limit: 25, offset: 0 });
    await serviceCategoriesApi.create({ name: "Lavado", sort_order: 0 }, "k1");
    await servicesApi.update("s1", { notes: null });
    await servicesApi.remove("s1");
    await servicesApi.replacePrices("s1", {
      prices: [{ vehicle_size_id: "z", price_cents: 100, duration_min: 30 }],
    });
    await getTemplate("lavadero");
    await applyTemplate({ kind: "detailing", items: [] });
    expect(seen.map((c) => `${c.method} ${c.url}`)).toEqual([
      "get /service-categories",
      "post /service-categories",
      "patch /services/s1",
      "delete /services/s1",
      "put /services/s1/prices",
      "get /templates/lavadero",
      "post /templates/apply",
    ]);
  });

  it("el alta manda Idempotency-Key; aplicar plantilla no (ya es idempotente)", async () => {
    await servicesApi.create(
      {
        name: "A",
        pricing_mode: "PRECIO_FIJO",
        prices: [{ vehicle_size_id: "z", price_cents: 100, duration_min: 30 }],
      },
      "clave-1",
    );
    await applyTemplate({ kind: "lavadero", items: [] });
    expect(seen[0]!.headers.get("Idempotency-Key")).toBe("clave-1");
    expect(seen[1]!.headers.get("Idempotency-Key")).toBeFalsy();
  });

  it("la lista manda q y category_id solo si tienen valor", async () => {
    await servicesApi.list({ limit: 25, offset: 0, q: "  ", category_id: "" });
    await servicesApi.list({ limit: 25, offset: 50, q: " cera ", category_id: "c1" });
    expect(seen[0]!.params).toEqual({ limit: 25, offset: 0 });
    expect(seen[1]!.params).toEqual({ limit: 25, offset: 50, q: "cera", category_id: "c1" });
  });

  it("el payload del PUT de precios viaja tal cual", async () => {
    await servicesApi.replacePrices("s1", {
      pricing_mode: "A_COTIZAR",
      prices: [{ vehicle_size_id: "z", price_cents: 100, duration_min: 30 }],
    });
    expect(JSON.parse(seen[0]!.data as string)).toEqual({
      pricing_mode: "A_COTIZAR",
      prices: [{ vehicle_size_id: "z", price_cents: 100, duration_min: 30 }],
    });
  });
});
