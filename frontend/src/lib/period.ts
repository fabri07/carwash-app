/**
 * Períodos para los filtros de listados (hoy, semana, mes, rango). Portado de
 * Véktor (`lib/period.ts`) con dos cambios:
 *
 * 1. **La zona es explícita.** Véktor calculaba con la hora LOCAL del
 *    navegador ("asumido" Buenos Aires): un celular con otra zona, o un test en
 *    CI en UTC, corría el día. Acá "hoy" es el día calendario en
 *    `America/Argentina/Cordoba`, lea quien lea.
 * 2. **Semana y mes son completos.** En Véktor "esta semana" terminaba hoy
 *    (ventas pasadas); acá hay agenda futura, así que la semana va de lunes a
 *    domingo y el mes del 1 al último día.
 *
 * Las fechas viajan como `YYYY-MM-DD` (día calendario del negocio). La
 * aritmética de días se hace en UTC sobre esa fecha, que no tiene horario de
 * verano que la corra.
 */

export const BUSINESS_TIME_ZONE = "America/Argentina/Cordoba";

export type PeriodValue =
  | { kind: "today" }
  | { kind: "week" }
  | { kind: "month" }
  | { kind: "range"; from: string; to: string };

export type PeriodKind = PeriodValue["kind"];

export interface DateRange {
  /** Primer día incluido, `YYYY-MM-DD`. */
  from: string;
  /** Último día incluido, `YYYY-MM-DD`. */
  to: string;
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

const pad = (n: number): string => String(n).padStart(2, "0");

/** El día calendario de `now` en la zona del negocio, como `YYYY-MM-DD`. */
export function todayInZone(now: Date = new Date(), timeZone = BUSINESS_TIME_ZONE): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function isIsoDate(value: string): boolean {
  if (!ISO_DATE.test(value)) return false;
  const [y, m, d] = value.split("-").map(Number) as [number, number, number];
  const date = new Date(Date.UTC(y, m - 1, d));
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d;
}

function toUtcDate(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number) as [number, number, number];
  return new Date(Date.UTC(y, m - 1, d));
}

function fromUtcDate(d: Date): string {
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}

export function addDays(iso: string, n: number): string {
  const d = toUtcDate(iso);
  d.setUTCDate(d.getUTCDate() + n);
  return fromUtcDate(d);
}

/** Lunes de la semana (lunes a domingo) que contiene `iso`. */
export function mondayOf(iso: string): string {
  const day = toUtcDate(iso).getUTCDay(); // 0 = domingo
  return addDays(iso, day === 0 ? -6 : 1 - day);
}

/**
 * Rango de días de un período. Un rango con `from` posterior a `to` se
 * devuelve ordenado: el filtro no tiene que fallar porque el usuario eligió
 * las fechas al revés.
 */
export function resolvePeriod(value: PeriodValue, now: Date = new Date()): DateRange {
  const today = todayInZone(now);
  switch (value.kind) {
    case "today":
      return { from: today, to: today };
    case "week": {
      const monday = mondayOf(today);
      return { from: monday, to: addDays(monday, 6) };
    }
    case "month": {
      const first = `${today.slice(0, 8)}01`;
      const [y, m] = first.split("-").map(Number) as [number, number];
      const last = fromUtcDate(new Date(Date.UTC(y, m, 0)));
      return { from: first, to: last };
    }
    case "range": {
      if (!isIsoDate(value.from) || !isIsoDate(value.to)) {
        throw new RangeError(`Rango inválido: ${value.from} – ${value.to}`);
      }
      return value.from <= value.to
        ? { from: value.from, to: value.to }
        : { from: value.to, to: value.from };
    }
  }
}

/** `2026-10-09` → `9/10/2026`. */
export function formatDay(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number) as [number, number, number];
  return `${d}/${m}/${y}`;
}

const LABELS: Record<Exclude<PeriodKind, "range">, string> = {
  today: "Hoy",
  week: "Esta semana",
  month: "Este mes",
};

export function formatPeriodLabel(value: PeriodValue, now: Date = new Date()): string {
  if (value.kind !== "range") return LABELS[value.kind];
  const { from, to } = resolvePeriod(value, now);
  return from === to ? formatDay(from) : `Del ${formatDay(from)} al ${formatDay(to)}`;
}
