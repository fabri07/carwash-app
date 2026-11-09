import { act, renderHook } from "@testing-library/react";
import { AxiosError, AxiosHeaders, type InternalAxiosRequestConfig } from "axios";

import { durationSummary, priceSummary, skipReasonLabel } from "@/features/catalogo/format";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { serviceError } from "@/lib/errors";
import { formatDuration } from "@/lib/time";
import {
  isSearchable,
  priceRowSchema,
  serviceCreateSchema,
  toPriceItems,
} from "@/validation/services";

const config = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
function apiError(status: number, code: string, message = "x") {
  return new AxiosError("x", "ERR", config, null, {
    status,
    data: { detail: { code, message } },
    statusText: "",
    headers: {},
    config,
  });
}

describe("formatDuration", () => {
  it.each([
    [0, "0 min"],
    [45, "45 min"],
    [60, "1 h"],
    [90, "1 h 30 min"],
    [120, "2 h"],
    [1440, "1 d"],
    [1500, "1 d 1 h"],
    [1470, "1 d 30 min"],
    [1530, "1 d 1 h 30 min"],
    [20160, "14 d"],
  ])("%i min → %s", (min, text) => expect(formatDuration(min)).toBe(text));

  it("lo que no es una duración válida se muestra como raya", () => {
    expect(formatDuration(-1)).toBe("—");
    expect(formatDuration(Number.NaN)).toBe("—");
  });
});

describe("resúmenes del catálogo", () => {
  const price = (cents: number, min: number) => ({
    id: "p",
    vehicle_size_id: "s",
    price_cents: cents,
    duration_min: min,
  });

  it("precio fijo: un valor o el rango entre tamaños", () => {
    expect(priceSummary({ pricing_mode: "PRECIO_FIJO", prices: [price(100_000, 30)] })).toBe(
      "$ 1.000,00",
    );
    expect(
      priceSummary({
        pricing_mode: "PRECIO_FIJO",
        prices: [price(300_000, 30), price(100_000, 60)],
      }),
    ).toBe("$ 1.000,00 – $ 3.000,00");
  });

  it("a cotizar: 'Desde' el más bajo; sin precios, lo dice", () => {
    expect(
      priceSummary({ pricing_mode: "A_COTIZAR", prices: [price(300_000, 30), price(100_000, 60)] }),
    ).toBe("Desde $ 1.000,00");
    expect(priceSummary({ pricing_mode: "PRECIO_FIJO", prices: [] })).toBe("Sin precios");
  });

  it("duración: valor, rango o raya", () => {
    expect(durationSummary({ prices: [price(1, 30), price(1, 30)] })).toBe("30 min");
    expect(durationSummary({ prices: [price(1, 30), price(1, 90)] })).toBe("30 min – 1 h 30 min");
    expect(durationSummary({ prices: [] })).toBe("—");
  });

  it("motivos de salteo de la plantilla", () => {
    expect(skipReasonLabel("GLOSSARY_CODE")).toBe("ya tenías este servicio");
    expect(skipReasonLabel("NAME")).toBe("ya tenías un servicio propio con ese nombre");
    expect(skipReasonLabel("OTRO")).toBe("OTRO");
  });
});

describe("validation/services", () => {
  it("isSearchable: sin letras ni números no se puede buscar", () => {
    expect(isSearchable("Lavado")).toBe(true);
    expect(isSearchable("Cerámico")).toBe(true);
    expect(isSearchable("360")).toBe(true);
    expect(isSearchable("洗車")).toBe(false);
    expect(isSearchable("---")).toBe(false);
  });

  it("precio y duración: enteros positivos con tope", () => {
    const ok = { vehicle_size_id: "s", price_cents: 100, duration_min: "30" };
    expect(priceRowSchema.safeParse(ok).success).toBe(true);
    expect(priceRowSchema.safeParse({ ...ok, price_cents: 0 }).success).toBe(false);
    expect(priceRowSchema.safeParse({ ...ok, price_cents: 10_000_000_001 }).success).toBe(false);
    expect(priceRowSchema.safeParse({ ...ok, price_cents: 1.5 }).success).toBe(false);
    expect(priceRowSchema.safeParse({ ...ok, price_cents: null }).success).toBe(false);
    expect(priceRowSchema.safeParse({ ...ok, duration_min: "0" }).success).toBe(false);
    expect(priceRowSchema.safeParse({ ...ok, duration_min: "20161" }).success).toBe(false);
    expect(priceRowSchema.safeParse({ ...ok, duration_min: "1,5" }).success).toBe(false);
    expect(priceRowSchema.safeParse({ ...ok, duration_min: "20160" }).success).toBe(true);
  });

  it("el alta pide al menos un tamaño y un nombre dentro del largo", () => {
    const base = { name: "Lavado", category_id: "", notes: "", pricing_mode: "PRECIO_FIJO" };
    expect(serviceCreateSchema.safeParse({ ...base, prices: [] }).success).toBe(false);
    const row = { vehicle_size_id: "s", price_cents: 100, duration_min: "30" };
    expect(serviceCreateSchema.safeParse({ ...base, prices: [row] }).success).toBe(true);
    expect(
      serviceCreateSchema.safeParse({ ...base, name: "x".repeat(81), prices: [row] }).success,
    ).toBe(false);
    expect(
      serviceCreateSchema.safeParse({ ...base, notes: "x".repeat(501), prices: [row] }).success,
    ).toBe(false);
  });

  it("toPriceItems pasa los minutos a número", () => {
    expect(toPriceItems([{ vehicle_size_id: "s", price_cents: 100, duration_min: "90" }])).toEqual([
      { vehicle_size_id: "s", price_cents: 100, duration_min: 90 },
    ]);
  });
});

describe("serviceError", () => {
  it("IN_USE explica qué retiene al servicio", () => {
    expect(serviceError(apiError(409, "IN_USE")).message).toMatch(
      /turnos, cotizaciones o trabajos/,
    );
  });

  it("cualquier 422 en contexto de precios pide recargar y agrega el detalle del servidor", () => {
    const e = serviceError(apiError(422, "VALIDATION_ERROR", "lo que sea en inglés"), "prices");
    expect(e.field).toBeNull();
    expect(e.message).toMatch(/Recargá la página/);
    expect(e.message).toMatch(/Detalle del servidor: lo que sea en inglés/);
    expect(serviceError(apiError(422, "VALIDATION_ERROR", ""), "prices").message).not.toMatch(
      /Detalle/,
    );
  });

  it("NAME_TAKEN cae en el campo nombre y el resto en el mensaje genérico", () => {
    expect(serviceError(apiError(409, "NAME_TAKEN")).field).toBe("name");
    expect(serviceError(apiError(422, "VALIDATION_ERROR", "otra cosa")).message).toBe(
      "Revisá los datos ingresados.",
    );
    expect(serviceError(new Error("x")).message).toMatch(/inesperado/);
  });
});

describe("useDebouncedValue", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it("emite el valor recién tras el retardo sin cambios", () => {
    const { result, rerender } = renderHook(({ v }) => useDebouncedValue(v, 300), {
      initialProps: { v: "a" },
    });
    rerender({ v: "ab" });
    rerender({ v: "abc" });
    act(() => {
      jest.advanceTimersByTime(299);
    });
    expect(result.current).toBe("a");
    act(() => {
      jest.advanceTimersByTime(1);
    });
    expect(result.current).toBe("abc");
  });
});
