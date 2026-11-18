/**
 * T3 (6.2) — Estados del servidor: 409 INVALID_TRANSITION / CONFLICT / SLOT_TAKEN, 422 por acción,
 * 403 y 404, 5xx. Ningún error muestra texto técnico, en inglés ni stack, y lo cargado se conserva.
 */
import { screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AgendaPageClient } from "@/features/operacion/AgendaPageClient";
import { JobPageClient } from "@/features/operacion/JobPageClient";
import { ManualBookingForm } from "@/features/operacion/ManualBookingForm";
import { RetrospectiveForm } from "@/features/operacion/RetrospectiveForm";
import { WalkInForm } from "@/features/operacion/WalkInForm";
import { useOfflineQueueStore } from "@/stores/offlineQueueStore";
import {
  apiError,
  createServer,
  goOffline,
  goOnline,
  job,
  queueItems,
  renderUi,
  resetUnits,
  TECHNICAL,
  type FakeServer,
} from "./harness";

const mockPush = jest.fn();
const mockReplace = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace }),
  useSearchParams: () => new URLSearchParams(),
}));
jest.mock("@/lib/api", () => ({
  API_URL: "http://localhost:8000",
  api: { get: jest.fn(), post: jest.fn() },
}));
jest.mock("@/services/catalog.service", () => ({ servicesApi: { list: jest.fn() } }));
jest.mock("@/services/config.service", () => ({
  getSettings: jest.fn(),
  resourcesApi: { list: jest.fn() },
  paymentMethodsApi: { list: jest.fn() },
  vehicleSizesApi: { list: jest.fn() },
}));

jest.setTimeout(30_000);

let server: FakeServer;
beforeEach(() => {
  resetUnits();
  server = createServer([job()]);
});

const LEAK =
  "Traceback (most recent call last): psycopg2.errors.UniqueViolation at app/services/job.py:88";

async function alertsText(): Promise<string> {
  const alerts = await screen.findAllByRole("alert");
  return alerts.map((a) => a.textContent ?? "").join(" | ");
}

const ACTIONS: {
  name: string;
  over: Partial<ReturnType<typeof job>>;
  url: RegExp;
  click: () => Promise<void>;
}[] = [
  {
    name: "INICIAR",
    over: { status: "PRESENTE" },
    url: /\/start$/,
    click: async () => {
      await userEvent.click(await screen.findByRole("button", { name: "INICIAR" }));
    },
  },
  {
    name: "FINALIZAR",
    over: { status: "EN_PROCESO" },
    url: /\/finish$/,
    click: async () => {
      await userEvent.click(await screen.findByRole("button", { name: "FINALIZAR" }));
    },
  },
  {
    name: "RETIRAR",
    over: { status: "COBRADO", balance_due_cents: 0, paid_cents: 2_000_000 },
    url: /\/pick-up$/,
    click: async () => {
      await userEvent.click(await screen.findByRole("button", { name: "RETIRAR" }));
    },
  },
  {
    name: "COBRAR",
    over: { status: "FINALIZADO" },
    url: /\/payments$/,
    click: async () => {
      await userEvent.click(await screen.findByRole("button", { name: /^COBRAR/ }));
      await userEvent.click(await screen.findByRole("button", { name: /CONFIRMAR COBRO/ }));
    },
  },
];

const ERRORS: { label: string; err: () => unknown; says: RegExp }[] = [
  {
    label: "409 INVALID_TRANSITION",
    err: () => apiError(409, "INVALID_TRANSITION", LEAK),
    says: /ya no corresponde.*no se perdió nada/s,
  },
  { label: "409 CONFLICT", err: () => apiError(409, "CONFLICT", LEAK), says: /otra acción/ },
  { label: "422 VALIDATION_ERROR", err: () => apiError(422, "VALIDATION_ERROR", LEAK), says: /\S/ },
  { label: "403 FORBIDDEN", err: () => apiError(403, "FORBIDDEN", LEAK), says: /permiso/ },
  { label: "404 NOT_FOUND", err: () => apiError(404, "NOT_FOUND", LEAK), says: /No se encontró/ },
];

describe("Cada acción × cada error del servidor (en línea)", () => {
  const cases = ACTIONS.flatMap((a) => ERRORS.map((e) => [a.name, e.label, a, e] as const));
  it.each(cases)(
    "%s con %s: mensaje en español, sin texto técnico, botón vivo",
    async (_n, _l, a, e) => {
      server = createServer([job(a.over)]);
      server.failPost.push({ match: a.url, error: e.err, times: -1 });
      renderUi(<JobPageClient id="j1" />);
      await a.click();
      const text = await alertsText();
      expect(text).toMatch(e.says);
      expect(text).not.toMatch(TECHNICAL);
      expect(document.body.textContent).not.toContain("psycopg2");
      expect(document.body.textContent).not.toContain("Traceback");
      // El error no deja la pantalla colgada: se puede volver a tocar.
      await waitFor(() => {
        const busy = screen
          .queryAllByRole("button")
          .filter((b) => (b as HTMLButtonElement).disabled);
        expect(
          busy.filter((b) => /INICIAR|FINALIZAR|RETIRAR|CONFIRMAR/.test(b.textContent ?? "")),
        ).toHaveLength(0);
      });
      expect(queueItems()).toHaveLength(0); // un rechazo definitivo no se encola
    },
  );

  it("un 409 INVALID_TRANSITION vuelve a leer el job (no muestra el estado viejo)", async () => {
    server.failPost.push({
      match: /\/start$/,
      error: () => apiError(409, "INVALID_TRANSITION"),
      times: -1,
    });
    renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("button", { name: "INICIAR" });
    const before = server.gets.filter((g) => g === "/jobs/j1").length;
    // Mientras tanto otro dispositivo lo inició.
    server.jobs.get("j1")!.status = "EN_PROCESO";
    await userEvent.click(screen.getByRole("button", { name: "INICIAR" }));
    await screen.findByRole("button", { name: "FINALIZAR" });
    expect(server.gets.filter((g) => g === "/jobs/j1").length).toBeGreaterThan(before);
  });

  it("un 422 al cobrar conserva el panel abierto y el importe que se había escrito", async () => {
    server = createServer([job({ status: "FINALIZADO" })]);
    server.failPost.push({
      match: /\/payments$/,
      error: () => apiError(422, "VALIDATION_ERROR", LEAK),
      times: -1,
    });
    renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await screen.findByRole("button", { name: /^COBRAR/ }));
    const amount = await screen.findByLabelText("Importe");
    await userEvent.clear(amount);
    await userEvent.type(amount, "5.000");
    await userEvent.click(screen.getByRole("button", { name: /CONFIRMAR COBRO/ }));
    expect(await alertsText()).toMatch(/Revisá el cobro/);
    expect(screen.getByLabelText("Importe")).toHaveValue("5.000,00");
    expect(screen.getByRole("button", { name: /CONFIRMAR COBRO/ })).toBeEnabled();
  });

  it("RETIRAR con deuda de un cliente nuevo (422) explica que hay que cobrar primero", async () => {
    server = createServer([job({ status: "FINALIZADO" })]);
    server.rejectPickUpWithDebt = true;
    renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await screen.findByRole("button", { name: "RETIRAR" }));
    expect(await alertsText()).toMatch(/con deuda.*cobrá el saldo/s);
  });

  it("cobrar de más (422) no deja el job con saldo distinto en pantalla", async () => {
    server = createServer([job({ status: "FINALIZADO" })]);
    renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await screen.findByRole("button", { name: /^COBRAR/ }));
    const amount = await screen.findByLabelText("Importe");
    await userEvent.clear(amount);
    await userEvent.type(amount, "99.999");
    // El front ya lo frena: confirmar queda deshabilitado y no sale ningún POST.
    expect(screen.getByRole("button", { name: /CONFIRMAR COBRO/ })).toBeDisabled();
    expect(server.attempts).toHaveLength(0);
    expect(await alertsText()).toMatch(/no superar el saldo/);
  });

  it("HALLAZGO: un 500 en línea no se presenta como 'Sin conexión' (el operador cree que no hay señal)", async () => {
    server.failPost.push({
      match: /\/start$/,
      error: () => apiError(500, undefined, LEAK),
      times: -1,
    });
    renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await screen.findByRole("button", { name: "INICIAR" }));
    await new Promise((r) => setTimeout(r, 100));
    const shown = document.body.textContent ?? "";
    expect(shown).not.toContain("psycopg2");
    expect(shown).not.toMatch(/Sin conexión/);
  });
});

describe("Errores al sincronizar lo que estaba en la cola", () => {
  it("un 422 de un toque guardado queda como fallido visible; reintentar usa la misma clave y la hora original", async () => {
    renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("button", { name: "INICIAR" });
    goOffline(server);
    await userEvent.click(screen.getByRole("button", { name: "INICIAR" }));
    await waitFor(() => expect(queueItems()).toHaveLength(1));
    const { id, occurredAt } = queueItems()[0]!;
    server.failPost.push({
      match: /\/start$/,
      error: () => apiError(422, "VALIDATION_ERROR", LEAK),
      times: 1,
    });
    goOnline(server);
    const banner = await screen.findByTestId("queue-banner");
    await waitFor(() => expect(banner).toHaveTextContent(/No se pudo enviar el inicio/));
    expect(banner.textContent).not.toMatch(TECHNICAL);
    expect(banner.textContent).not.toContain("psycopg2");
    expect(useOfflineQueueStore.getState().failed).toHaveLength(1);

    await userEvent.click(within(banner).getByRole("button", { name: "Reintentar" }));
    await waitFor(() => expect(server.received).toHaveLength(1));
    expect(server.received[0]).toMatchObject({ key: id, occurred_at: occurredAt });
    await waitFor(() => expect(screen.queryByTestId("queue-banner")).toBeNull());
  });

  it("un 403 y un 404 al sincronizar quedan visibles y descartables; un 409 CONFLICT no se reintenta solo", async () => {
    renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("button", { name: "INICIAR" });
    goOffline(server);
    await userEvent.click(screen.getByRole("button", { name: "INICIAR" }));
    await waitFor(() => expect(queueItems()).toHaveLength(1));
    server.failPost.push({
      match: /\/start$/,
      error: () => apiError(409, "CONFLICT", LEAK),
      times: -1,
    });
    goOnline(server);
    const banner = await screen.findByTestId("queue-banner");
    await waitFor(() => expect(banner).toHaveTextContent(/el servidor la rechazó/));
    const sent = server.attempts.filter((a) => a.url === "/jobs/j1/start").length;
    await new Promise((r) => setTimeout(r, 150));
    expect(server.attempts.filter((a) => a.url === "/jobs/j1/start").length).toBe(sent);
    await userEvent.click(within(banner).getByRole("button", { name: "Descartar" }));
    expect(screen.queryByTestId("queue-banner")).toBeNull();
  });

  it("el conflicto también se ve en la agenda, con la acción y la hora y sin datos del cliente", async () => {
    useOfflineQueueStore.setState({
      conflicts: [
        {
          id: "c1",
          userId: "u1",
          tenantId: "t1",
          kind: "operacion.start",
          payload: { jobId: "j1", body: {} },
          createdAt: "2030-11-25T13:05:00.000Z",
          occurredAt: "2030-11-25T13:05:00.000Z",
          attempts: 0,
          failedAt: "2030-11-25T18:00:00.000Z",
          status: 409,
        },
      ],
    });
    renderUi(<AgendaPageClient />);
    const banner = await screen.findByTestId("queue-banner");
    expect(banner).toHaveTextContent(/No se aplicó el inicio/);
    expect(banner.textContent).not.toMatch(/Cliente Demo|AA000AA|\+549351/);
    await userEvent.click(within(banner).getByRole("button", { name: "Entendido" }));
    expect(screen.queryByTestId("queue-banner")).toBeNull();
  });
});

describe("Pantallas de lectura: 403 y 404", () => {
  it("un job ajeno (404) y uno prohibido (403) dicen lo mismo y no filtran nada", async () => {
    server.failGet.push({ match: /^\/jobs\/j1$/, error: () => apiError(404, "NOT_FOUND", LEAK) });
    const a = renderUi(<JobPageClient id="j1" />);
    const t404 = await alertsText();
    a.unmount();
    server.failGet.length = 0;
    server.failGet.push({ match: /^\/jobs\/j1$/, error: () => apiError(403, "FORBIDDEN", LEAK) });
    renderUi(<JobPageClient id="j1" />);
    const t403 = await alertsText();
    expect(t403).toBe(t404);
    expect(t404).not.toMatch(
      TECHNICAL.source.includes("detail") ? /psycopg2|Traceback/ : TECHNICAL,
    );
  });

  it("HALLAZGO: la agenda con 403 del servidor no culpa a la conexión", async () => {
    server.failGet.push({ match: /^\/agenda\/day$/, error: () => apiError(403, "FORBIDDEN") });
    renderUi(<AgendaPageClient />);
    const text = await alertsText();
    expect(text).not.toMatch(/conexión/);
  });
});

async function pickBasics() {
  await userEvent.selectOptions(await screen.findByLabelText("Tamaño del vehículo"), "s1");
  await userEvent.click(screen.getByRole("button", { name: "Lavado completo" }));
}

describe("Formularios: el error no borra lo cargado", () => {
  it("turno a mano: 409 SLOT_TAKEN se muestra en español y conserva cada campo", async () => {
    server.failPost.push({
      match: /\/bookings\/manual$/,
      error: () => apiError(409, "SLOT_TAKEN", LEAK),
      times: -1,
    });
    renderUi(<ManualBookingForm />);
    await pickBasics();
    await userEvent.type(screen.getByLabelText("Cliente"), "Cliente Demo");
    await userEvent.type(screen.getByLabelText("Teléfono"), "+54 9 351 000-0001");
    await userEvent.type(screen.getByLabelText("Patente"), "AA000AA");
    await userEvent.selectOptions(screen.getByLabelText("Puesto"), "r2");
    fireEvent.change(screen.getByLabelText("Día"), { target: { value: "2030-11-25" } });
    fireEvent.change(screen.getByLabelText("Hora de inicio"), { target: { value: "10:00" } });
    await userEvent.click(screen.getByRole("button", { name: "Cargar turno" }));
    expect(await alertsText()).toMatch(/horario ya no está libre/);
    expect(document.body.textContent).not.toContain("psycopg2");
    expect(screen.getByLabelText("Cliente")).toHaveValue("Cliente Demo");
    expect(screen.getByLabelText("Teléfono")).toHaveValue("+54 9 351 000-0001");
    expect(screen.getByLabelText("Patente")).toHaveValue("AA000AA");
    expect(screen.getByLabelText("Puesto")).toHaveValue("r2");
    expect(screen.getByLabelText("Día")).toHaveValue("2030-11-25");
    expect(screen.getByLabelText("Hora de inicio")).toHaveValue("10:00");
    expect(screen.getByRole("button", { name: "Lavado completo" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Cargar turno" })).toBeEnabled();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it.each([
    ["422 VALIDATION_ERROR", () => apiError(422, "VALIDATION_ERROR", LEAK), /Revisá el turno/],
    ["409 BLOCKED", () => apiError(409, "BLOCKED", LEAK), /bloqueo/],
    ["409 INVALID_PHONE", () => apiError(409, "INVALID_PHONE", LEAK), /teléfono no es válido/],
    ["403", () => apiError(403, "FORBIDDEN", LEAK), /permiso/],
    ["404", () => apiError(404, "NOT_FOUND", LEAK), /No se encontró/],
  ])("turno a mano con %s: mensaje en español sin texto técnico", async (_l, err, says) => {
    server.failPost.push({ match: /\/bookings\/manual$/, error: err, times: -1 });
    renderUi(<ManualBookingForm />);
    await pickBasics();
    await userEvent.selectOptions(screen.getByLabelText("Puesto"), "r1");
    fireEvent.change(screen.getByLabelText("Día"), { target: { value: "2030-11-25" } });
    fireEvent.change(screen.getByLabelText("Hora de inicio"), { target: { value: "10:00" } });
    await userEvent.click(screen.getByRole("button", { name: "Cargar turno" }));
    const text = await alertsText();
    expect(text).toMatch(says);
    expect(text).not.toMatch(/Traceback|psycopg|VALIDATION_ERROR|SLOT_TAKEN|INVALID_PHONE|BLOCKED/);
  });

  it("carga retrospectiva: 422 de horas inválidas se explica y conserva todo", async () => {
    server.failPost.push({
      match: /\/jobs\/retrospective$/,
      error: () => apiError(422, "VALIDATION_ERROR", LEAK),
      times: -1,
    });
    renderUi(<RetrospectiveForm />);
    await pickBasics();
    await userEvent.type(screen.getByLabelText("Cliente"), "Cliente Demo");
    fireEvent.change(screen.getByLabelText("Día"), { target: { value: "2031-01-10" } });
    fireEvent.change(screen.getByLabelText("Inicio"), { target: { value: "09:00" } });
    fireEvent.change(screen.getByLabelText("Fin"), { target: { value: "10:30" } });
    await userEvent.click(screen.getByRole("button", { name: "Cargar trabajo" }));
    const text = await alertsText();
    expect(text).toMatch(/ninguno puede ser a futuro/);
    expect(text).not.toMatch(TECHNICAL);
    expect(screen.getByLabelText("Cliente")).toHaveValue("Cliente Demo");
    expect(screen.getByLabelText("Inicio")).toHaveValue("09:00");
    expect(screen.getByLabelText("Fin")).toHaveValue("10:30");
    expect(screen.getByLabelText("Día")).toHaveValue("2031-01-10");
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("HALLAZGO: un cobro mayor al total en la carga retrospectiva (422) no se explica como error de horas", async () => {
    server.failPost.push({
      match: /\/jobs\/retrospective$/,
      error: () => apiError(422, "VALIDATION_ERROR", "amount exceeds balance"),
      times: -1,
    });
    renderUi(<RetrospectiveForm />);
    await pickBasics();
    fireEvent.change(screen.getByLabelText("Inicio"), { target: { value: "09:00" } });
    fireEvent.change(screen.getByLabelText("Fin"), { target: { value: "10:30" } });
    fireEvent.change(screen.getByLabelText("Día"), { target: { value: "2026-11-10" } });
    const amount = screen.getByLabelText("Importe cobrado");
    await userEvent.type(amount, "99.999");
    await userEvent.click(screen.getByRole("button", { name: "Cargar trabajo" }));
    const text = await alertsText();
    // El mensaje del 422 sólo habla de horas y servicios a cotizar: el usuario no entiende qué corregir.
    expect(text).toMatch(/cobro|importe|total/i);
  });

  it("ingreso sin turno (LLEGÓ): 409 INVALID_PHONE y 422 conservan lo escrito", async () => {
    server.failPost.push({
      match: /^\/jobs$/,
      error: () => apiError(409, "INVALID_PHONE", LEAK),
      times: -1,
    });
    renderUi(<WalkInForm />);
    await pickBasics();
    await userEvent.type(screen.getByLabelText("Teléfono"), "123");
    await userEvent.type(screen.getByLabelText("Cliente"), "Cliente Demo");
    await userEvent.click(screen.getByRole("button", { name: "LLEGÓ" }));
    expect(await alertsText()).toMatch(/teléfono no es válido/);
    expect(screen.getByLabelText("Teléfono")).toHaveValue("123");
    expect(screen.getByLabelText("Cliente")).toHaveValue("Cliente Demo");
    expect(mockPush).not.toHaveBeenCalled();
  });
});
