/**
 * Utilidades del tester T3 (6.2). Cada suite declara sus `jest.mock` (`@/lib/api`, `next/navigation`,
 * catalog.service y config.service) y usa estas utilidades: un "servidor" falso en la frontera HTTP
 * (`api.get/api.post`), con máquina de estados, idempotencia por `Idempotency-Key`, modo avión y
 * fallas inyectables. Se prueba la UI real, el hook real y la cola real contra ese servidor.
 */
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider, onlineManager } from "@tanstack/react-query";
import { AxiosError, AxiosHeaders } from "axios";
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { useOfflineQueueStore } from "@/stores/offlineQueueStore";
import { useToastStore } from "@/stores/toastStore";
import { servicesApi } from "@/services/catalog.service";
import {
  getSettings,
  paymentMethodsApi,
  resourcesApi,
  vehicleSizesApi,
} from "@/services/config.service";
import type { DayEntry, DayResponse, Job } from "@/features/operacion/types";
import type { PaymentMethod, PermissionCode, Service, VehicleSize } from "@/types/api";

export const TZ = "America/Argentina/Cordoba";
export const QUEUE_KEY = "carwash_offline_queue";

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const cfg = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
export function apiError(status: number, code?: string, message = "x") {
  const response = {
    status,
    data: code ? { detail: { code, message } } : { detail: message },
    statusText: "",
    headers: {},
    config: cfg,
  } as AxiosResponse;
  return new AxiosError("Request failed", "ERR_BAD_REQUEST", cfg, null, response);
}
export const networkError = () => new AxiosError("Network Error", "ERR_NETWORK", cfg);

// ---------------------------------------------------------------- fixtures
const stamp = {
  tenant_id: "t1",
  created_at: "2026-11-05T00:00:00Z",
  updated_at: "2026-11-05T00:00:00Z",
};
export const page = <T,>(items: T[]) => ({
  items,
  total: items.length,
  limit: 200,
  offset: 0,
  has_more: false,
});
export const method = (id: string, code: string, label: string, for_income = true): PaymentMethod =>
  ({
    id,
    code,
    label,
    for_income,
    for_expense: true,
    commission_bps: 0,
    settlement_account: null,
    ...stamp,
  }) as PaymentMethod;
export const efectivo = method("m1", "EFECTIVO", "Efectivo");
export const tarjeta = method("m2", "TARJETA", "Tarjeta");
export const puestos = [
  { id: "r1", name: "Puesto 1", sort_order: 0, ...stamp },
  { id: "r2", name: "Puesto 2", sort_order: 1, ...stamp },
];
export const tamanos = [
  { id: "s1", code: "AUTO", label: "Auto", sort_order: 0, ...stamp },
] as VehicleSize[];
export const servicio = {
  id: "sv1",
  name: "Lavado completo",
  pricing_mode: "PRECIO_FIJO",
  prices: [{ id: "p1", vehicle_size_id: "s1", price_cents: 2_000_000, duration_min: 60 }],
} as unknown as Service;
export const cotizar = {
  ...servicio,
  id: "sv2",
  name: "Pulido",
  pricing_mode: "A_COTIZAR",
} as Service;

export function job(over: Partial<Job> = {}): Job {
  return {
    id: "j1",
    status: "PRESENTE",
    arrived_at: "2030-11-25T13:00:00Z",
    scheduled_at: "2030-11-25T13:00:00Z",
    started_at: null,
    finished_at: null,
    picked_up_at: null,
    settled_at: null,
    cancel_delay_from: null,
    tolerance_min: 20,
    arrival_delay_min: 0,
    entry_mode: "EN_VIVO",
    channel: "TURNERO_WEB",
    booking_id: "b1",
    booking_code: "TUR-ABC234",
    resource_id: "r1",
    vehicle_size_id: "s1",
    vehicle_plate: "AA000AA",
    customer: { id: "c1", name: "Cliente Demo", phone_e164: "+5493510000001" },
    services: [
      {
        service_id: "sv1",
        name: "Lavado completo",
        duration_min: 60,
        price_cents: 2_000_000,
        pricing_mode: "PRECIO_FIJO",
      },
    ],
    base_price_cents: 2_000_000,
    discount_cents: 0,
    surcharge_cents: 0,
    total_cents: 2_000_000,
    paid_cents: 0,
    balance_due_cents: 2_000_000,
    payments: [],
    inspection: null,
    notes: null,
    ...over,
  } as Job;
}

export function entry(over: Partial<DayEntry> = {}): DayEntry {
  return {
    id: "e1",
    kind: "JOB",
    job_id: "j1",
    booking_id: "b1",
    job_status: "EN_PROCESO",
    booking_status: null,
    resource_id: "r1",
    start_at: "2030-11-25T13:00:00Z",
    end_at: "2030-11-25T14:00:00Z",
    code: "TUR-ABC234",
    customer: { id: "c1", name: "Cliente Demo", phone_e164: "+5493510000001" },
    vehicle_plate: "AA000AA",
    services: ["Lavado completo"],
    total_cents: 2_000_000,
    paid_cents: 0,
    balance_due_cents: 2_000_000,
    cancel_delay_from: null,
    entry_mode: "EN_VIVO",
    ...over,
  } as DayEntry;
}

export function bookingEntry(over: Partial<DayEntry> = {}): DayEntry {
  return entry({
    id: "b1",
    kind: "BOOKING",
    job_id: null,
    booking_id: "b1",
    job_status: null,
    booking_status: "CONFIRMADO",
    balance_due_cents: null,
    ...over,
  } as Partial<DayEntry>);
}

export function day(entries: DayEntry[], over: Partial<DayResponse> = {}): DayResponse {
  return {
    date: "2030-11-25",
    timezone: TZ,
    tolerance_min: 20,
    truncated: false,
    resources: [
      { id: "r1", name: "Puesto 1" },
      { id: "r2", name: "Puesto 2" },
    ],
    entries,
    ...over,
  } as DayResponse;
}

export const owner = {
  id: "u1",
  email: "dueno@example.com",
  username: null,
  role: "OWNER" as const,
  tenant_id: "t1",
  permission_profile_id: null,
};
export const staff = { ...owner, id: "u2", email: "staff@example.com", role: "STAFF" as const };

export const ALL_PERMS: PermissionCode[] = [
  "AGENDA_VER",
  "JOBS_OPERAR",
  "COBROS_REGISTRAR",
  "COBROS_ANULAR",
  "TURNOS_GESTIONAR",
];

export function loginAs(permissions: PermissionCode[] | "owner") {
  if (permissions === "owner") {
    useAuthStore.setState({ user: owner, permissions: [], permissionsLoaded: true });
  } else {
    useAuthStore.setState({ user: staff, permissions, permissionsLoaded: true });
  }
}

export function renderUi(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { qc, ...render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>) };
}

// ---------------------------------------------------------------- navegador
export function setOnline(value: boolean) {
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => value });
}

// ---------------------------------------------------------------- servidor falso
export interface Received {
  url: string;
  body: Record<string, unknown>;
  key: string | undefined;
  occurred_at: string | undefined;
}

export interface FakeServer {
  online: boolean;
  jobs: Map<string, Job>;
  days: Record<string, DayResponse>;
  defaultDay: DayResponse;
  /** Todo lo que el servidor PROCESÓ (no cuenta lo que no llegó por falta de señal). */
  received: Received[];
  /** Todo intento de POST, llegue o no. */
  attempts: Received[];
  gets: string[];
  /** Falla para un POST (regex de la url). `times < 0` = siempre. */
  failPost: Array<{ match: RegExp; error: () => unknown; times: number }>;
  failGet: Array<{ match: RegExp; error: () => unknown }>;
  /** Frena todos los POST hasta que se resuelva la promesa. */
  gate: Promise<void> | null;
  idem: Map<string, unknown>;
  rejectPickUpWithDebt: boolean;
  nextJobId: number;
}

export function createServer(initial: Job[] = [job()]): FakeServer {
  const s: FakeServer = {
    online: true,
    jobs: new Map(initial.map((j) => [j.id, j])),
    days: {},
    defaultDay: day([entry()]),
    received: [],
    attempts: [],
    gets: [],
    failPost: [],
    failGet: [],
    gate: null,
    idem: new Map(),
    rejectPickUpWithDebt: false,
    nextJobId: 100,
  };
  const apiMock = api as unknown as { get: jest.Mock; post: jest.Mock };

  apiMock.get.mockImplementation(async (url: string, config?: { params?: { date?: string } }) => {
    s.gets.push(url);
    if (!s.online) throw networkError();
    for (const f of s.failGet) if (f.match.test(url)) throw f.error();
    if (url === "/agenda/day") {
      const d = config?.params?.date ?? "";
      return { data: s.days[d] ?? { ...s.defaultDay, date: d } };
    }
    let m = /^\/jobs\/([^/]+)\/events$/.exec(url);
    if (m) return { data: { ...page([]), limit: 20 } };
    m = /^\/jobs\/([^/]+)$/.exec(url);
    if (m) {
      const j = s.jobs.get(m[1]!);
      if (!j) throw apiError(404, "NOT_FOUND");
      return { data: clone(j) };
    }
    throw apiError(404, "NOT_FOUND");
  });

  apiMock.post.mockImplementation(
    async (
      url: string,
      body: Record<string, unknown> = {},
      config?: { headers?: Record<string, string> },
    ) => {
      const key = config?.headers?.["Idempotency-Key"];
      const rec: Received = {
        url,
        body: clone(body),
        key,
        occurred_at: body.occurred_at as string | undefined,
      };
      s.attempts.push(rec);
      if (s.gate) await s.gate;
      if (!s.online) throw networkError();
      for (const f of s.failPost) {
        if (f.times !== 0 && f.match.test(url)) {
          if (f.times > 0) f.times -= 1;
          throw f.error();
        }
      }
      if (key && s.idem.has(key)) {
        // Reenvío con la misma clave: mismo resultado, sin efectos (200).
        return { data: clone(s.idem.get(key)) };
      }
      const out = applyPost(s, url, body);
      s.received.push(rec);
      if (key) s.idem.set(key, out);
      return { data: clone(out) };
    },
  );
  return s;
}

function applyPost(s: FakeServer, url: string, body: Record<string, unknown>): unknown {
  const at = body.occurred_at as string | undefined;
  if (url === "/jobs") {
    const id = `j${s.nextJobId++}`;
    const j = job({
      id,
      status: "PRESENTE",
      booking_id: (body.booking_id as string | undefined) ?? null,
    });
    s.jobs.set(id, j);
    return j;
  }
  if (url === "/jobs/retrospective" || url === "/bookings/manual") {
    return job({ id: `j${s.nextJobId++}`, status: "FINALIZADO" });
  }
  const m =
    /^\/jobs\/([^/]+)\/(start|finish|pick-up|cancel-delay|payments|inspection)(?:\/.*)?$/.exec(url);
  if (!m) throw apiError(404, "NOT_FOUND");
  const j = s.jobs.get(m[1]!);
  if (!j) throw apiError(404, "NOT_FOUND");
  const act = m[2]!;
  const bad = () => apiError(409, "INVALID_TRANSITION");
  if (act === "start") {
    if (j.status !== "PRESENTE") throw bad();
    Object.assign(j, { status: "EN_PROCESO", started_at: at });
  } else if (act === "finish") {
    if (j.status !== "EN_PROCESO") throw bad();
    Object.assign(j, { status: "FINALIZADO", finished_at: at });
  } else if (act === "payments") {
    if (j.status !== "FINALIZADO") throw bad();
    const amount = (body.amount_cents as number | undefined) ?? j.balance_due_cents;
    if (amount > j.balance_due_cents || amount <= 0) throw apiError(422, "VALIDATION_ERROR");
    const balance = j.balance_due_cents - amount;
    Object.assign(j, {
      paid_cents: j.paid_cents + amount,
      balance_due_cents: balance,
      status: balance === 0 ? "COBRADO" : "FINALIZADO",
    });
  } else if (act === "pick-up") {
    if (j.status !== "FINALIZADO" && j.status !== "COBRADO") throw bad();
    if (s.rejectPickUpWithDebt && j.balance_due_cents > 0) throw apiError(422, "VALIDATION_ERROR");
    Object.assign(j, { status: "RETIRADO", picked_up_at: at });
  } else if (act === "cancel-delay") {
    if (j.status !== "PRESENTE") throw bad();
    Object.assign(j, { status: "CANCELADO_DEMORA" });
  }
  return clone(j);
}

export function resetUnits(extra?: { methods?: PaymentMethod[] }) {
  jest.clearAllMocks();
  onlineManager.setOnline(true);
  localStorage.clear();
  setOnline(true);
  useToastStore.setState({ toasts: [] });
  useOfflineQueueStore.setState({ items: [], failed: [], conflicts: [] });
  loginAs("owner");
  (getSettings as jest.Mock).mockResolvedValue({ timezone: TZ });
  (resourcesApi.list as jest.Mock).mockResolvedValue(page(puestos));
  (paymentMethodsApi.list as jest.Mock).mockResolvedValue(
    page(extra?.methods ?? [efectivo, tarjeta]),
  );
  (vehicleSizesApi.list as jest.Mock).mockResolvedValue(page(tamanos));
  (servicesApi.list as jest.Mock).mockResolvedValue(page([servicio, cotizar]));
}

/** Simula "recargar la página": la memoria de la cola se tira y se vuelve a leer del localStorage. */
export async function reloadQueue() {
  const raw = localStorage.getItem(QUEUE_KEY);
  useOfflineQueueStore.setState({ items: [], failed: [], conflicts: [] });
  if (raw) localStorage.setItem(QUEUE_KEY, raw);
  await useOfflineQueueStore.persist.rehydrate();
}

export function goOffline(s: FakeServer) {
  s.online = false;
  setOnline(false);
  window.dispatchEvent(new Event("offline"));
}
export function goOnline(s: FakeServer) {
  s.online = true;
  setOnline(true);
  window.dispatchEvent(new Event("online"));
}

export const queueItems = () => useOfflineQueueStore.getState().items;

/** Texto que un usuario no debería leer jamás en un error: técnico, inglés, internals. */
export const TECHNICAL =
  /Traceback|psycopg|SQLSTATE|IntegrityError|AxiosError|Request failed|Network Error|status code|\[object|undefined|NaN|stack|at \w+\.\w+ \(|VALIDATION_ERROR|INVALID_TRANSITION|DUPLICATE_IDEMPOTENT|SLOT_TAKEN|\bError \d{3}\b|Internal Server|Not Found|Forbidden|detail/i;

// Jest toma todo lo que vive en `__tests__/` por suite; este archivo es solo utilidades.
it.skip("harness.tsx: utilidades, no es una suite", () => undefined);
