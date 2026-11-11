import type { InternalAxiosRequestConfig } from "axios";

import { api } from "@/lib/api";
import {
  getBodyTypeSizes,
  putBodyTypeSizes,
  vehicleColorsApi,
  vehicleMakesApi,
  vehicleModelsApi,
} from "@/services/vehicles.service";

describe("vehicles.service (FASE-4-CONTRATO §4, PR 4.4)", () => {
  const seen: InternalAxiosRequestConfig[] = [];
  beforeAll(() => {
    api.defaults.adapter = async (config) => {
      seen.push(config);
      return {
        data: { items: [{ body_type: "SUV", vehicle_size_id: null }], total: 1 },
        status: 200,
        statusText: "OK",
        headers: {},
        config,
      };
    };
  });
  beforeEach(() => {
    seen.length = 0;
  });

  it("habla con los endpoints del contrato", async () => {
    await vehicleMakesApi.list({ limit: 25, offset: 0 });
    await vehicleMakesApi.create({ name: "Marca" }, "k1");
    await vehicleModelsApi.update("m1", { hidden: true });
    await vehicleColorsApi.create({ name: "Lila" });
    expect(await getBodyTypeSizes()).toEqual([{ body_type: "SUV", vehicle_size_id: null }]);
    await putBodyTypeSizes({ items: [] });
    expect(seen.map((c) => `${c.method} ${c.url}`)).toEqual([
      "get /vehicle-makes",
      "post /vehicle-makes",
      "patch /vehicle-models/m1",
      "post /vehicle-colors",
      "get /body-type-sizes",
      "put /body-type-sizes",
    ]);
    expect(seen[1]!.headers["Idempotency-Key"]).toBe("k1");
    expect(seen[3]!.headers["Idempotency-Key"]).toBeUndefined();
  });

  it("no manda filtros vacíos y recorta la búsqueda", async () => {
    await vehicleModelsApi.list({
      limit: 25,
      offset: 0,
      q: "  hilux ",
      include_hidden: false,
      make_id: "",
    });
    expect(seen[0]!.params).toEqual({ limit: 25, offset: 0, q: "hilux" });
    await vehicleModelsApi.list({
      limit: 25,
      offset: 0,
      q: " ",
      include_hidden: true,
      make_id: "x",
    });
    expect(seen[1]!.params).toEqual({ limit: 25, offset: 0, include_hidden: true, make_id: "x" });
  });
});
