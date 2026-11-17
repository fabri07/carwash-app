/**
 * T3 (6.2) — PII y seguridad: qué queda en localStorage al encolar (solo lo necesario), nada de PII en
 * URLs, consola, banner, toasts ni mensajes de error. Datos sintéticos.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AgendaPageClient } from "@/features/operacion/AgendaPageClient";
import { BookingArrivalClient } from "@/features/operacion/BookingArrivalClient";
import { JobPageClient } from "@/features/operacion/JobPageClient";
import { WalkInForm } from "@/features/operacion/WalkInForm";
import { findSmallTouchTargets } from "@/lib/touchTargets";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { useToastStore } from "@/stores/toastStore";
import {
  apiError,
  bookingEntry,
  createServer,
  day,
  entry,
  goOffline,
  goOnline,
  job,
  QUEUE_KEY,
  queueItems,
  renderUi,
  resetUnits,
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

const PII = [
  "Cliente Demo",
  "AA000AA",
  "+5493510000001",
  "5493510000001",
  "dueno@example.com",
  "staff@example.com",
];
const NEW_PII = ["Persona Privada", "+54 9 351 000-0042", "ZZ999ZZ"];

let server: FakeServer;
let spies: jest.SpyInstance[];
beforeEach(() => {
  resetUnits();
  server = createServer([job()]);
  spies = (["log", "info", "warn", "error", "debug"] as const).map((m) =>
    jest.spyOn(console, m).mockImplementation(() => undefined),
  );
});
afterEach(() => spies.forEach((s) => s.mockRestore()));

const consoleText = () =>
  spies
    .flatMap((s) =>
      s.mock.calls.map((c) =>
        c.map((x: unknown) => (x instanceof Error ? x.message + x.stack : String(x))).join(" "),
      ),
    )
    .join("\n");
const stored = () => localStorage.getItem(QUEUE_KEY) ?? "";

describe("Lo que queda en localStorage al encolar", () => {
  it("un toque de INICIAR/FINALIZAR/COBRAR/RETIRAR sin señal guarda SOLO ids y el cuerpo mínimo, sin datos del cliente", async () => {
    server = createServer([job({ status: "FINALIZADO" })]);
    renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("button", { name: /^COBRAR/ });
    goOffline(server);
    await userEvent.click(screen.getByRole("button", { name: /^COBRAR/ }));
    await userEvent.click(await screen.findByRole("button", { name: /CONFIRMAR COBRO/ }));
    await userEvent.click(await screen.findByRole("button", { name: "RETIRAR" }));
    await waitFor(() => expect(queueItems()).toHaveLength(2));

    const raw = stored();
    for (const p of PII) expect(raw).not.toContain(p);
    expect(Object.keys(localStorage)).toContain(QUEUE_KEY);
    for (const k of Object.keys(localStorage))
      for (const p of PII.slice(0, 4)) expect(localStorage.getItem(k)).not.toContain(p);
    const items = queueItems();
    for (const i of items) {
      expect(Object.keys(i).sort()).toEqual(
        [
          "attempts",
          "createdAt",
          "dedupeKey",
          "id",
          "kind",
          "occurredAt",
          "payload",
          "tenantId",
          "userId",
        ].sort(),
      );
    }
    expect(items[0]!.payload).toEqual({ jobId: "j1", body: { payment_method_id: "m1" } });
    expect(items[1]!.payload).toEqual({ jobId: "j1", body: {} });
    // Ni el occurred_at (lo agrega la cola al enviar) ni credenciales ni el job completo.
    expect(raw).not.toMatch(/occurred_at|token|password|cookie|balance_due|customer/i);
  });

  it("el ingreso sin turno encolado guarda exactamente los campos que hacen falta para reenviarlo (y nada más)", async () => {
    renderUi(<WalkInForm />);
    await userEvent.selectOptions(await screen.findByLabelText("Tamaño del vehículo"), "s1");
    await userEvent.click(screen.getByRole("button", { name: "Lavado completo" }));
    await userEvent.type(screen.getByLabelText("Cliente"), NEW_PII[0]!);
    await userEvent.type(screen.getByLabelText("Teléfono"), NEW_PII[1]!);
    await userEvent.type(screen.getByLabelText("Patente"), NEW_PII[2]!);
    goOffline(server);
    await userEvent.click(screen.getByRole("button", { name: "LLEGÓ" }));
    await waitFor(() => expect(queueItems()).toHaveLength(1));
    const payload = queueItems()[0]!.payload as { body: Record<string, unknown> };
    expect(Object.keys(payload.body).sort()).toEqual(
      [
        "channel",
        "customer_name",
        "customer_phone",
        "plate",
        "resource_id",
        "service_ids",
        "vehicle_size_id",
      ].sort(),
    );
    // Los mensajes que ve el usuario no repiten sus datos.
    expect(
      useToastStore
        .getState()
        .toasts.map((t) => t.message)
        .join(" "),
    ).not.toMatch(/Persona|ZZ999|351 000/);
  });

  it("descartar un fallido borra el payload (y su PII) del localStorage", async () => {
    renderUi(<WalkInForm />);
    await userEvent.selectOptions(await screen.findByLabelText("Tamaño del vehículo"), "s1");
    await userEvent.click(screen.getByRole("button", { name: "Lavado completo" }));
    await userEvent.type(screen.getByLabelText("Cliente"), NEW_PII[0]!);
    goOffline(server);
    await userEvent.click(screen.getByRole("button", { name: "LLEGÓ" }));
    await waitFor(() => expect(queueItems()).toHaveLength(1));
    expect(stored()).toContain("Persona Privada");
    server.failPost.push({
      match: /^\/jobs$/,
      error: () => apiError(422, "VALIDATION_ERROR"),
      times: -1,
    });
    goOnline(server);
    // Ahora está fallido: se ve en el banner de cualquier pantalla de operación.
    renderUi(<AgendaPageClient />);
    const banner = await screen.findByTestId("queue-banner");
    await within(banner).findByRole("button", { name: "Descartar" });
    expect(banner.textContent).not.toMatch(/Persona|ZZ999|351 000/);
    await userEvent.click(within(banner).getByRole("button", { name: "Descartar" }));
    expect(stored()).not.toContain("Persona Privada");
  });

  // H12 (info), sin cambio: la cola sobrevive al logout por diseño de 6.3 (perder cargas es peor).
  it.failing(
    "HALLAZGO (info): cerrar sesión deja en localStorage el nombre/teléfono/patente de lo que no llegó a enviarse",
    async () => {
      renderUi(<WalkInForm />);
      await userEvent.selectOptions(await screen.findByLabelText("Tamaño del vehículo"), "s1");
      await userEvent.click(screen.getByRole("button", { name: "Lavado completo" }));
      await userEvent.type(screen.getByLabelText("Cliente"), NEW_PII[0]!);
      await userEvent.type(screen.getByLabelText("Patente"), NEW_PII[2]!);
      goOffline(server);
      await userEvent.click(screen.getByRole("button", { name: "LLEGÓ" }));
      await waitFor(() => expect(queueItems()).toHaveLength(1));
      useAuthStore.getState().clear();
      // Por diseño la cola sobrevive al logout (perder cargas es peor), pero en una tablet compartida
      // la PII del cliente queda legible para quien abra las herramientas del navegador.
      expect(stored()).not.toMatch(/Persona Privada|ZZ999ZZ/);
    },
  );
});

describe("Nada de PII en URLs, consola, banner ni mensajes", () => {
  it("la agenda: ningún enlace lleva nombre, teléfono, patente ni email; solo ids y fecha", async () => {
    server.defaultDay = day([
      entry({ job_status: "PRESENTE" }),
      bookingEntry({ id: "b2", booking_id: "b2", resource_id: "r2" }),
    ]);
    const { container } = renderUi(<AgendaPageClient initialDate="2030-11-25" />);
    await screen.findAllByText(/Cliente Demo/);
    const hrefs = Array.from(container.querySelectorAll("a")).map(
      (a) => a.getAttribute("href") ?? "",
    );
    expect(hrefs.length).toBeGreaterThan(3);
    for (const h of hrefs) for (const p of PII) expect(decodeURIComponent(h)).not.toContain(p);
    for (const h of hrefs) expect(h).toMatch(/^\/(agenda|operacion)/);
    expect(server.gets.every((g) => !/Cliente|AA000AA|5493510/.test(g))).toBe(true);
    const calls = (api.get as jest.Mock).mock.calls;
    expect(JSON.stringify(calls)).not.toMatch(/Cliente|AA000AA|5493510/);
  });

  it("la llegada de un turno no pone datos del cliente en la URL: navega solo a /operacion/{id}", async () => {
    server.defaultDay = day([bookingEntry()]);
    renderUi(<BookingArrivalClient bookingId="b1" />);
    await userEvent.click(await screen.findByRole("button", { name: "LLEGÓ" }));
    await waitFor(() => expect(mockReplace).toHaveBeenCalled());
    for (const c of [...mockPush.mock.calls, ...mockReplace.mock.calls]) {
      expect(String(c[0])).toMatch(/^\/operacion\/j\d+$|^\/agenda/);
      for (const p of PII) expect(String(c[0])).not.toContain(p);
    }
  });

  it("flujo completo con errores y reconexión: la consola no recibe nombre, teléfono, patente ni email", async () => {
    server = createServer([job({ status: "FINALIZADO" })]);
    renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("button", { name: /^COBRAR/ });
    goOffline(server);
    await userEvent.click(screen.getByRole("button", { name: /^COBRAR/ }));
    await userEvent.click(await screen.findByRole("button", { name: /CONFIRMAR COBRO/ }));
    await waitFor(() => expect(queueItems()).toHaveLength(1));
    server.failPost.push({
      match: /payments$/,
      error: () => apiError(409, "INVALID_TRANSITION", "Cliente Demo AA000AA"),
      times: 1,
    });
    goOnline(server);
    await screen.findByText(/No se aplicó el cobro/);
    const banner = screen.getByTestId("queue-banner");
    for (const p of PII) expect(banner.textContent).not.toContain(p);
    const logged = consoleText();
    for (const p of PII) expect(logged).not.toContain(p);
  });

  it("los mensajes de error no repiten lo que mandó el servidor (que podría traer datos del cliente)", async () => {
    server.failPost.push({
      match: /\/start$/,
      error: () =>
        apiError(422, "VALIDATION_ERROR", "El cliente Cliente Demo con patente AA000AA no existe"),
      times: -1,
    });
    renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await screen.findByRole("button", { name: "INICIAR" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).not.toMatch(/no existe|El cliente Cliente/);
  });

  it("sanity: el verificador de 44 px de esta carpeta no es vacío (detecta un botón de 32 px)", () => {
    document.body.innerHTML =
      '<button class="h-8 w-8">x</button><button class="h-11 min-w-11">y</button>';
    expect(findSmallTouchTargets(document.body)).toHaveLength(1);
    document.body.innerHTML = "";
    fireEvent.click(document.body);
  });
});

// Al final: deja backoff de módulo (fallas transitorias) que afectaría a los tests siguientes.
describe("lastError de la cola", () => {
  it("el lastError que se guarda es genérico: nunca el cuerpo ni el texto que mandó el servidor", async () => {
    renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("button", { name: "INICIAR" });
    goOffline(server);
    await userEvent.click(screen.getByRole("button", { name: "INICIAR" }));
    await waitFor(() => expect(queueItems()).toHaveLength(1));
    server.failPost.push({
      match: /\/start$/,
      error: () => apiError(500, undefined, "boom: Cliente Demo AA000AA +5493510000001"),
      times: -1,
    });
    goOnline(server);
    await waitFor(() => expect(queueItems()[0]?.lastError).toBeTruthy());
    expect(stored()).not.toMatch(/Cliente Demo|AA000AA|5493510000001|boom/);
    expect(queueItems()[0]!.lastError).toMatch(/^Error 500/);
  });
});
