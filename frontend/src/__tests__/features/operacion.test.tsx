import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import {
  AxiosError,
  AxiosHeaders,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import { AgendaPageClient } from "@/features/operacion/AgendaPageClient";
import { BookingArrivalClient } from "@/features/operacion/BookingArrivalClient";
import { InspectionPageClient } from "@/features/operacion/InspectionForm";
import { JobPageClient } from "@/features/operacion/JobPageClient";
import { ManualBookingForm } from "@/features/operacion/ManualBookingForm";
import { RetrospectiveForm } from "@/features/operacion/RetrospectiveForm";
import { WalkInForm } from "@/features/operacion/WalkInForm";
import { operacionErrorMessage } from "@/features/operacion/errors";
import { defaultPaymentMethod } from "@/features/operacion/queries";
import { TOUCH_TARGET, undersizedControls } from "@/features/operacion/touch";
import * as service from "@/features/operacion/service";
import type { DayEntry, DayResponse, Job } from "@/features/operacion/types";
import { addDays, todayInZone } from "@/lib/period";
import { servicesApi } from "@/services/catalog.service";
import {
  getSettings,
  paymentMethodsApi,
  resourcesApi,
  vehicleSizesApi,
} from "@/services/config.service";
import { useAuthStore } from "@/stores/authStore";
import { useOfflineQueueStore } from "@/stores/offlineQueueStore";
import { useToastStore } from "@/stores/toastStore";
import type { PaymentMethod, PermissionCode, Service, VehicleSize } from "@/types/api";

const push = jest.fn();
const replace = jest.fn();
let searchParams = new URLSearchParams();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace }),
  useSearchParams: () => searchParams,
}));
jest.mock("@/features/operacion/service");
jest.mock("@/services/catalog.service", () => ({ servicesApi: { list: jest.fn() } }));
jest.mock("@/services/config.service", () => ({
  getSettings: jest.fn(),
  resourcesApi: { list: jest.fn() },
  paymentMethodsApi: { list: jest.fn() },
  vehicleSizesApi: { list: jest.fn() },
}));

jest.setTimeout(30_000);

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

const axiosConfig = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
function apiError(status: number, code: string) {
  const response = {
    status,
    data: { detail: { code, message: "x" } },
    statusText: "",
    headers: {},
    config: axiosConfig,
  } as AxiosResponse;
  return new AxiosError("x", "ERR_BAD_REQUEST", axiosConfig, null, response);
}
const networkError = () => new AxiosError("Network Error", "ERR_NETWORK", axiosConfig);

const TZ = "America/Argentina/Cordoba";
const stamp = {
  tenant_id: "t1",
  created_at: "2026-11-05T00:00:00Z",
  updated_at: "2026-11-05T00:00:00Z",
};
const page = <T,>(items: T[]) => ({
  items,
  total: items.length,
  limit: 200,
  offset: 0,
  has_more: false,
});
const method = (id: string, code: string, label: string): PaymentMethod =>
  ({
    id,
    code,
    label,
    for_income: true,
    for_expense: true,
    commission_bps: 0,
    settlement_account: null,
    ...stamp,
  }) as PaymentMethod;
const efectivo = method("m1", "EFECTIVO", "Efectivo");
const tarjeta = method("m2", "TARJETA", "Tarjeta");
const puestos = [
  { id: "r1", name: "Puesto 1", sort_order: 0, ...stamp },
  { id: "r2", name: "Puesto 2", sort_order: 1, ...stamp },
];
const tamanos = [
  { id: "s1", code: "AUTO", label: "Auto", sort_order: 0, ...stamp },
] as VehicleSize[];
const servicio = {
  id: "sv1",
  name: "Lavado completo",
  pricing_mode: "PRECIO_FIJO",
  prices: [{ id: "p1", vehicle_size_id: "s1", price_cents: 2_000_000, duration_min: 60 }],
} as unknown as Service;
const cotizar = { ...servicio, id: "sv2", name: "Pulido", pricing_mode: "A_COTIZAR" } as Service;

function job(over: Partial<Job> = {}): Job {
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

function entry(over: Partial<DayEntry> = {}): DayEntry {
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

function day(entries: DayEntry[], over: Partial<DayResponse> = {}): DayResponse {
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

const owner = {
  id: "u1",
  email: "dueno@example.com",
  username: null,
  role: "OWNER" as const,
  tenant_id: "t1",
  permission_profile_id: null,
};
const staff = { ...owner, id: "u2", role: "STAFF" as const };

function as(permissions: PermissionCode[]) {
  useAuthStore.setState({ user: staff, permissions, permissionsLoaded: true });
}

function renderUi(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

/** Cuenta los toques del usuario: el KPI del contrato es 1 toque (llegó/iniciar/finalizar) y 2 (cobrar). */
function taps() {
  let n = 0;
  return {
    get count() {
      return n;
    },
    async click(el: HTMLElement) {
      n += 1;
      await userEvent.click(el);
    },
  };
}

const sv = service as jest.Mocked<typeof service>;

beforeEach(() => {
  jest.clearAllMocks();
  searchParams = new URLSearchParams();
  useToastStore.setState({ toasts: [] });
  useOfflineQueueStore.setState({ items: [], failed: [] });
  useAuthStore.setState({ user: owner, permissions: [], permissionsLoaded: true });
  (getSettings as jest.Mock).mockResolvedValue({ timezone: TZ });
  (resourcesApi.list as jest.Mock).mockResolvedValue(page(puestos));
  (paymentMethodsApi.list as jest.Mock).mockResolvedValue(page([efectivo, tarjeta]));
  (vehicleSizesApi.list as jest.Mock).mockResolvedValue(page(tamanos));
  (servicesApi.list as jest.Mock).mockResolvedValue(page([servicio, cotizar]));
  sv.getJob.mockResolvedValue(job());
  sv.listJobEvents.mockResolvedValue({ ...page([]), has_more: false } as never);
  sv.getDay.mockResolvedValue(day([entry()]));
});

describe("Agenda del día (D6-1)", () => {
  it("arma una columna por puesto, la de sin puesto, el saldo y los estados con texto", async () => {
    sv.getDay.mockResolvedValue(
      day([
        entry({ job_status: "FINALIZADO" }),
        entry({
          id: "e2",
          kind: "BOOKING",
          job_id: null,
          job_status: null,
          booking_status: "CONFIRMADO",
          resource_id: "r2",
          paid_cents: 0,
          balance_due_cents: null,
        }),
        entry({ id: "e3", job_id: "j3", resource_id: null, job_status: "PRESENTE" }),
      ]),
    );
    renderUi(<AgendaPageClient />);
    const p1 = await screen.findByRole("region", { name: "Puesto Puesto 1" });
    expect(within(p1).getByText("Finalizado")).toBeInTheDocument();
    expect(within(p1).getByText("Debe $ 20.000,00")).toBeInTheDocument();
    expect(within(p1).getByText("10:00–11:00")).toBeInTheDocument();
    const p2 = screen.getByRole("region", { name: "Puesto Puesto 2" });
    expect(within(p2).getByText("Confirmado")).toBeInTheDocument();
    expect(within(p2).queryByText(/Debe/)).not.toBeInTheDocument();
    const sinPuesto = screen.getByRole("region", { name: "Puesto Sin puesto" });
    expect(within(sinPuesto).getByText("En el local")).toBeInTheDocument();
  });

  it("tocar un renglón abre la pantalla operativa: el job, o el turno que aún no llegó", async () => {
    sv.getDay.mockResolvedValue(
      day([
        entry(),
        entry({
          id: "e2",
          kind: "BOOKING",
          job_id: null,
          job_status: null,
          booking_id: "b9",
          booking_status: "CONFIRMADO",
          resource_id: "r2",
        }),
      ]),
    );
    renderUi(<AgendaPageClient initialDate="2030-11-25" />);
    const links = await screen.findAllByRole("link", { name: /Cliente Demo/ });
    expect(links.map((l) => l.getAttribute("href"))).toEqual([
      "/operacion/j1",
      "/operacion/turno/b9?date=2030-11-25",
    ]);
    expect(sv.getDay).toHaveBeenCalledWith("2030-11-25");
  });

  it("el selector de día usa hoy y ayer del negocio y avisa si la lista se truncó", async () => {
    sv.getDay.mockResolvedValue(day([entry()], { truncated: true }));
    renderUi(<AgendaPageClient />);
    expect(await screen.findByRole("status")).toHaveTextContent(/más turnos y trabajos/);
    const hoy = todayInZone(new Date(), TZ);
    await userEvent.click(screen.getByRole("button", { name: "Ayer" }));
    await waitFor(() => expect(sv.getDay).toHaveBeenCalledWith(addDays(hoy, -1)));
    await userEvent.click(screen.getByRole("button", { name: "Día siguiente" }));
    await waitFor(() => expect(sv.getDay).toHaveBeenCalledWith(hoy));
  });

  it("muestra los accesos a cargar según permiso y oculta la agenda sin AGENDA_VER", async () => {
    as(["AGENDA_VER", "JOBS_OPERAR"]);
    const { unmount } = renderUi(<AgendaPageClient />);
    expect(await screen.findByRole("link", { name: "Ingreso sin turno" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Cargar trabajo hecho" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Turno a mano" })).not.toBeInTheDocument();
    unmount();
    as(["JOBS_OPERAR"]);
    renderUi(<AgendaPageClient />);
    expect(await screen.findByText(/No tenés acceso a la agenda/)).toBeInTheDocument();
    expect(sv.getDay).toHaveBeenCalledTimes(1); // solo el de la primera pantalla
  });

  it("avisa cuando falla la carga y cuando no hay turnos", async () => {
    sv.getDay.mockRejectedValueOnce(networkError());
    const { unmount } = renderUi(<AgendaPageClient />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/No se pudo cargar la agenda/);
    unmount();
    sv.getDay.mockResolvedValue(day([]));
    renderUi(<AgendaPageClient />);
    expect(await screen.findByText(/No hay turnos ni trabajos este día/)).toBeInTheDocument();
  });
});

describe("Pantalla operativa: KPI de toques (FASE-6-CONTRATO)", () => {
  it("INICIAR es 1 toque, con Idempotency-Key y la hora del toque", async () => {
    sv.startJob.mockResolvedValue(job({ status: "EN_PROCESO" }));
    renderUi(<JobPageClient id="j1" />);
    const t = taps();
    const antes = Date.now();
    await t.click(await screen.findByRole("button", { name: "INICIAR" }));
    await waitFor(() => expect(sv.startJob).toHaveBeenCalledTimes(1));
    const [id, body, key] = sv.startJob.mock.calls[0]!;
    expect(id).toBe("j1");
    expect(typeof key).toBe("string");
    const occurred = Date.parse(body.occurred_at);
    expect(occurred).toBeGreaterThanOrEqual(antes - 1);
    expect(occurred).toBeLessThanOrEqual(Date.now());
    expect(t.count).toBe(1);
  });

  it("FINALIZAR es 1 toque", async () => {
    sv.getJob.mockResolvedValue(job({ status: "EN_PROCESO" }));
    sv.finishJob.mockResolvedValue(job({ status: "FINALIZADO" }));
    renderUi(<JobPageClient id="j1" />);
    const t = taps();
    await t.click(await screen.findByRole("button", { name: "FINALIZAR" }));
    await waitFor(() => expect(sv.finishJob).toHaveBeenCalledTimes(1));
    expect(t.count).toBe(1);
  });

  it("COBRAR son 2 toques: abrir y confirmar, con el medio por defecto y el saldo", async () => {
    sv.getJob.mockResolvedValue(job({ status: "FINALIZADO" }));
    sv.recordPayment.mockResolvedValue(job({ status: "COBRADO", balance_due_cents: 0 }));
    renderUi(<JobPageClient id="j1" />);
    const t = taps();
    await t.click(await screen.findByRole("button", { name: /COBRAR/ }));
    expect(screen.getByLabelText("Medio de pago")).toHaveValue("m1"); // efectivo
    await t.click(screen.getByRole("button", { name: /CONFIRMAR COBRO/ }));
    await waitFor(() => expect(sv.recordPayment).toHaveBeenCalledTimes(1));
    const [id, body, key] = sv.recordPayment.mock.calls[0]!;
    expect(id).toBe("j1");
    expect(body.payment_method_id).toBe("m1");
    expect(body).not.toHaveProperty("amount_cents"); // el servidor cobra el saldo completo
    expect(typeof body.occurred_at).toBe("string");
    expect(typeof key).toBe("string");
    expect(t.count).toBe(2);
  });

  it("RETIRAR es 1 toque desde COBRADO", async () => {
    sv.getJob.mockResolvedValue(job({ status: "COBRADO", balance_due_cents: 0 }));
    sv.pickUpJob.mockResolvedValue(job({ status: "RETIRADO" }));
    renderUi(<JobPageClient id="j1" />);
    const t = taps();
    await t.click(await screen.findByRole("button", { name: "RETIRAR" }));
    await waitFor(() => expect(sv.pickUpJob).toHaveBeenCalledTimes(1));
    expect(t.count).toBe(1);
  });

  it("LLEGÓ de un turno es 1 toque y sigue en la pantalla del job", async () => {
    sv.getDay.mockResolvedValue(
      day([
        entry({
          kind: "BOOKING",
          job_id: null,
          job_status: null,
          booking_status: "CONFIRMADO",
          booking_id: "b1",
        }),
      ]),
    );
    sv.receiveJob.mockResolvedValue(job());
    searchParams = new URLSearchParams("date=2030-11-25");
    renderUi(<BookingArrivalClient bookingId="b1" />);
    const t = taps();
    await t.click(await screen.findByRole("button", { name: "LLEGÓ" }));
    await waitFor(() => expect(sv.receiveJob).toHaveBeenCalledTimes(1));
    const [body, key] = sv.receiveJob.mock.calls[0]!;
    expect(body).toMatchObject({ booking_id: "b1" });
    expect(typeof body.occurred_at).toBe("string");
    expect(typeof key).toBe("string");
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/operacion/j1"));
    expect(t.count).toBe(1);
  });

  it("un turno que ya tiene job lleva a la pantalla del job; uno que no existe avisa", async () => {
    sv.getDay.mockResolvedValue(day([entry({ booking_id: "b1" })]));
    searchParams = new URLSearchParams("date=2030-11-25");
    const { unmount } = renderUi(<BookingArrivalClient bookingId="b1" />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/operacion/j1"));
    unmount();
    renderUi(<BookingArrivalClient bookingId="otro" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/No se encontró el turno/);
  });
});

describe("Pantalla operativa: estados, permisos y errores", () => {
  it("muestra los botones según el estado: cada paso solo cuando corresponde", async () => {
    sv.getJob.mockResolvedValue(job({ status: "PRESENTE" }));
    const { unmount } = renderUi(<JobPageClient id="j1" />);
    expect(await screen.findByRole("button", { name: "INICIAR" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "FINALIZAR" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "RETIRAR" })).not.toBeInTheDocument();
    unmount();
    sv.getJob.mockResolvedValue(job({ status: "FINALIZADO" }));
    renderUi(<JobPageClient id="j1" />);
    expect(await screen.findByRole("button", { name: /COBRAR/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "RETIRAR" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "INICIAR" })).not.toBeInTheDocument();
  });

  it("oculta las acciones sin permiso (el servidor manda igual)", async () => {
    as(["AGENDA_VER"]);
    sv.getJob.mockResolvedValue(job({ status: "FINALIZADO" }));
    const { unmount } = renderUi(<JobPageClient id="j1" />);
    await screen.findByText("Cliente Demo · AA000AA");
    expect(screen.queryByRole("button", { name: /COBRAR/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "RETIRAR" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Recepción completa/ })).not.toBeInTheDocument();
    unmount();
    as(["AGENDA_VER", "JOBS_OPERAR"]); // opera pero no cobra
    sv.getJob.mockResolvedValue(job({ status: "FINALIZADO" }));
    renderUi(<JobPageClient id="j1" />);
    expect(await screen.findByRole("button", { name: "RETIRAR" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /COBRAR/ })).not.toBeInTheDocument();
  });

  it("'Cancelar por demora' aparece solo desde cancel_delay_from y es un botón manual", async () => {
    sv.getJob.mockResolvedValue(
      job({ cancel_delay_from: new Date(Date.now() + 3_600_000).toISOString() }),
    );
    const { unmount } = renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("button", { name: "INICIAR" });
    expect(screen.queryByRole("button", { name: "Cancelar por demora" })).not.toBeInTheDocument();
    unmount();

    sv.getJob.mockResolvedValue(
      job({ cancel_delay_from: new Date(Date.now() - 60_000).toISOString() }),
    );
    sv.cancelDelay.mockResolvedValue(job({ status: "CANCELADO_DEMORA" }));
    renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancelar por demora" }));
    expect(sv.cancelDelay).not.toHaveBeenCalled(); // pide confirmación: nunca automático
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /Sí, cancelar por demora/ }));
    await waitFor(() => expect(sv.cancelDelay).toHaveBeenCalledTimes(1));
    expect(sv.cancelDelay.mock.calls[0]![0]).toBe("j1");
  });

  it("un 409 INVALID_TRANSITION se muestra en español, vuelve a leer el job y no pierde nada", async () => {
    sv.startJob.mockRejectedValue(apiError(409, "INVALID_TRANSITION"));
    renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await screen.findByRole("button", { name: "INICIAR" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/ya no corresponde/);
    await waitFor(() => expect(sv.getJob.mock.calls.length).toBeGreaterThan(1));
    expect(useOfflineQueueStore.getState().items).toHaveLength(0);
  });

  it("un cobro rechazado (422) explica qué revisar; un job ajeno (404) no revela nada", async () => {
    sv.getJob.mockResolvedValue(job({ status: "FINALIZADO" }));
    sv.recordPayment.mockRejectedValue(apiError(422, "VALIDATION_ERROR"));
    const { unmount } = renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await screen.findByRole("button", { name: /COBRAR/ }));
    await userEvent.click(screen.getByRole("button", { name: /CONFIRMAR COBRO/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Revisá el cobro/);
    unmount();
    sv.getJob.mockRejectedValue(apiError(404, "NOT_FOUND"));
    renderUi(<JobPageClient id="ajeno" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/No se encontró el trabajo/);
  });

  it("permite cobrar menos del saldo (cobro parcial) y rechaza un importe mayor", async () => {
    sv.getJob.mockResolvedValue(job({ status: "FINALIZADO" }));
    sv.recordPayment.mockResolvedValue(job({ status: "FINALIZADO", balance_due_cents: 1_000_000 }));
    renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await screen.findByRole("button", { name: /COBRAR/ }));
    const importe = screen.getByLabelText("Importe");
    await userEvent.clear(importe);
    await userEvent.type(importe, "30.000");
    expect(screen.getByRole("alert")).toHaveTextContent(/no superar el saldo/);
    expect(screen.getByRole("button", { name: /CONFIRMAR COBRO/ })).toBeDisabled();
    await userEvent.clear(importe);
    await userEvent.type(importe, "10.000");
    await userEvent.selectOptions(screen.getByLabelText("Medio de pago"), "m2");
    await userEvent.click(screen.getByRole("button", { name: /CONFIRMAR COBRO/ }));
    await waitFor(() => expect(sv.recordPayment).toHaveBeenCalledTimes(1));
    expect(sv.recordPayment.mock.calls[0]![1]).toMatchObject({
      payment_method_id: "m2",
      amount_cents: 1_000_000,
    });
  });

  it("anula un cobro con motivo (COBROS_ANULAR)", async () => {
    const pago = {
      id: "pay1",
      kind: "SALDO" as const,
      amount_cents: 2_000_000,
      occurred_at: "2030-11-25T14:30:00Z",
      payment_method_id: "m1",
      voided: false,
    };
    sv.getJob.mockResolvedValue(
      job({ status: "COBRADO", balance_due_cents: 0, paid_cents: 2_000_000, payments: [pago] }),
    );
    sv.voidPayment.mockResolvedValue(job());
    renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Anular cobro" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Error de importe");
    await userEvent.click(within(dialog).getByRole("button", { name: "Anular cobro" }));
    await waitFor(() => expect(sv.voidPayment).toHaveBeenCalledTimes(1));
    const [id, paymentId, body, key] = sv.voidPayment.mock.calls[0]!;
    expect([id, paymentId, body.reason, typeof key]).toEqual([
      "j1",
      "pay1",
      "Error de importe",
      "string",
    ]);
  });

  it("sin COBROS_ANULAR no hay botón de anular", async () => {
    as(["AGENDA_VER", "JOBS_OPERAR", "COBROS_REGISTRAR"]);
    sv.getJob.mockResolvedValue(
      job({
        status: "COBRADO",
        balance_due_cents: 0,
        payments: [
          {
            id: "pay1",
            kind: "SALDO",
            amount_cents: 1,
            occurred_at: "2030-11-25T14:30:00Z",
            payment_method_id: "m1",
            voided: false,
          },
        ],
      }),
    );
    renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("button", { name: "RETIRAR" });
    expect(screen.queryByRole("button", { name: "Anular cobro" })).not.toBeInTheDocument();
  });

  it("la recepción completa es un enlace secundario y no está en el camino de los botones", async () => {
    renderUi(<JobPageClient id="j1" />);
    const link = await screen.findByRole("link", { name: /Recepción completa \(opcional\)/ });
    expect(link).toHaveAttribute("href", "/operacion/j1/recepcion");
    expect(sv.recordInspection).not.toHaveBeenCalled();
  });

  it("la línea de tiempo se pagina con 'Ver más' y marca la carga manual", async () => {
    const ev = (id: string, type: string, manual = false) => ({
      id,
      event_type: type,
      occurred_at: "2030-11-25T13:00:00Z",
      created_at: "2030-11-25T13:00:00Z",
      actor_user_id: "u1",
      from_status: null,
      to_status: null,
      metadata: manual ? { manual_entry: true } : {},
    });
    sv.listJobEvents
      .mockResolvedValueOnce({
        items: [ev("e1", "JOB_RECEIVED", true)],
        total: 2,
        limit: 20,
        offset: 0,
        has_more: true,
      } as never)
      .mockResolvedValueOnce({
        items: [ev("e2", "JOB_STARTED")],
        total: 2,
        limit: 20,
        offset: 20,
        has_more: false,
      } as never);
    renderUi(<JobPageClient id="j1" />);
    expect(await screen.findByText(/Llegó/, { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("(carga manual)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Ver más" }));
    expect(await screen.findByText("Se inició")).toBeInTheDocument();
    expect(sv.listJobEvents).toHaveBeenLastCalledWith("j1", { limit: 20, offset: 20 });
    expect(screen.queryByRole("button", { name: "Ver más" })).not.toBeInTheDocument();
  });
});

describe("Sin conexión: cola offline con la hora del toque", () => {
  it("encola con la hora del toque, muestra el paso siguiente y sincroniza con esa hora", async () => {
    sv.startJob.mockRejectedValueOnce(networkError());
    renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await screen.findByRole("button", { name: "INICIAR" }));
    // Quedó en la cola y la pantalla ya muestra el paso siguiente (se puede seguir sin señal).
    expect(await screen.findByRole("button", { name: "FINALIZAR" })).toBeInTheDocument();
    expect(
      screen
        .getAllByRole("status")
        .map((n) => n.textContent)
        .join(" "),
    ).toMatch(/Sin conexión/);
    const [item] = useOfflineQueueStore.getState().items;
    expect(item!.kind).toBe("operacion.start");
    const tapAt = item!.occurredAt as string; // la fija la cola al tocar
    expect(item!.payload).not.toHaveProperty("body.occurred_at"); // el payload no lleva hora propia
    expect(item!.dedupeKey).toBe("iniciar:j1");
    const intentoOnline = sv.startJob.mock.calls[0]!;
    expect(intentoOnline[1].occurred_at).toBe(tapAt);
    expect(intentoOnline[2]).toBe(item!.id); // el id del ítem ES la Idempotency-Key

    await new Promise((r) => setTimeout(r, 25)); // pasa el tiempo hasta que vuelve la señal
    sv.startJob.mockResolvedValue(job({ status: "EN_PROCESO" }));
    window.dispatchEvent(new Event("online"));
    await waitFor(() => expect(sv.startJob).toHaveBeenCalledTimes(2));
    const reenvio = sv.startJob.mock.calls[1]!;
    expect(reenvio[1].occurred_at).toBe(tapAt); // la hora del toque, no la de la sincronización
    expect(reenvio[2]).toBe(item!.id); // misma clave: el servidor no la duplica
    await waitFor(() => expect(useOfflineQueueStore.getState().items).toHaveLength(0));
  });

  it("un 409 DUPLICATE_IDEMPOTENT cuenta como éxito y no deja nada en la cola", async () => {
    sv.startJob.mockRejectedValue(apiError(409, "DUPLICATE_IDEMPOTENT"));
    renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await screen.findByRole("button", { name: "INICIAR" }));
    await waitFor(() => expect(sv.startJob).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(useOfflineQueueStore.getState().items).toHaveLength(0);
  });

  it("un doble toque rápido manda una sola acción", async () => {
    let release: (j: Job) => void = () => undefined;
    sv.startJob.mockReturnValue(new Promise<Job>((r) => (release = r)));
    renderUi(<JobPageClient id="j1" />);
    const boton = await screen.findByRole("button", { name: "INICIAR" });
    await userEvent.click(boton);
    await userEvent.click(boton);
    expect(sv.startJob).toHaveBeenCalledTimes(1);
    release(job({ status: "EN_PROCESO" }));
  });
});

describe("Cola endurecida: conflictos y fallidos visibles", () => {
  const queued = (over: object) => ({
    id: "q1",
    userId: "u1",
    tenantId: "t1",
    kind: "operacion.start",
    payload: { jobId: "j1", body: {} },
    createdAt: "2030-11-25T13:00:00.000Z",
    occurredAt: "2030-11-25T13:00:00.000Z",
    attempts: 0,
    failedAt: "2030-11-25T13:05:00.000Z",
    ...over,
  });

  it("un conflicto descartado se muestra en español y se saca con 'Entendido'", async () => {
    useOfflineQueueStore.setState({ conflicts: [queued({})] as never });
    renderUi(<JobPageClient id="j1" />);
    const alerta = await screen.findByText(/No se aplicó el inicio/);
    expect(alerta).toHaveTextContent(/otro dispositivo se adelantó/);
    await userEvent.click(screen.getByRole("button", { name: "Entendido" }));
    expect(useOfflineQueueStore.getState().conflicts).toHaveLength(0);
  });

  it("un fallido se puede reintentar o descartar, también desde la agenda", async () => {
    useOfflineQueueStore.setState({ failed: [queued({ kind: "operacion.pay" })] as never });
    renderUi(<AgendaPageClient />);
    expect(await screen.findByText(/No se pudo enviar el cobro/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(useOfflineQueueStore.getState().failed).toHaveLength(0);
    await waitFor(() => expect(sv.recordPayment).toHaveBeenCalledTimes(1)); // reintentó con la misma cola
    useOfflineQueueStore.setState({ items: [], failed: [queued({})] as never });
    await userEvent.click(await screen.findByRole("button", { name: "Descartar" }));
    expect(useOfflineQueueStore.getState().failed).toHaveLength(0);
  });

  it("los dedupeKey son acción:destino y un toque repetido en vuelo no deja el botón colgado", async () => {
    let release: (j: Job) => void = () => undefined;
    sv.getJob.mockResolvedValue(job({ status: "EN_PROCESO" }));
    sv.finishJob.mockReturnValue(new Promise<Job>((r) => (release = r)));
    renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await screen.findByRole("button", { name: "FINALIZAR" }));
    release(job({ status: "FINALIZADO" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "FINALIZAR" })).toBeEnabled());
  });

  it("LLEGÓ de un turno usa llego:<bookingId> y sin turno una clave propia por toque", async () => {
    sv.receiveJob.mockResolvedValue(job());
    sv.getDay.mockResolvedValue(
      day([entry({ kind: "BOOKING", job_id: null, job_status: null, booking_id: "b1" })]),
    );
    searchParams = new URLSearchParams("date=2030-11-25");
    renderUi(<BookingArrivalClient bookingId="b1" />);
    sv.receiveJob.mockRejectedValueOnce(networkError());
    await userEvent.click(await screen.findByRole("button", { name: "LLEGÓ" }));
    await waitFor(() => expect(useOfflineQueueStore.getState().items).toHaveLength(1));
    expect(useOfflineQueueStore.getState().items[0]!.dedupeKey).toBe("llego:b1");
    expect(useOfflineQueueStore.getState().items[0]!.payload).toEqual({
      body: { booking_id: "b1" },
    });
  });
});

describe("H2: dos cobros sin señal no se pisan", () => {
  it("el doble toque comparte clave (cobrar#0) y el segundo cobro legítimo lleva otra (cobrar#1)", async () => {
    sv.getJob.mockResolvedValue(job({ status: "FINALIZADO" }));
    sv.recordPayment.mockRejectedValue(networkError());
    renderUi(<JobPageClient id="j1" />);
    const cobrar = async (importe: string) => {
      await userEvent.click(await screen.findByRole("button", { name: /^COBRAR/ }));
      const campo = screen.getByLabelText("Importe");
      await userEvent.clear(campo);
      await userEvent.type(campo, importe);
      await userEvent.click(screen.getByRole("button", { name: /CONFIRMAR COBRO/ }));
    };
    await cobrar("5.000");
    await waitFor(() => expect(useOfflineQueueStore.getState().items).toHaveLength(1));
    await cobrar("3.000");
    await waitFor(() => expect(useOfflineQueueStore.getState().items).toHaveLength(2));
    expect(useOfflineQueueStore.getState().items.map((i) => i.dedupeKey)).toEqual([
      "cobrar#0:j1",
      "cobrar#1:j1",
    ]);
  });
});

describe("H7/H8/H1 en las cargas", () => {
  it("sin COBROS_REGISTRAR la retrospectiva arranca pendiente y no ofrece el cobro", async () => {
    as(["AGENDA_VER", "JOBS_OPERAR"]);
    renderUi(<RetrospectiveForm />);
    await screen.findByLabelText("Inicio");
    expect(screen.queryByRole("button", { name: /Cobrado|Cobro pendiente \(tocá/ })).toBeNull();
    expect(screen.getByText(/no tenés permiso para registrar cobros/i)).toBeInTheDocument();
  });

  it("un 403 al cargar se explica específico", async () => {
    sv.recordRetrospective.mockRejectedValue(apiError(403, "FORBIDDEN"));
    renderUi(<RetrospectiveForm />);
    await screen.findByText("Tamaño del vehículo");
    await userEvent.selectOptions(screen.getByLabelText("Tamaño del vehículo"), "s1");
    await userEvent.click(screen.getByRole("button", { name: "Lavado completo" }));
    fireEvent.change(screen.getByLabelText("Inicio"), { target: { value: "09:00" } });
    fireEvent.change(screen.getByLabelText("Fin"), { target: { value: "10:00" } });
    await userEvent.click(screen.getByRole("button", { name: "Cargar trabajo" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /No tenés permiso para cargar cobros/,
    );
  });

  it("un fin menor al inicio con duración razonable se carga al día siguiente y lo avisa", async () => {
    sv.recordRetrospective.mockResolvedValue(job());
    renderUi(<RetrospectiveForm />);
    await screen.findByText("Tamaño del vehículo");
    await userEvent.selectOptions(screen.getByLabelText("Tamaño del vehículo"), "s1");
    await userEvent.click(screen.getByRole("button", { name: "Lavado completo" }));
    fireEvent.change(screen.getByLabelText("Día"), { target: { value: "2026-11-10" } });
    fireEvent.change(screen.getByLabelText("Inicio"), { target: { value: "23:00" } });
    fireEvent.change(screen.getByLabelText("Fin"), { target: { value: "01:00" } });
    expect(await screen.findByText(/terminó al día siguiente \(11\/11\)/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cargar trabajo" }));
    await waitFor(() => expect(sv.recordRetrospective).toHaveBeenCalledTimes(1));
    expect(sv.recordRetrospective.mock.calls[0]![0].finished_at).toBe("2026-11-11T04:00:00.000Z");
  });

  it("un importe ilegible es un error del campo y no se manda como el total", async () => {
    renderUi(<RetrospectiveForm />);
    await screen.findByText("Tamaño del vehículo");
    await userEvent.selectOptions(screen.getByLabelText("Tamaño del vehículo"), "s1");
    await userEvent.click(screen.getByRole("button", { name: "Lavado completo" }));
    fireEvent.change(screen.getByLabelText("Inicio"), { target: { value: "09:00" } });
    fireEvent.change(screen.getByLabelText("Fin"), { target: { value: "10:00" } });
    await userEvent.type(screen.getByLabelText("Importe cobrado"), "20.5");
    await userEvent.click(screen.getByRole("button", { name: "Cargar trabajo" }));
    expect(await screen.findByText(/Importe no válido/)).toBeInTheDocument();
    expect(sv.recordRetrospective).not.toHaveBeenCalled();
  });
});

describe("Ingreso sin turno (LLEGÓ sin reserva)", () => {
  async function fill() {
    await screen.findByText("Tamaño del vehículo");
    await userEvent.selectOptions(screen.getByLabelText("Tamaño del vehículo"), "s1");
    await userEvent.click(screen.getByRole("button", { name: "Lavado completo" }));
  }

  it("LLEGÓ es 1 toque, el cliente puede ser 'sin nombre' y no ofrece servicios a cotizar", async () => {
    sv.receiveJob.mockResolvedValue(job({ id: "j7" }));
    renderUi(<WalkInForm />);
    await fill();
    expect(screen.queryByRole("button", { name: "Pulido" })).not.toBeInTheDocument();
    const t = taps();
    await t.click(screen.getByRole("button", { name: "LLEGÓ" }));
    await waitFor(() => expect(sv.receiveJob).toHaveBeenCalledTimes(1));
    const [body, key] = sv.receiveJob.mock.calls[0]!;
    expect(body).toMatchObject({
      customer_name: null,
      customer_phone: null,
      plate: null,
      vehicle_size_id: "s1",
      service_ids: ["sv1"],
      resource_id: null,
    });
    expect(body).not.toHaveProperty("booking_id");
    expect(typeof key).toBe("string");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/operacion/j7"));
    expect(t.count).toBe(1);
  });

  it("manda nombre, teléfono y patente cuando se cargan, y pide tamaño y servicio", async () => {
    sv.receiveJob.mockResolvedValue(job({ id: "j7" }));
    renderUi(<WalkInForm />);
    await screen.findByText("Tamaño del vehículo");
    await userEvent.click(screen.getByRole("button", { name: "LLEGÓ" }));
    expect(await screen.findByText("Elegí el tamaño del vehículo")).toBeInTheDocument();
    expect(screen.getByText("Elegí al menos un servicio")).toBeInTheDocument();
    expect(sv.receiveJob).not.toHaveBeenCalled();
    await fill();
    await userEvent.type(screen.getByLabelText("Cliente"), "Cliente Demo");
    await userEvent.type(screen.getByLabelText("Teléfono"), "+54 9 351 000-0001");
    await userEvent.type(screen.getByLabelText("Patente"), "AA000AA");
    await userEvent.click(screen.getByRole("button", { name: "LLEGÓ" }));
    await waitFor(() => expect(sv.receiveJob).toHaveBeenCalledTimes(1));
    expect(sv.receiveJob.mock.calls[0]![0]).toMatchObject({
      customer_name: "Cliente Demo",
      customer_phone: "+54 9 351 000-0001",
      plate: "AA000AA",
    });
  });

  it("un teléfono inválido (409/422) se explica y lo cargado queda", async () => {
    sv.receiveJob.mockRejectedValue(apiError(422, "INVALID_PHONE"));
    renderUi(<WalkInForm />);
    await fill();
    await userEvent.type(screen.getByLabelText("Cliente"), "Cliente Demo");
    await userEvent.click(screen.getByRole("button", { name: "LLEGÓ" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/teléfono no es válido/);
    expect(screen.getByLabelText("Cliente")).toHaveValue("Cliente Demo");
    expect(push).not.toHaveBeenCalled();
  });
});

describe("Turno solo a mano", () => {
  async function fill() {
    await screen.findByText("Tamaño del vehículo");
    await userEvent.selectOptions(screen.getByLabelText("Tamaño del vehículo"), "s1");
    await userEvent.click(screen.getByRole("button", { name: "Lavado completo" }));
    await userEvent.selectOptions(screen.getByLabelText("Puesto"), "r1");
    fireEvent.change(screen.getByLabelText("Día"), { target: { value: "2030-11-25" } });
    fireEvent.change(screen.getByLabelText("Hora de inicio"), { target: { value: "10:00" } });
  }

  it("crea el turno con la hora del negocio convertida a UTC y vuelve a la agenda", async () => {
    sv.createManualBooking.mockResolvedValue({ booking: {}, deposit_paid_cents: 0 } as never);
    renderUi(<ManualBookingForm />);
    await fill();
    await userEvent.type(screen.getByLabelText("Cliente"), "Cliente Demo");
    await userEvent.click(screen.getByRole("button", { name: "Cargar turno" }));
    await waitFor(() => expect(sv.createManualBooking).toHaveBeenCalledTimes(1));
    const [body, key] = sv.createManualBooking.mock.calls[0]!;
    expect(body).toMatchObject({
      customer_name: "Cliente Demo",
      plate: null,
      resource_id: "r1",
      service_ids: ["sv1"],
      start_at: "2030-11-25T13:00:00.000Z", // 10:00 en Córdoba (UTC-3)
      deposit: null,
      channel: "CARGA_MANUAL",
    });
    expect(typeof key).toBe("string");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/agenda"));
  });

  it("con seña manda el medio por defecto; sin elegir puesto ni hora lo pide", async () => {
    sv.createManualBooking.mockResolvedValue({ booking: {}, deposit_paid_cents: 1 } as never);
    renderUi(<ManualBookingForm />);
    await screen.findByText("Tamaño del vehículo");
    await userEvent.click(screen.getByRole("button", { name: "Cargar turno" }));
    expect(await screen.findByText("Elegí el puesto")).toBeInTheDocument();
    expect(screen.getByText("Elegí la hora")).toBeInTheDocument();
    await fill();
    await userEvent.click(screen.getByRole("button", { name: /Sin seña/ }));
    await userEvent.click(screen.getByRole("button", { name: "Cargar turno" }));
    await waitFor(() => expect(sv.createManualBooking).toHaveBeenCalledTimes(1));
    expect(sv.createManualBooking.mock.calls[0]![0].deposit).toEqual({ payment_method_id: "m1" });
  });

  it("un 409 SLOT_TAKEN se muestra en español sin perder lo cargado", async () => {
    sv.createManualBooking.mockRejectedValue(apiError(409, "SLOT_TAKEN"));
    renderUi(<ManualBookingForm />);
    await fill();
    await userEvent.type(screen.getByLabelText("Cliente"), "Cliente Demo");
    await userEvent.click(screen.getByRole("button", { name: "Cargar turno" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/horario ya no está libre/);
    expect(screen.getByLabelText("Cliente")).toHaveValue("Cliente Demo");
    expect(screen.getByLabelText("Hora de inicio")).toHaveValue("10:00");
    expect(screen.getByRole("button", { name: "Lavado completo" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(push).not.toHaveBeenCalled();
  });

  it("un 422 explica las reglas del turno a mano", async () => {
    sv.createManualBooking.mockRejectedValue(apiError(422, "VALIDATION_ERROR"));
    renderUi(<ManualBookingForm />);
    await fill();
    await userEvent.click(screen.getByRole("button", { name: "Cargar turno" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/tiene que ser a futuro/);
  });
});

describe("Carga retrospectiva", () => {
  async function fill() {
    await screen.findByText("Tamaño del vehículo");
    await userEvent.selectOptions(screen.getByLabelText("Tamaño del vehículo"), "s1");
    await userEvent.click(screen.getByRole("button", { name: "Lavado completo" }));
    fireEvent.change(screen.getByLabelText("Día"), { target: { value: "2026-11-16" } });
    fireEvent.change(screen.getByLabelText("Inicio"), { target: { value: "09:00" } });
    fireEvent.change(screen.getByLabelText("Fin"), { target: { value: "10:30" } });
  }

  it("carga un trabajo cobrado con horas reales y cae en la agenda de su día", async () => {
    sv.recordRetrospective.mockResolvedValue(job({ entry_mode: "RETROSPECTIVA" }));
    renderUi(<RetrospectiveForm />);
    await fill();
    await userEvent.click(screen.getByRole("button", { name: "Cargar trabajo" }));
    await waitFor(() => expect(sv.recordRetrospective).toHaveBeenCalledTimes(1));
    const [body, key] = sv.recordRetrospective.mock.calls[0]!;
    expect(body).toMatchObject({
      customer_name: null,
      plate: null,
      started_at: "2026-11-16T12:00:00.000Z",
      finished_at: "2026-11-16T13:30:00.000Z",
      payment: { payment_method_id: "m1" }, // el medio por defecto, sin importe: el total
    });
    expect(typeof key).toBe("string");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/agenda?date=2026-11-16"));
  });

  it("con el cobro pendiente manda payment null", async () => {
    sv.recordRetrospective.mockResolvedValue(job());
    renderUi(<RetrospectiveForm />);
    await fill();
    await userEvent.click(screen.getByRole("button", { name: /Cobrado/ }));
    expect(screen.queryByLabelText("Medio de pago")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cargar trabajo" }));
    await waitFor(() => expect(sv.recordRetrospective).toHaveBeenCalledTimes(1));
    expect(sv.recordRetrospective.mock.calls[0]![0].payment).toBeNull();
  });

  it("manda el importe parcial cuando se escribe", async () => {
    sv.recordRetrospective.mockResolvedValue(job());
    renderUi(<RetrospectiveForm />);
    await fill();
    await userEvent.type(screen.getByLabelText("Importe cobrado"), "5.000");
    await userEvent.selectOptions(screen.getByLabelText("Medio de pago"), "m2");
    await userEvent.click(screen.getByRole("button", { name: "Cargar trabajo" }));
    await waitFor(() => expect(sv.recordRetrospective).toHaveBeenCalledTimes(1));
    expect(sv.recordRetrospective.mock.calls[0]![0].payment).toEqual({
      payment_method_id: "m2",
      amount_cents: 500_000,
    });
  });

  it("rechaza un fin anterior al inicio sin llamar al servidor", async () => {
    renderUi(<RetrospectiveForm />);
    await fill();
    fireEvent.change(screen.getByLabelText("Fin"), { target: { value: "08:00" } });
    await userEvent.click(screen.getByRole("button", { name: "Cargar trabajo" }));
    expect(await screen.findByText("El fin no puede ser anterior al inicio")).toBeInTheDocument();
    expect(sv.recordRetrospective).not.toHaveBeenCalled();
  });

  it("un 422 (hora a futuro) se explica y lo cargado queda", async () => {
    sv.recordRetrospective.mockRejectedValue(apiError(422, "VALIDATION_ERROR"));
    renderUi(<RetrospectiveForm />);
    await fill();
    await userEvent.type(screen.getByLabelText("Cliente"), "Cliente Demo");
    await userEvent.click(screen.getByRole("button", { name: "Cargar trabajo" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/ninguno puede ser a futuro/);
    expect(screen.getByLabelText("Cliente")).toHaveValue("Cliente Demo");
    expect(screen.getByLabelText("Inicio")).toHaveValue("09:00");
  });

  it("sin señal queda en la cola y avisa", async () => {
    sv.recordRetrospective.mockRejectedValue(networkError());
    renderUi(<RetrospectiveForm />);
    await fill();
    await userEvent.click(screen.getByRole("button", { name: "Cargar trabajo" }));
    await waitFor(() => expect(useOfflineQueueStore.getState().items).toHaveLength(1));
    expect(useOfflineQueueStore.getState().items[0]!.kind).toBe("operacion.retrospective");
    expect(useToastStore.getState().toasts.map((x) => x.message)).toEqual([
      expect.stringMatching(/Sin conexión/),
    ]);
  });
});

describe("Recepción completa (opcional)", () => {
  it("guarda la recepción y vuelve al trabajo; es JOBS_OPERAR", async () => {
    sv.recordInspection.mockResolvedValue(job());
    renderUi(<InspectionPageClient id="j1" />);
    await userEvent.selectOptions(await screen.findByLabelText("Suciedad"), "BARRO");
    await userEvent.type(screen.getByLabelText("Daños previos"), "Rayón en la puerta");
    await userEvent.click(screen.getByRole("button", { name: "Guardar recepción" }));
    await waitFor(() => expect(sv.recordInspection).toHaveBeenCalledTimes(1));
    const [id, body] = sv.recordInspection.mock.calls[0]!;
    expect(id).toBe("j1");
    expect(body).toMatchObject({
      dirt_level: "BARRO",
      valuables: null,
      pre_existing_damage: "Rayón en la puerta",
    });
    await waitFor(() => expect(push).toHaveBeenCalledWith("/operacion/j1"));
  });

  it("sin JOBS_OPERAR no se abre", async () => {
    as(["AGENDA_VER"]);
    renderUi(<InspectionPageClient id="j1" />);
    expect(await screen.findByText(/No tenés acceso a la recepción/)).toBeInTheDocument();
  });
});

describe("Targets táctiles ≥ 44 px (A8)", () => {
  it("exporta una constante compartida y detecta controles chicos", () => {
    expect(TOUCH_TARGET).toBe("min-h-11 min-w-11");
    document.body.innerHTML = '<button class="h-8">x</button><button class="min-h-11">y</button>';
    expect(undersizedControls(document.body)).toHaveLength(1);
    document.body.innerHTML = "";
  });

  it("la agenda, el job (con cobro abierto y diálogos), las cargas y la recepción cumplen", async () => {
    const mounted: [string, React.ReactElement, () => Promise<unknown>][] = [
      [
        "agenda",
        <AgendaPageClient key="a" />,
        () => screen.findByRole("region", { name: /Puesto 1/ }),
      ],
      [
        "job",
        <JobPageClient key="j" id="j1" />,
        () => screen.findByRole("button", { name: "INICIAR" }),
      ],
      ["walk-in", <WalkInForm key="w" />, () => screen.findByText("Tamaño del vehículo")],
      [
        "turno a mano",
        <ManualBookingForm key="m" />,
        () => screen.findByText("Tamaño del vehículo"),
      ],
      ["retrospectiva", <RetrospectiveForm key="r" />, () => screen.findByLabelText("Fin")],
      [
        "recepción",
        <InspectionPageClient key="i" id="j1" />,
        () => screen.findByLabelText("Suciedad"),
      ],
    ];
    for (const [name, ui, ready] of mounted) {
      const { unmount } = renderUi(ui);
      await ready();
      expect({ name, chicos: undersizedControls(document.body).map((e) => e.outerHTML) }).toEqual({
        name,
        chicos: [],
      });
      unmount();
    }
  });

  it("el job finalizado con el cobro abierto, el cobro anulable y el diálogo cumplen", async () => {
    sv.getJob.mockResolvedValue(
      job({
        status: "FINALIZADO",
        payments: [
          {
            id: "pay1",
            kind: "SALDO",
            amount_cents: 100,
            occurred_at: "2030-11-25T14:30:00Z",
            payment_method_id: "m1",
            voided: false,
          },
        ],
        cancel_delay_from: null,
      }),
    );
    renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await screen.findByRole("button", { name: /COBRAR/ }));
    expect(undersizedControls(document.body)).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: "Anular cobro" }));
    await screen.findByRole("dialog");
    expect(undersizedControls(document.body)).toEqual([]);
  });
});

describe("Utilidades", () => {
  it("elige el efectivo como medio por defecto, o el primero que cobre", () => {
    expect(defaultPaymentMethod([tarjeta, efectivo])?.code).toBe("EFECTIVO");
    expect(defaultPaymentMethod([tarjeta])?.code).toBe("TARJETA");
    expect(defaultPaymentMethod([{ ...tarjeta, for_income: false }])).toBeNull();
    expect(defaultPaymentMethod(undefined)).toBeNull();
  });

  it("traduce los errores del contrato y cae en el genérico por estado", () => {
    expect(operacionErrorMessage(apiError(409, "SLOT_TAKEN"), "manualBooking")).toMatch(/horario/);
    expect(operacionErrorMessage(apiError(409, "BLOCKED"), "manualBooking")).toMatch(/bloqueo/);
    expect(operacionErrorMessage(apiError(409, "CONFLICT"), "pay")).toMatch(/otra acción/);
    expect(operacionErrorMessage(apiError(403, "FORBIDDEN"), "pay")).toMatch(/permiso/);
    expect(operacionErrorMessage(apiError(422, "VALIDATION_ERROR"), "pickUp")).toMatch(/deuda/);
    expect(operacionErrorMessage(apiError(422, "VALIDATION_ERROR"), "cancelDelay")).toMatch(
      /tolerancia/,
    );
  });
});
