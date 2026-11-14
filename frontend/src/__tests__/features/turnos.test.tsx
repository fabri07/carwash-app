import { act, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import {
  AxiosError,
  AxiosHeaders,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import { InboxBadge } from "@/features/turnos/InboxBadge";
import { COUNTS_REFRESH_MS, inboxErrorMessage, runAction } from "@/features/turnos/queries";
import { addDays, formatDay, whatsappLink, zonedToUtcIso } from "@/features/turnos/time";
import { TurnosPageClient } from "@/features/turnos/TurnosPageClient";
import { getAvailability } from "@/services/agenda.service";
import {
  approveBooking,
  cancelBooking,
  confirmDeposit,
  getBookingCounts,
  listBookings,
  listDepositCases,
  markIncident,
  markNoShow,
  rejectBooking,
  rescheduleBooking,
  resolveDepositCase,
} from "@/services/bookings.service";
import { getSettings, paymentMethodsApi, resourcesApi } from "@/services/config.service";
import { useAuthStore } from "@/stores/authStore";
import { useToastStore } from "@/stores/toastStore";
import type { DepositCase, InboxBooking, PaymentMethod, Resource } from "@/types/api";

jest.mock("@/services/bookings.service", () => ({
  listBookings: jest.fn(),
  getBookingCounts: jest.fn(),
  getBooking: jest.fn(),
  confirmDeposit: jest.fn(),
  approveBooking: jest.fn(),
  rejectBooking: jest.fn(),
  cancelBooking: jest.fn(),
  markNoShow: jest.fn(),
  rescheduleBooking: jest.fn(),
  markIncident: jest.fn(),
  removeIncidentMark: jest.fn(),
  listDepositCases: jest.fn(),
  resolveDepositCase: jest.fn(),
}));
jest.mock("@/services/agenda.service", () => ({ getAvailability: jest.fn() }));
jest.mock("@/services/config.service", () => ({
  getSettings: jest.fn(),
  resourcesApi: { list: jest.fn() },
  paymentMethodsApi: { list: jest.fn() },
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

const TZ = "America/Argentina/Cordoba";
const stamp = {
  tenant_id: "t1",
  created_at: "2026-11-05T00:00:00Z",
  updated_at: "2026-11-05T00:00:00Z",
};
const puestos: Resource[] = [
  { id: "r1", name: "Puesto 1", sort_order: 0, ...stamp },
  { id: "r2", name: "Puesto 2", sort_order: 1, ...stamp },
];
const efectivo = {
  id: "m1",
  code: "EFECTIVO",
  label: "Efectivo",
  for_income: true,
  for_expense: true,
  commission_bps: 0,
  settlement_account: null,
  ...stamp,
} as PaymentMethod;

function turno(over: Partial<InboxBooking> = {}): InboxBooking {
  return {
    id: "b1",
    code: "TUR-ABC234",
    status: "CONFIRMADO",
    source: "TURNERO_WEB",
    channel: "TURNERO_WEB",
    resource_id: "r1",
    // Lunes 25/11/2030 10:00 en Córdoba (futuro).
    start_at: "2030-11-25T13:00:00Z",
    end_at: "2030-11-25T14:00:00Z",
    hold_expires_at: null,
    duration_min: 60,
    vehicle_size_id: "s1",
    price_cents: 2_000_000,
    deposit_required_cents: 0,
    deposit_paid_cents: 0,
    customer: { id: "c1", name: "Cliente Demo", phone_e164: "+5493510000001" },
    vehicle_plate: "AA000AA",
    services: [
      {
        service_id: "sv1",
        name: "Lavado completo",
        duration_min: 60,
        price_cents: 2_000_000,
        pricing_mode: "PRECIO_FIJO",
      },
    ],
    notes: null,
    created_at: "2026-11-05T00:00:00Z",
    ...over,
  } as InboxBooking;
}

function page<T>(items: T[]) {
  return { items, total: items.length, limit: 200, offset: 0, has_more: false };
}

const owner = {
  id: "u1",
  email: "dueno@example.com",
  username: null,
  role: "OWNER" as const,
  tenant_id: "t1",
  permission_profile_id: null,
};

/** `listBookings` según los estados pedidos (cada pestaña pide los suyos). */
function bookingsBy(byStatus: Partial<Record<string, InboxBooking[]>>) {
  (listBookings as jest.Mock).mockImplementation(async (f: { status?: string[] }) =>
    page((f.status ?? []).flatMap((s) => byStatus[s] ?? [])),
  );
}

function renderPage(ui = <TurnosPageClient />) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

async function openTab(name: RegExp) {
  await userEvent.click(await screen.findByRole("tab", { name }));
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
  useAuthStore.setState({ user: owner, permissions: [], permissionsLoaded: true });
  (getSettings as jest.Mock).mockResolvedValue({
    timezone: TZ,
    deposit_instructions: "Alias: lavadero.demo",
  });
  (resourcesApi.list as jest.Mock).mockResolvedValue(page(puestos));
  (paymentMethodsApi.list as jest.Mock).mockResolvedValue(page([efectivo]));
  (getBookingCounts as jest.Mock).mockResolvedValue({
    pending_deposit: 1,
    in_review: 2,
    deposit_cases: 0,
    total: 3,
  });
  (listDepositCases as jest.Mock).mockResolvedValue(page([]));
  bookingsBy({ CONFIRMADO: [turno()] });
});

describe("Bandeja: Próximos", () => {
  it("lista los turnos con cliente, WhatsApp y contadores en las pestañas", async () => {
    renderPage();
    const fila = await screen.findByRole("listitem", { name: "Turno TUR-ABC234" });
    expect(fila).toHaveTextContent("Cliente Demo · AA000AA · Lavado completo");
    expect(fila).toHaveTextContent("Puesto 1");
    expect(within(fila).getByRole("link", { name: /whatsapp/i })).toHaveAttribute(
      "href",
      "https://wa.me/5493510000001",
    );
    expect(await screen.findByRole("tab", { name: /lista de espera\s*2/i })).toBeInTheDocument();
    const pedido = (listBookings as jest.Mock).mock.calls[0]![0];
    expect(pedido.status).toEqual(["CONFIRMADO", "PENDIENTE_SEÑA", "RECIBIDO"]);
    expect(pedido.from).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("cancela con motivo y avisa", async () => {
    (cancelBooking as jest.Mock).mockResolvedValueOnce({});
    renderPage();
    const fila = await screen.findByRole("listitem", { name: "Turno TUR-ABC234" });
    await userEvent.click(within(fila).getByRole("button", { name: "Cancelar" }));
    const dialog = await screen.findByRole("dialog", { name: "Cancelar el turno" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancelar turno" }));
    expect(await within(dialog).findByText("Escribí el motivo.")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Lluvia");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancelar turno" }));
    await waitFor(() => expect(cancelBooking).toHaveBeenCalledTimes(1));
    const [id, body, key] = (cancelBooking as jest.Mock).mock.calls[0]!;
    expect([id, body, typeof key]).toEqual(["b1", { reason: "Lluvia" }, "string"]);
    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.message)).toContain(
        "Turno cancelado. Avisale al cliente.",
      ),
    );
  });

  it("marca una incidencia y el no asistió de un turno que ya empezó", async () => {
    bookingsBy({
      CONFIRMADO: [turno({ id: "b2", code: "TUR-PASADO", start_at: "2020-01-01T13:00:00Z" })],
    });
    (markIncident as jest.Mock).mockResolvedValueOnce({});
    (markNoShow as jest.Mock).mockResolvedValueOnce({});
    renderPage();
    const fila = await screen.findByRole("listitem", { name: "Turno TUR-PASADO" });
    await userEvent.click(within(fila).getByRole("button", { name: "Marcar incidencia" }));
    const dialog = await screen.findByRole("dialog", { name: /incidencia/i });
    await userEvent.type(within(dialog).getByLabelText("Qué pasó"), "No avisó");
    await userEvent.click(within(dialog).getByRole("button", { name: "Marcar incidencia" }));
    await waitFor(() =>
      expect(markIncident).toHaveBeenCalledWith("b2", { reason: "No avisó" }, expect.any(String)),
    );
    await userEvent.click(within(fila).getByRole("button", { name: "No asistió" }));
    const confirmar = await screen.findByRole("dialog", { name: /no vino/i });
    await userEvent.click(within(confirmar).getByRole("button", { name: "No asistió" }));
    await waitFor(() => expect(markNoShow).toHaveBeenCalledWith("b2", expect.any(String)));
  });

  it("reprograma eligiendo un horario libre y avisa si quedó fuera de reglas", async () => {
    (getAvailability as jest.Mock).mockResolvedValue({
      date: "2030-11-25",
      timezone: TZ,
      duration_min: 60,
      slots: [
        {
          start_at: "2030-11-25T15:00:00Z",
          end_at: "2030-11-25T16:00:00Z",
          resource_ids: ["r1"],
        },
      ],
    });
    (rescheduleBooking as jest.Mock).mockResolvedValueOnce({
      booking: turno(),
      outside_rules: true,
    });
    renderPage();
    const fila = await screen.findByRole("listitem", { name: "Turno TUR-ABC234" });
    await userEvent.click(within(fila).getByRole("button", { name: "Reprogramar" }));
    const dialog = await screen.findByRole("dialog", { name: /reprogramar/i });
    await userEvent.click(await within(dialog).findByRole("button", { name: "12:00" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar cambio" }));
    await waitFor(() =>
      expect(rescheduleBooking).toHaveBeenCalledWith("b1", {
        start_at: "2030-11-25T15:00:00.000Z",
      }),
    );
    expect((getAvailability as jest.Mock).mock.calls[0]![0]).toEqual({
      date: "2030-11-25",
      vehicle_size_id: "s1",
      service_ids: ["sv1"],
    });
    await waitFor(() =>
      expect(
        useToastStore
          .getState()
          .toasts.map((t) => t.message)
          .join(),
      ).toMatch(/fuera del horario/),
    );
  });

  it("al reprogramar, un horario tomado queda como error en el diálogo", async () => {
    (getAvailability as jest.Mock).mockResolvedValue({
      date: "2030-11-25",
      timezone: TZ,
      duration_min: 60,
      slots: [],
    });
    (rescheduleBooking as jest.Mock).mockRejectedValueOnce(apiError(409, "SLOT_TAKEN"));
    renderPage();
    const fila = await screen.findByRole("listitem", { name: "Turno TUR-ABC234" });
    await userEvent.click(within(fila).getByRole("button", { name: "Reprogramar" }));
    const dialog = await screen.findByRole("dialog", { name: /reprogramar/i });
    expect(await within(dialog).findByText(/no hay horarios libres/i)).toBeInTheDocument();
    // Sin cambios no se manda nada.
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar cambio" }));
    expect(await within(dialog).findByText("Cambiá el horario o el puesto.")).toBeInTheDocument();
    await userEvent.selectOptions(within(dialog).getByLabelText("Puesto"), "r2");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar cambio" }));
    await waitFor(() =>
      expect(rescheduleBooking).toHaveBeenCalledWith("b1", { resource_id: "r2" }),
    );
    expect(await within(dialog).findByText(/ya no está libre/i)).toBeInTheDocument();
  });

  it("sin TURNOS_GESTIONAR se ve pero no hay botones", async () => {
    useAuthStore.setState({
      user: { ...owner, role: "STAFF" as const, permission_profile_id: "p1" },
      permissions: ["AGENDA_VER"],
      permissionsLoaded: true,
    });
    renderPage();
    const fila = await screen.findByRole("listitem", { name: "Turno TUR-ABC234" });
    expect(within(fila).queryByRole("button")).toBeNull();
  });

  it("sin AGENDA_VER no hay acceso", async () => {
    useAuthStore.setState({
      user: { ...owner, role: "STAFF" as const, permission_profile_id: "p1" },
      permissions: ["COBROS_REGISTRAR"],
      permissionsLoaded: true,
    });
    renderPage();
    expect(await screen.findByText("No tenés acceso a los turnos")).toBeInTheDocument();
    expect(listBookings).not.toHaveBeenCalled();
  });

  it("muestra el error si la lista no carga", async () => {
    (listBookings as jest.Mock).mockRejectedValue(new Error("boom"));
    renderPage();
    expect(await screen.findByText(/no se pudo cargar la bandeja/i)).toBeInTheDocument();
  });
});

describe("Bandeja: señas y lista de espera", () => {
  const conSena = turno({
    id: "b3",
    code: "TUR-SENA",
    status: "PENDIENTE_SEÑA",
    deposit_required_cents: 600_000,
    hold_expires_at: "2030-11-25T12:30:00Z",
  });

  it("confirma la seña con medio, importe y una clave", async () => {
    bookingsBy({ PENDIENTE_SEÑA: [conSena] });
    (confirmDeposit as jest.Mock).mockResolvedValueOnce({ booking: conSena, late: true });
    renderPage();
    await openTab(/pendientes de seña/i);
    expect(await screen.findByText(/alias: lavadero\.demo/i)).toBeInTheDocument();
    const fila = await screen.findByRole("listitem", { name: "Turno TUR-SENA" });
    expect(fila).toHaveTextContent("Retenido hasta las 09:30");
    await userEvent.click(within(fila).getByRole("button", { name: "Confirmar seña" }));
    const dialog = await screen.findByRole("dialog", { name: /confirmar la seña/i });
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirmar seña" }));
    await waitFor(() => expect(confirmDeposit).toHaveBeenCalledTimes(1));
    const [id, body, key] = (confirmDeposit as jest.Mock).mock.calls[0]!;
    expect(id).toBe("b3");
    expect(body).toEqual({ payment_method_id: "m1", amount_cents: 600_000 });
    expect(typeof key).toBe("string");
    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.message)).toContain(
        "Seña registrada: el turno se confirmó tarde.",
      ),
    );
  });

  it("un vencido sin seña no aparece; uno con seña ofrece la seña tardía", async () => {
    bookingsBy({
      VENCIDO: [
        turno({ id: "b4", code: "TUR-SIN", status: "VENCIDO" }),
        turno({ id: "b5", code: "TUR-TARDE", status: "VENCIDO", deposit_required_cents: 1 }),
      ],
    });
    renderPage();
    await openTab(/pendientes de seña/i);
    const fila = await screen.findByRole("listitem", { name: "Turno TUR-TARDE" });
    expect(within(fila).getByRole("button", { name: "Confirmar seña tardía" })).toBeInTheDocument();
    expect(screen.queryByRole("listitem", { name: "Turno TUR-SIN" })).toBeNull();
  });

  it("aprueba y rechaza pedidos de la lista de espera", async () => {
    const pedido = turno({ id: "b6", code: "TUR-ESPERA", status: "PENDIENTE_REVISION" });
    bookingsBy({ PENDIENTE_REVISION: [pedido] });
    (approveBooking as jest.Mock).mockRejectedValueOnce(apiError(409, "SLOT_TAKEN"));
    (rejectBooking as jest.Mock).mockResolvedValueOnce({});
    renderPage();
    await openTab(/lista de espera/i);
    const fila = await screen.findByRole("listitem", { name: "Turno TUR-ESPERA" });
    await userEvent.click(within(fila).getByRole("button", { name: "Aprobar" }));
    const aprobar = await screen.findByRole("dialog", { name: /aprobar/i });
    await userEvent.click(within(aprobar).getByRole("button", { name: "Aprobar" }));
    expect(await within(aprobar).findByText(/ya no está libre/i)).toBeInTheDocument();
    await userEvent.click(within(aprobar).getByRole("button", { name: "Cancelar" }));

    await userEvent.click(within(fila).getByRole("button", { name: "Rechazar" }));
    const rechazar = await screen.findByRole("dialog", { name: "Rechazar el pedido" });
    await userEvent.type(within(rechazar).getByLabelText("Motivo"), "Sin lugar");
    await userEvent.click(within(rechazar).getByRole("button", { name: "Rechazar" }));
    await waitFor(() =>
      expect(rejectBooking).toHaveBeenCalledWith("b6", { reason: "Sin lugar" }, expect.any(String)),
    );
  });

  it("resuelve una seña devolviéndola", async () => {
    const caso: DepositCase = {
      cancellation: {
        id: "x1",
        booking_id: "b7",
        initiator: "NEGOCIO",
        classification: "OPERATIVA",
        requested_at: "2030-11-20T13:00:00Z",
        anticipation_min: 100,
        reason: "Lluvia",
        previous_status: "CONFIRMADO",
        resulting_status: "CANCELADO_OPERATIVO",
        deposit_paid_cents: 600_000,
        deposit_status: "REPROGRAMACION_O_DEVOLUCION_PENDIENTE",
        resolved_at: null,
        resolution_reason: null,
        refund_payment_id: null,
        ...stamp,
      },
      booking: turno({ id: "b7", code: "TUR-CASO", status: "CANCELADO_OPERATIVO" }),
    } as DepositCase;
    (listDepositCases as jest.Mock).mockResolvedValue(page([caso]));
    (resolveDepositCase as jest.Mock).mockResolvedValueOnce({});
    renderPage();
    await openTab(/señas a resolver/i);
    const fila = await screen.findByRole("listitem", { name: "Turno TUR-CASO" });
    expect(fila).toHaveTextContent("Reprogramar o devolver");
    await userEvent.click(within(fila).getByRole("button", { name: "Resolver" }));
    const dialog = await screen.findByRole("dialog", { name: /resolver la seña/i });
    expect(within(dialog).queryByRole("option", { name: "Retenerla" })).toBeNull(); // solo se retiene si canceló después de la hora
    await userEvent.type(within(dialog).getByLabelText("Motivo"), "Transferido");
    await userEvent.click(within(dialog).getByRole("button", { name: "Resolver" }));
    await waitFor(() => expect(resolveDepositCase).toHaveBeenCalledTimes(1));
    const [id, body, key] = (resolveDepositCase as jest.Mock).mock.calls[0]!;
    expect(id).toBe("x1");
    expect(body).toEqual({
      status: "DEVUELTA",
      reason: "Transferido",
      refund: { payment_method_id: "m1", amount_cents: 600_000 },
    });
    expect(typeof key).toBe("string");
  });
});

describe("Contador del menú", () => {
  it("muestra el total y se refresca cada minuto", async () => {
    jest.useFakeTimers({ doNotFake: ["nextTick", "setImmediate"] });
    try {
      renderPage(<InboxBadge collapsed={false} />);
      expect(await screen.findByTestId("inbox-badge")).toHaveTextContent("3");
      (getBookingCounts as jest.Mock).mockResolvedValue({
        pending_deposit: 0,
        in_review: 0,
        deposit_cases: 0,
        total: 0,
      });
      await act(async () => {
        jest.advanceTimersByTime(COUNTS_REFRESH_MS + 10);
      });
      await waitFor(() => expect(screen.queryByTestId("inbox-badge")).toBeNull());
      expect((getBookingCounts as jest.Mock).mock.calls.length).toBeGreaterThanOrEqual(2);
    } finally {
      jest.useRealTimers();
    }
  });

  it("pasado de 99 muestra 99+", async () => {
    (getBookingCounts as jest.Mock).mockResolvedValue({
      pending_deposit: 150,
      in_review: 0,
      deposit_cases: 0,
      total: 150,
    });
    renderPage(<InboxBadge collapsed />);
    expect(await screen.findByTestId("inbox-badge")).toHaveTextContent("99+");
  });
});

describe("helpers de la bandeja", () => {
  it("convierte hora local del negocio a UTC y arma links", () => {
    expect(zonedToUtcIso("2030-11-25", "12:00", TZ)).toBe("2030-11-25T15:00:00.000Z");
    expect(zonedToUtcIso("2030-11-25", "23:30", TZ)).toBe("2030-11-26T02:30:00.000Z");
    expect(addDays("2030-12-31", 1)).toBe("2031-01-01");
    expect(whatsappLink("+54 9 351 000-0001")).toBe("https://wa.me/5493510000001");
    expect(whatsappLink(null)).toBeNull();
    expect(formatDay("2030-11-25T13:00:00Z", TZ)).toMatch(/25\/11/);
  });

  it("traduce los errores y trata el reenvío como hecho", async () => {
    expect(inboxErrorMessage(apiError(409, "BLOCKED"))).toMatch(/bloqueo/);
    expect(inboxErrorMessage(apiError(409, "CONFLICT"))).toMatch(/recargá/i);
    expect(inboxErrorMessage(apiError(422, "VALIDATION_ERROR"))).toMatch(/revisá/i);
    expect(inboxErrorMessage(apiError(403, "FORBIDDEN"))).toMatch(/permiso/);
    await expect(
      runAction(() => Promise.reject(apiError(409, "DUPLICATE_IDEMPOTENT"))),
    ).resolves.toBeUndefined();
    await expect(runAction(() => Promise.reject(apiError(409, "SLOT_TAKEN")))).rejects.toThrow(
      /ya no está libre/,
    );
  });
});
