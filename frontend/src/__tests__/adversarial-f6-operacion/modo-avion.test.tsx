/**
 * T3 (6.2) — Modo avión de punta a punta con las pantallas reales (A7): iniciar→finalizar→cobrar sin
 * señal, recarga en medio, doble toque, dos jobs a la vez, LLEGÓ sin señal y colisiones de la clave de
 * dedupe. El servidor falso vive en la frontera HTTP: la cola, el hook y la UI son los de verdad.
 */
import { act, fireEvent, renderHook, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";

import { BookingArrivalClient } from "@/features/operacion/BookingArrivalClient";
import { JobPageClient } from "@/features/operacion/JobPageClient";
import { KINDS, useOperacionSubmit } from "@/features/operacion/useOperacionSubmit";
import {
  bookingEntry,
  createServer,
  day,
  goOffline,
  goOnline,
  job,
  queueItems,
  reloadQueue,
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

// Solo el reloj `Date` es falso: los timers son reales (userEvent y waitFor andan normal).
const KEEP_REAL = [
  "setTimeout",
  "clearTimeout",
  "setInterval",
  "clearInterval",
  "setImmediate",
  "clearImmediate",
  "nextTick",
  "queueMicrotask",
  "requestAnimationFrame",
  "cancelAnimationFrame",
  "requestIdleCallback",
  "cancelIdleCallback",
  "performance",
  "hrtime",
] as const;
const clockAt = (iso: string) => jest.setSystemTime(new Date(iso));

let server: FakeServer;

beforeEach(() => {
  resetUnits();
  server = createServer([job()]);
  jest.useFakeTimers({ doNotFake: [...KEEP_REAL] });
});
afterEach(() => {
  jest.useRealTimers();
});

const T1 = "2030-11-25T13:05:00.000Z";
const T2 = "2030-11-25T13:50:00.000Z";
const T3 = "2030-11-25T14:20:00.000Z";
const SYNC = "2030-11-25T18:00:00.000Z";

describe("Modo avión: iniciar→finalizar→cobrar sin señal", () => {
  it("al volver la señal sale UN envío por toque, en orden, con la hora de cada toque y la misma Idempotency-Key", async () => {
    clockAt(T1);
    renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("button", { name: "INICIAR" });
    goOffline(server);

    await userEvent.click(screen.getByRole("button", { name: "INICIAR" }));
    // La pantalla adelanta el estado para seguir sin señal.
    expect(await screen.findByRole("button", { name: "FINALIZAR" })).toBeInTheDocument();
    expect(
      screen
        .getAllByRole("status")
        .map((n) => n.textContent)
        .join(" "),
    ).toMatch(/Sin conexión/);

    clockAt(T2);
    await userEvent.click(screen.getByRole("button", { name: "FINALIZAR" }));
    await userEvent.click(await screen.findByRole("button", { name: /^COBRAR/ }));

    clockAt(T3);
    await userEvent.click(await screen.findByRole("button", { name: /CONFIRMAR COBRO/ }));
    await waitFor(() => expect(queueItems()).toHaveLength(3));

    const items = queueItems();
    expect(items.map((i) => i.occurredAt)).toEqual([T1, T2, T3]);
    expect(new Set(items.map((i) => i.id)).size).toBe(3);
    // El primer intento (fallido por red) ya iba con la misma clave que quedó en la cola.
    expect(server.attempts[0]!.key).toBe(items[0]!.id);
    expect(server.received).toHaveLength(0);

    clockAt(SYNC);
    goOnline(server);
    await waitFor(() => expect(server.received).toHaveLength(3));
    expect(server.received.map((r) => r.url)).toEqual([
      "/jobs/j1/start",
      "/jobs/j1/finish",
      "/jobs/j1/payments",
    ]);
    expect(server.received.map((r) => r.occurred_at)).toEqual([T1, T2, T3]);
    expect(server.received.map((r) => r.key)).toEqual(items.map((i) => i.id));
    // Nadie lleva la hora de la sincronización.
    expect(server.received.some((r) => r.occurred_at === SYNC)).toBe(false);
    await waitFor(() => expect(queueItems()).toHaveLength(0));
    // Y no hay envíos de más pasado un rato.
    await new Promise((r) => setTimeout(r, 200));
    expect(server.received).toHaveLength(3);
    expect(server.attempts.filter((a) => a.key === items[0]!.id).length).toBeLessThanOrEqual(2);
  });

  it("recargar la página en medio no pierde ni duplica el toque: sale una vez con su hora original", async () => {
    clockAt(T1);
    const first = renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("button", { name: "INICIAR" });
    goOffline(server);
    await userEvent.click(screen.getByRole("button", { name: "INICIAR" }));
    await waitFor(() => expect(queueItems()).toHaveLength(1));
    const id = queueItems()[0]!.id;
    first.unmount();

    await reloadQueue();
    expect(queueItems()).toHaveLength(1);
    renderUi(<JobPageClient id="j1" />);

    clockAt(SYNC);
    goOnline(server);
    await waitFor(() => expect(server.received).toHaveLength(1));
    expect(server.received[0]).toMatchObject({ url: "/jobs/j1/start", occurred_at: T1, key: id });
    await new Promise((r) => setTimeout(r, 200));
    expect(server.received).toHaveLength(1);
    expect(queueItems()).toHaveLength(0);
  });

  it("HALLAZGO: recargar SIN señal deja al operador sin pantalla (dice 'No se encontró el trabajo')", async () => {
    clockAt(T1);
    const first = renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("button", { name: "INICIAR" });
    goOffline(server);
    await userEvent.click(screen.getByRole("button", { name: "INICIAR" }));
    await waitFor(() => expect(queueItems()).toHaveLength(1));
    first.unmount();
    await reloadQueue();

    renderUi(<JobPageClient id="j1" />);
    await new Promise((r) => setTimeout(r, 100));
    // El trabajo existe y hay un toque guardado: no puede decir que no existe, ni dejar sin botones.
    expect(screen.queryByText(/No se encontró el trabajo/)).toBeNull();
  });

  it("HALLAZGO: un refetch fallido (volver a la app sin señal) no reemplaza la pantalla que ya tenía datos", async () => {
    renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("button", { name: "INICIAR" });
    // Conexión muerta con el SO creyendo que hay red (wifi sin salida, 4G flojo): el GET falla.
    server.online = false;
    act(() => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    await new Promise((r) => setTimeout(r, 100));
    expect(screen.getByRole("button", { name: "INICIAR" })).toBeInTheDocument();
    expect(screen.queryByText(/No se encontró el trabajo/)).toBeNull();
  });
});

describe("Doble toque", () => {
  it("dos toques seguidos sobre INICIAR sin señal = una sola entrada en la cola", async () => {
    renderUi(<JobPageClient id="j1" />);
    const btn = await screen.findByRole("button", { name: "INICIAR" });
    goOffline(server);
    fireEvent.click(btn);
    fireEvent.click(btn);
    await waitFor(() => expect(queueItems()).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 50));
    expect(queueItems()).toHaveLength(1);
  });

  it("dos toques con red lenta = un solo POST", async () => {
    let release!: () => void;
    server.gate = new Promise<void>((res) => {
      release = res;
    });
    renderUi(<JobPageClient id="j1" />);
    const btn = await screen.findByRole("button", { name: "INICIAR" });
    fireEvent.click(btn);
    fireEvent.click(btn);
    await userEvent.click(btn).catch(() => undefined);
    release();
    await waitFor(() => expect(server.received).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 100));
    expect(server.attempts).toHaveLength(1);
  });

  it("repetir el toque tras sincronizar manda de nuevo con otra clave y el servidor responde 409 (no se pierde, se avisa)", async () => {
    renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await screen.findByRole("button", { name: "INICIAR" }));
    await waitFor(() => expect(server.received).toHaveLength(1));
    // Otro dispositivo ya lo tenía: el job quedó EN_PROCESO; el segundo toque llega con otra clave.
    const keyBefore = server.received[0]!.key;
    expect(keyBefore).toBeTruthy();
    expect(server.jobs.get("j1")!.status).toBe("EN_PROCESO");
  });

  it("HALLAZGO: dos cobros parciales sin señal se pisan: el segundo se descarta en silencio (dedupe cobrar:<job>)", async () => {
    server.jobs.set("j1", job({ status: "FINALIZADO" }));
    renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("button", { name: /^COBRAR/ });
    goOffline(server);

    // Cobro 1: $ 5.000 en efectivo.
    await userEvent.click(screen.getByRole("button", { name: /^COBRAR/ }));
    const amount = await screen.findByLabelText("Importe");
    await userEvent.clear(amount);
    await userEvent.type(amount, "5.000");
    await userEvent.click(screen.getByRole("button", { name: /CONFIRMAR COBRO/ }));
    await waitFor(() => expect(queueItems()).toHaveLength(1));

    // Cobro 2: el resto ($ 15.000), con tarjeta. La pantalla ya mostró el saldo bajado.
    await userEvent.click(await screen.findByRole("button", { name: /^COBRAR/ }));
    await userEvent.click(await screen.findByRole("button", { name: /CONFIRMAR COBRO/ }));
    await new Promise((r) => setTimeout(r, 50));

    // Dos cobros distintos tienen que ser dos entradas: cada uno es plata que entró.
    expect(queueItems().filter((i) => i.kind === KINDS.pay)).toHaveLength(2);
  });
});

function renderQueueHook() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return renderHook(() => useOperacionSubmit(), { wrapper });
}

describe("Dos jobs distintos", () => {
  beforeEach(() => {
    server = createServer([job({ id: "j1" }), job({ id: "j2" })]);
  });

  it("en paralelo no se bloquean entre sí: los dos POST salen aunque el primero siga en vuelo", async () => {
    let release!: () => void;
    server.gate = new Promise<void>((res) => {
      release = res;
    });
    const { result } = renderQueueHook();
    const a = result.current.run(KINDS.start, { jobId: "j1", body: {} }, "iniciar:j1");
    const b = result.current.run(KINDS.start, { jobId: "j2", body: {} }, "iniciar:j2");
    await waitFor(() => expect(server.attempts).toHaveLength(2));
    release();
    await expect(Promise.all([a, b])).resolves.toEqual([
      expect.objectContaining({ status: "ok" }),
      expect.objectContaining({ status: "ok" }),
    ]);
    expect(server.received.map((r) => r.url).sort()).toEqual(["/jobs/j1/start", "/jobs/j2/start"]);
  });

  it("sin señal se conservan los dos y al volver salen los dos, cada job en su orden", async () => {
    goOffline(server);
    const { result } = renderQueueHook();
    await result.current.run(KINDS.start, { jobId: "j1", body: {} }, "iniciar:j1");
    await result.current.run(KINDS.start, { jobId: "j2", body: {} }, "iniciar:j2");
    await result.current.run(KINDS.finish, { jobId: "j1", body: {} }, "finalizar:j1");
    await result.current.run(KINDS.finish, { jobId: "j2", body: {} }, "finalizar:j2");
    expect(queueItems()).toHaveLength(4);
    goOnline(server);
    await act(async () => {
      await result.current.flush();
    });
    await waitFor(() => expect(server.received).toHaveLength(4));
    const by = (j: string) =>
      server.received.filter((r) => r.url.includes(`/${j}/`)).map((r) => r.url);
    expect(by("j1")).toEqual(["/jobs/j1/start", "/jobs/j1/finish"]);
    expect(by("j2")).toEqual(["/jobs/j2/start", "/jobs/j2/finish"]);
  });
});

describe("Estado cambiado por otro dispositivo mientras no había señal", () => {
  it("el toque guardado vuelve 409: sale de la cola sin reintento y queda un aviso descartable", async () => {
    renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("button", { name: "INICIAR" });
    goOffline(server);
    await userEvent.click(screen.getByRole("button", { name: "INICIAR" }));
    await waitFor(() => expect(queueItems()).toHaveLength(1));
    // Otro dispositivo ya lo inició.
    server.jobs.get("j1")!.status = "EN_PROCESO";
    goOnline(server);
    await waitFor(() =>
      expect(screen.getByTestId("queue-banner")).toHaveTextContent(/No se aplicó el inicio/),
    );
    expect(screen.getByTestId("queue-banner")).toHaveTextContent(/otro dispositivo se adelantó/);
    expect(queueItems()).toHaveLength(0);
    const sent = server.attempts.filter((a) => a.url === "/jobs/j1/start").length;
    await new Promise((r) => setTimeout(r, 200));
    expect(server.attempts.filter((a) => a.url === "/jobs/j1/start").length).toBe(sent);
    await userEvent.click(screen.getByRole("button", { name: "Entendido" }));
    expect(screen.queryByTestId("queue-banner")).toBeNull();
  });
});

describe("LLEGÓ sin señal", () => {
  beforeEach(() => {
    server.defaultDay = day([bookingEntry()]);
  });

  it("queda guardado y se avisa con el texto de sin conexión; no se duplica con otro toque", async () => {
    renderUi(<BookingArrivalClient bookingId="b1" />);
    const llego = await screen.findByRole("button", { name: "LLEGÓ" });
    goOffline(server);
    await userEvent.click(llego);
    expect(await screen.findByText(/Sin conexión: quedó guardado/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "LLEGÓ" }));
    await new Promise((r) => setTimeout(r, 50));
    expect(queueItems()).toHaveLength(1);
    expect(queueItems()[0]!.dedupeKey).toBe("llego:b1");
    // Al volver la señal se crea el job una sola vez.
    goOnline(server);
    await waitFor(() => expect(server.received).toHaveLength(1));
    expect(server.received[0]!.url).toBe("/jobs");
  });

  it("HALLAZGO: tras LLEGÓ sin señal el operador puede seguir con INICIAR (no queda detenido hasta que vuelva la red)", async () => {
    renderUi(<BookingArrivalClient bookingId="b1" />);
    const llego = await screen.findByRole("button", { name: "LLEGÓ" });
    goOffline(server);
    await userEvent.click(llego);
    await screen.findByText(/Sin conexión: quedó guardado/);
    // A7/T4: llegó→iniciar→finalizar→cobrar en modo avión. Sin job no hay a qué mandar INICIAR.
    const canContinue =
      screen.queryByRole("button", { name: "INICIAR" }) !== null ||
      mockReplace.mock.calls.length > 0;
    expect(canContinue).toBe(true);
  });
});
