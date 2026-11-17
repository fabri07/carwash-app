/**
 * T3 (6.2) — KPI de toques (A2): LLEGÓ, INICIAR, FINALIZAR, RETIRAR = 1 toque; COBRAR = 2. Se cuentan
 * clicks reales sobre la UI renderizada, contra un servidor falso en la frontera HTTP.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { BookingArrivalClient } from "@/features/operacion/BookingArrivalClient";
import { JobPageClient } from "@/features/operacion/JobPageClient";
import { paymentMethodsApi } from "@/services/config.service";
import {
  bookingEntry,
  createServer,
  day,
  efectivo,
  job,
  method,
  renderUi,
  resetUnits,
  tarjeta,
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
let taps = 0;
async function tap(el: HTMLElement) {
  taps += 1;
  await userEvent.click(el);
}

beforeEach(() => {
  resetUnits();
  taps = 0;
  server = createServer([job()]);
});

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

describe("KPI de toques sobre la UI real", () => {
  it("INICIAR = 1 toque; manda Idempotency-Key (UUID) y la hora del toque", async () => {
    renderUi(<JobPageClient id="j1" />);
    const before = Date.now();
    await tap(await screen.findByRole("button", { name: "INICIAR" }));
    await waitFor(() => expect(server.received).toHaveLength(1));
    expect(taps).toBe(1);
    const r = server.received[0]!;
    expect(r.url).toBe("/jobs/j1/start");
    expect(r.key).toMatch(UUID);
    expect(Date.parse(r.occurred_at!)).toBeGreaterThanOrEqual(before);
    expect(Object.keys(r.body)).toEqual(["occurred_at"]);
  });

  it("FINALIZAR = 1 toque", async () => {
    server.jobs.set("j1", job({ status: "EN_PROCESO" }));
    renderUi(<JobPageClient id="j1" />);
    await tap(await screen.findByRole("button", { name: "FINALIZAR" }));
    await waitFor(() => expect(server.received).toHaveLength(1));
    expect(taps).toBe(1);
    expect(server.received[0]!.url).toBe("/jobs/j1/finish");
  });

  it("RETIRAR = 1 toque (cobrado) y también 1 toque con deuda (outline)", async () => {
    server.jobs.set("j1", job({ status: "COBRADO", balance_due_cents: 0, paid_cents: 2_000_000 }));
    const { unmount } = renderUi(<JobPageClient id="j1" />);
    await tap(await screen.findByRole("button", { name: "RETIRAR" }));
    await waitFor(() => expect(server.received).toHaveLength(1));
    expect(taps).toBe(1);
    expect(server.received[0]!.url).toBe("/jobs/j1/pick-up");
    unmount();

    taps = 0;
    server = createServer([job({ status: "FINALIZADO" })]);
    renderUi(<JobPageClient id="j1" />);
    await tap(await screen.findByRole("button", { name: "RETIRAR" }));
    await waitFor(() => expect(server.received).toHaveLength(1));
    expect(taps).toBe(1);
  });

  it("COBRAR = exactamente 2 toques (abrir y confirmar), con el medio por defecto y sin importe", async () => {
    server.jobs.set("j1", job({ status: "FINALIZADO" }));
    renderUi(<JobPageClient id="j1" />);
    await tap(await screen.findByRole("button", { name: /^COBRAR/ }));
    await tap(await screen.findByRole("button", { name: /CONFIRMAR COBRO/ }));
    await waitFor(() => expect(server.received).toHaveLength(1));
    expect(taps).toBe(2);
    const r = server.received[0]!;
    expect(r.url).toBe("/jobs/j1/payments");
    expect(r.body.payment_method_id).toBe("m1");
    expect(r.body).not.toHaveProperty("amount_cents");
    expect(r.key).toMatch(UUID);
  });

  it("COBRAR parcial sigue siendo abrir + confirmar (escribir no cuenta como toque) y manda el importe en centavos", async () => {
    server.jobs.set("j1", job({ status: "FINALIZADO" }));
    renderUi(<JobPageClient id="j1" />);
    await tap(await screen.findByRole("button", { name: /^COBRAR/ }));
    const amount = await screen.findByLabelText("Importe");
    await userEvent.clear(amount);
    await userEvent.type(amount, "5.000");
    await tap(screen.getByRole("button", { name: /CONFIRMAR COBRO/ }));
    await waitFor(() => expect(server.received).toHaveLength(1));
    expect(taps).toBe(2);
    expect(server.received[0]!.body.amount_cents).toBe(500_000); // $ 5.000 exactos, no $ 5
  });

  it("sin EFECTIVO el medio por defecto es el primero que cobra; los que no cobran no se ofrecen", async () => {
    resetUnits({ methods: [method("m9", "GASTOS", "Solo gastos", false), tarjeta] });
    server = createServer([job({ status: "FINALIZADO" })]);
    renderUi(<JobPageClient id="j1" />);
    await tap(await screen.findByRole("button", { name: /^COBRAR/ }));
    await tap(await screen.findByRole("button", { name: /CONFIRMAR COBRO/ }));
    await waitFor(() => expect(server.received).toHaveLength(1));
    expect(taps).toBe(2);
    expect(server.received[0]!.body.payment_method_id).toBe(tarjeta.id);
  });

  it("sin ningún medio que cobre no hay botón de confirmar y se explica qué hacer", async () => {
    resetUnits({ methods: [method("m9", "GASTOS", "Solo gastos", false)] });
    server = createServer([job({ status: "FINALIZADO" })]);
    renderUi(<JobPageClient id="j1" />);
    await tap(await screen.findByRole("button", { name: /^COBRAR/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/No hay un medio de pago/);
    expect(screen.queryByRole("button", { name: /CONFIRMAR COBRO/ })).toBeNull();
    expect(server.attempts).toHaveLength(0);
  });

  it("HALLAZGO H5: si falla la carga de medios de pago, COBRAR no queda muerto sin explicación", async () => {
    (paymentMethodsApi.list as jest.Mock).mockRejectedValue(new Error("boom"));
    server = createServer([job({ status: "FINALIZADO" })]);
    renderUi(<JobPageClient id="j1" />);
    const cobrar = await screen.findByRole("button", { name: /^COBRAR/ });
    await waitFor(() => expect(paymentMethodsApi.list).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 50));
    // Aceptable: el botón anda, o hay un aviso. Inaceptable: botón deshabilitado y silencio.
    const explained =
      !(cobrar as HTMLButtonElement).disabled || screen.queryByRole("alert") !== null;
    expect(explained).toBe(true);
  });

  it("LLEGÓ de un turno = 1 toque: POST /jobs con booking_id, clave y hora; sigue en el job", async () => {
    server.defaultDay = day([bookingEntry()]);
    renderUi(<BookingArrivalClient bookingId="b1" />);
    await tap(await screen.findByRole("button", { name: "LLEGÓ" }));
    await waitFor(() => expect(server.received).toHaveLength(1));
    expect(taps).toBe(1);
    const r = server.received[0]!;
    expect(r.url).toBe("/jobs");
    expect(r.body.booking_id).toBe("b1");
    expect(r.key).toMatch(UUID);
    expect(r.occurred_at).toBeTruthy();
    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/operacion/j100"));
  });

  it("el cobro a un job con saldo cero no ofrece COBRAR (no hay toques de más)", async () => {
    server.jobs.set("j1", job({ status: "FINALIZADO", balance_due_cents: 0 }));
    renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("button", { name: "RETIRAR" });
    expect(screen.queryByRole("button", { name: /^COBRAR/ })).toBeNull();
  });

  it("efectivo sigue siendo el default aunque venga segundo en la lista", async () => {
    resetUnits({ methods: [tarjeta, efectivo] });
    server = createServer([job({ status: "FINALIZADO" })]);
    renderUi(<JobPageClient id="j1" />);
    await tap(await screen.findByRole("button", { name: /^COBRAR/ }));
    await tap(await screen.findByRole("button", { name: /CONFIRMAR COBRO/ }));
    await waitFor(() => expect(server.received).toHaveLength(1));
    expect(server.received[0]!.body.payment_method_id).toBe("m1");
  });
});
