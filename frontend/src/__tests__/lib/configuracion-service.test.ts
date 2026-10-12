import type { InternalAxiosRequestConfig } from "axios";

import { api } from "@/lib/api";
import {
  createBusinessHour,
  createPaymentMethod,
  createResource,
  createVehicleSize,
  deleteBusinessHour,
  deletePaymentMethod,
  deleteResource,
  deleteVehicleSize,
  getSettings,
  listBusinessHours,
  listPaymentMethods,
  listResources,
  listVehicleSizes,
  updateBusinessHour,
  updatePaymentMethod,
  updateResource,
  updateSettings,
  updateVehicleSize,
} from "@/services/configuracion.service";

describe("configuracion.service (FASE-4-CONTRATO §4, PR 4.2)", () => {
  const seen: InternalAxiosRequestConfig[] = [];
  beforeAll(() => {
    api.defaults.adapter = async (config) => {
      seen.push(config);
      return { data: { ok: true }, status: 200, statusText: "OK", headers: {}, config };
    };
  });

  it("habla con los endpoints del contrato", async () => {
    await getSettings();
    await updateSettings({ deposit_enabled: true, deposit_bps: 2500 });
    await listVehicleSizes({ limit: 25, offset: 0 });
    await createVehicleSize({ code: "CHICO", label: "Chico", sort_order: 0 }, "k1");
    await updateVehicleSize("v1", { label: "Auto chico" });
    await deleteVehicleSize("v1");
    await listResources({ limit: 25, offset: 25 });
    await createResource({ name: "Box 1", sort_order: 0 });
    await updateResource("r1", { name: "Box 2" });
    await deleteResource("r1");
    await listBusinessHours({ limit: 25, offset: 0 });
    await createBusinessHour({ weekday: 1, opens_at: "09:00", closes_at: "13:00" }, "k2");
    await updateBusinessHour("h1", { closes_at: "14:00" });
    await deleteBusinessHour("h1");
    await listPaymentMethods({ limit: 25, offset: 0 });
    await createPaymentMethod(
      {
        code: "EFECTIVO",
        label: "Efectivo",
        commission_bps: 0,
        for_income: true,
        for_expense: false,
      },
      "k3",
    );
    await updatePaymentMethod("m1", { settlement_account: null });
    await deletePaymentMethod("m1");

    expect(seen.map((c) => `${c.method} ${c.url}`)).toEqual([
      "get /settings",
      "patch /settings",
      "get /vehicle-sizes",
      "post /vehicle-sizes",
      "patch /vehicle-sizes/v1",
      "delete /vehicle-sizes/v1",
      "get /resources",
      "post /resources",
      "patch /resources/r1",
      "delete /resources/r1",
      "get /business-hours",
      "post /business-hours",
      "patch /business-hours/h1",
      "delete /business-hours/h1",
      "get /payment-methods",
      "post /payment-methods",
      "patch /payment-methods/m1",
      "delete /payment-methods/m1",
    ]);
    expect(JSON.parse(seen[1]!.data as string)).toEqual({
      deposit_enabled: true,
      deposit_bps: 2500,
    });
    expect(seen[6]!.params).toEqual({ limit: 25, offset: 25 });
    // El alta manda Idempotency-Key solo si se la pasan.
    expect(seen[3]!.headers["Idempotency-Key"]).toBe("k1");
    expect(seen[7]!.headers["Idempotency-Key"]).toBeUndefined();
    expect(seen[11]!.headers["Idempotency-Key"]).toBe("k2");
    expect(seen[15]!.headers["Idempotency-Key"]).toBe("k3");
    // `null` explícito viaja: es lo que borra la cuenta de destino.
    expect(JSON.parse(seen[16]!.data as string)).toEqual({ settlement_account: null });
  });
});
