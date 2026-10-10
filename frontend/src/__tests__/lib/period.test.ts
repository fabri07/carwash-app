import {
  addDays,
  BUSINESS_TIME_ZONE,
  formatDay,
  formatPeriodLabel,
  isIsoDate,
  mondayOf,
  resolvePeriod,
  todayInZone,
} from "@/lib/period";

// Jueves 9/10/2026 a las 01:30 UTC = miércoles 8/10 a las 22:30 en Córdoba (UTC-3).
const LATE_NIGHT = new Date("2026-10-09T01:30:00Z");
// Jueves 9/10/2026 al mediodía en Córdoba.
const NOON = new Date("2026-10-09T15:00:00Z");

describe("period (zona America/Argentina/Cordoba)", () => {
  it("'hoy' es el día calendario del negocio, no el de UTC", () => {
    expect(BUSINESS_TIME_ZONE).toBe("America/Argentina/Cordoba");
    expect(todayInZone(LATE_NIGHT)).toBe("2026-10-08");
    expect(resolvePeriod({ kind: "today" }, LATE_NIGHT)).toEqual({
      from: "2026-10-08",
      to: "2026-10-08",
    });
    expect(todayInZone(LATE_NIGHT, "UTC")).toBe("2026-10-09");
  });

  it("la semana va de lunes a domingo, completa (hay agenda futura)", () => {
    expect(resolvePeriod({ kind: "week" }, NOON)).toEqual({ from: "2026-10-05", to: "2026-10-11" });
    // Un domingo pertenece a la semana que empezó el lunes anterior.
    expect(mondayOf("2026-10-11")).toBe("2026-10-05");
    expect(mondayOf("2026-10-05")).toBe("2026-10-05");
  });

  it("el mes va del 1 al último día, también en febrero bisiesto", () => {
    expect(resolvePeriod({ kind: "month" }, NOON)).toEqual({
      from: "2026-10-01",
      to: "2026-10-31",
    });
    expect(resolvePeriod({ kind: "month" }, new Date("2028-02-10T15:00:00Z"))).toEqual({
      from: "2028-02-01",
      to: "2028-02-29",
    });
  });

  it("rango: se ordena si viene al revés y rechaza fechas inválidas", () => {
    expect(resolvePeriod({ kind: "range", from: "2026-10-20", to: "2026-10-01" })).toEqual({
      from: "2026-10-01",
      to: "2026-10-20",
    });
    expect(() => resolvePeriod({ kind: "range", from: "2026-02-30", to: "2026-03-01" })).toThrow(
      RangeError,
    );
  });

  it("helpers", () => {
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
    expect(isIsoDate("2026-10-09")).toBe(true);
    expect(isIsoDate("2026-13-01")).toBe(false);
    expect(isIsoDate("9/10/2026")).toBe(false);
    expect(formatDay("2026-10-09")).toBe("9/10/2026");
  });

  it("etiquetas", () => {
    expect(formatPeriodLabel({ kind: "today" })).toBe("Hoy");
    expect(formatPeriodLabel({ kind: "week" })).toBe("Esta semana");
    expect(formatPeriodLabel({ kind: "month" })).toBe("Este mes");
    expect(formatPeriodLabel({ kind: "range", from: "2026-10-01", to: "2026-10-09" })).toBe(
      "Del 1/10/2026 al 9/10/2026",
    );
    expect(formatPeriodLabel({ kind: "range", from: "2026-10-09", to: "2026-10-09" })).toBe(
      "9/10/2026",
    );
  });
});
