import type { InternalAxiosRequestConfig } from "axios";

import { api } from "@/lib/api";
import {
  businessHoursApi,
  getSettings,
  patchSettings,
  paymentMethodsApi,
  resourcesApi,
  vehicleSizesApi,
} from "@/services/config.service";

describe("config.service (FASE-4-CONTRATO §4)", () => {
  const seen: InternalAxiosRequestConfig[] = [];
  beforeAll(() => {
    api.defaults.adapter = async (config) => {
      seen.push(config);
      return { data: { ok: true }, status: 200, statusText: "OK", headers: {}, config };
    };
  });

  it("habla con los endpoints del contrato", async () => {
    await getSettings();
    await patchSettings({ deposit_bps: 2000 });
    await vehicleSizesApi.list({ limit: 25, offset: 0 });
    await resourcesApi.create({ name: "P1", sort_order: 0 }, "k1");
    await businessHoursApi.update("h1", { closes_at: "20:00" });
    await paymentMethodsApi.remove("m1");
    expect(seen.map((c) => `${c.method} ${c.url}`)).toEqual([
      "get /settings",
      "patch /settings",
      "get /vehicle-sizes",
      "post /resources",
      "patch /business-hours/h1",
      "delete /payment-methods/m1",
    ]);
  });

  it("los alta mandan Idempotency-Key solo si hay clave", async () => {
    seen.length = 0;
    await resourcesApi.create({ name: "P1", sort_order: 0 }, "clave-1");
    await resourcesApi.create({ name: "P2", sort_order: 0 });
    expect(seen[0]!.headers.get("Idempotency-Key")).toBe("clave-1");
    expect(seen[1]!.headers.get("Idempotency-Key")).toBeFalsy();
  });
});
