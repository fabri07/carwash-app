import {
  cancelBooking,
  createBooking,
  getAvailability,
  getBusiness,
  getCatalog,
  lookupBooking,
  publicApi,
} from "@/services/public.service";

describe("cliente del turnero público", () => {
  let get: jest.SpyInstance;
  let post: jest.SpyInstance;

  beforeEach(() => {
    get = jest.spyOn(publicApi, "get").mockResolvedValue({ data: "ok" });
    post = jest.spyOn(publicApi, "post").mockResolvedValue({ data: "ok" });
  });

  afterEach(() => jest.restoreAllMocks());

  it("no manda cookies ni tiene interceptores de sesión", () => {
    expect(publicApi.defaults.withCredentials).toBe(false);
    expect(publicApi.defaults.baseURL).toMatch(/\/v1\/public$/);
  });

  it("lee el negocio, el catálogo y la disponibilidad por slug", async () => {
    await expect(getBusiness("lavadero norte")).resolves.toBe("ok");
    expect(get).toHaveBeenLastCalledWith("/lavadero%20norte");
    await getCatalog("lavadero-norte");
    expect(get).toHaveBeenLastCalledWith("/lavadero-norte/catalog");
    await getAvailability("lavadero-norte", {
      date: "2026-11-23",
      vehicleSizeId: "auto",
      serviceIds: ["a", "b"],
    });
    const [ruta, { params }] = get.mock.calls.at(-1)!;
    expect(ruta).toBe("/lavadero-norte/availability");
    expect((params as URLSearchParams).toString()).toBe(
      "date=2026-11-23&vehicle_size_id=auto&service_ids=a&service_ids=b",
    );
  });

  it("reserva con Idempotency-Key, busca y cancela", async () => {
    const reserva = {
      vehicle_size_id: "auto",
      service_ids: ["a"],
      start_at: "2026-11-23T13:00:00Z",
      name: "Cliente Web",
      phone: "3510000001",
      accept_policy: true,
    };
    await createBooking("lavadero-norte", reserva, "clave-1");
    expect(post).toHaveBeenLastCalledWith("/lavadero-norte/bookings", reserva, {
      headers: { "Idempotency-Key": "clave-1" },
    });
    await lookupBooking("lavadero-norte", { code: "TUR-K7M2QX", phone: "3510000001" });
    expect(post).toHaveBeenLastCalledWith("/lavadero-norte/bookings/lookup", {
      code: "TUR-K7M2QX",
      phone: "3510000001",
    });
    await cancelBooking("lavadero-norte", { code: "TUR-K7M2QX", phone: "3510000001" });
    expect(post).toHaveBeenLastCalledWith("/lavadero-norte/bookings/cancel", {
      code: "TUR-K7M2QX",
      phone: "3510000001",
    });
  });
});
