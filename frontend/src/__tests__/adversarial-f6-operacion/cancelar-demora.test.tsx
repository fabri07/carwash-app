/**
 * T3 (6.2) — Cancelar por demora (D6-2, A4): aparece solo desde `cancel_delay_from`, nunca es
 * automático, pide confirmación y no aparece fuera de PRESENTE. Reloj falso (solo `Date` + timers de
 * jest) y con el reloj del dispositivo adelantado/atrasado.
 */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { JobPageClient } from "@/features/operacion/JobPageClient";
import {
  apiError,
  createServer,
  job,
  loginAs,
  queueItems,
  goOffline,
  renderUi,
  resetUnits,
  type FakeServer,
} from "./harness";
import type { Job } from "@/features/operacion/types";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
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

const FROM = "2030-11-25T13:21:00.000Z"; // turno 13:00 + tolerancia 20 min + 1 (la guarda es estricta)
const FROM_MS = Date.parse(FROM);
const BTN = { name: "Cancelar por demora" };

let server: FakeServer;
function setup(over: Partial<Job> = {}) {
  server = createServer([job({ cancel_delay_from: FROM, ...over })]);
}

beforeEach(() => {
  resetUnits();
  jest.useFakeTimers();
  setup();
});
afterEach(() => {
  jest.useRealTimers();
});

const user = () => userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
async function mountAt(ms: number) {
  jest.setSystemTime(ms);
  const r = renderUi(<JobPageClient id="j1" />);
  await screen.findByRole("button", { name: "INICIAR" });
  return r;
}

describe("Cuándo aparece (reloj del dispositivo)", () => {
  it("1 ms antes: NO aparece", async () => {
    await mountAt(FROM_MS - 1);
    expect(screen.queryByRole("button", BTN)).toBeNull();
  });

  it("justo en cancel_delay_from: aparece", async () => {
    await mountAt(FROM_MS);
    expect(screen.getByRole("button", BTN)).toBeInTheDocument();
  });

  it("1 ms después y mucho después: aparece", async () => {
    const a = await mountAt(FROM_MS + 1);
    expect(screen.getByRole("button", BTN)).toBeInTheDocument();
    a.unmount();
    await mountAt(FROM_MS + 6 * 3600_000);
    expect(screen.getByRole("button", BTN)).toBeInTheDocument();
  });

  it("con la pantalla abierta, aparece sola al cumplirse la tolerancia (revisa cada 15 s)", async () => {
    await mountAt(FROM_MS - 40_000);
    expect(screen.queryByRole("button", BTN)).toBeNull();
    await act(async () => {
      jest.advanceTimersByTime(45_000);
    });
    expect(screen.getByRole("button", BTN)).toBeInTheDocument();
  });

  it("HALLAZGO (bajo): aparece con hasta 15 s de atraso respecto de cancel_delay_from", async () => {
    await mountAt(FROM_MS - 10_000);
    await act(async () => {
      jest.advanceTimersByTime(10_000); // reloj en FROM exacto, pero el intervalo de 15 s no corrió
    });
    expect(Date.now()).toBeGreaterThanOrEqual(FROM_MS);
    expect(screen.getByRole("button", BTN)).toBeInTheDocument();
  });

  it("reloj del dispositivo ADELANTADO: el botón aparece antes, pero el servidor rechaza (422) y se explica en español", async () => {
    setup();
    server.failPost.push({
      match: /cancel-delay$/,
      error: () => apiError(422, "VALIDATION_ERROR", "demora no supera tolerancia"),
      times: -1,
    });
    // Hora real del servidor: 13:10. El celular cree que son las 15:00.
    await mountAt(FROM_MS + 99 * 60_000);
    await user().click(screen.getByRole("button", BTN));
    await user().click(await screen.findByRole("button", { name: "Sí, cancelar por demora" }));
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.map((a) => a.textContent).join(" ")).toMatch(
      /Todavía no se puede cancelar por demora/,
    );
    expect(document.body.textContent).not.toMatch(/VALIDATION_ERROR|demora no supera/);
    expect(server.jobs.get("j1")!.status).toBe("PRESENTE");
  });

  it("reloj del dispositivo ATRASADO: no aparece aunque el servidor ya lo permitiría (la UI sigue al celular)", async () => {
    // Observación de diseño: no hay hora del servidor en el job; documenta el límite.
    await mountAt(FROM_MS - 3 * 3600_000);
    expect(screen.queryByRole("button", BTN)).toBeNull();
  });
});

describe("Nunca automático y siempre con confirmación", () => {
  it("pasan horas con el botón visible y no sale ningún POST", async () => {
    await mountAt(FROM_MS + 1);
    await act(async () => {
      jest.advanceTimersByTime(3 * 3600_000);
    });
    expect(server.attempts).toHaveLength(0);
    expect(screen.getByRole("button", BTN)).toBeInTheDocument();
  });

  it("el primer toque solo abre la confirmación; 'Cancelar' no manda nada", async () => {
    await mountAt(FROM_MS + 1);
    await user().click(screen.getByRole("button", BTN));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(server.attempts).toHaveLength(0);
    await user().click(screen.getByRole("button", { name: "Cancelar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(server.attempts).toHaveLength(0);
  });

  it("confirmar manda un solo POST con clave y hora del toque; un doble clic no lo duplica", async () => {
    await mountAt(FROM_MS + 1);
    await user().click(screen.getByRole("button", BTN));
    const confirm = await screen.findByRole("button", { name: "Sí, cancelar por demora" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(server.received).toHaveLength(1));
    await act(async () => {
      jest.advanceTimersByTime(500);
    });
    expect(server.attempts).toHaveLength(1);
    const r = server.received[0]!;
    expect(r.url).toBe("/jobs/j1/cancel-delay");
    expect(r.key).toMatch(/^[0-9a-f-]{36}$/);
    expect(Date.parse(r.occurred_at!)).toBeGreaterThanOrEqual(FROM_MS + 1);
    expect(Date.parse(r.occurred_at!)).toBeLessThan(FROM_MS + 1000);
  });

  it("sin señal la cancelación queda en la cola con la hora del toque y el botón desaparece", async () => {
    await mountAt(FROM_MS + 1);
    goOffline(server);
    await user().click(screen.getByRole("button", BTN));
    await user().click(await screen.findByRole("button", { name: "Sí, cancelar por demora" }));
    await waitFor(() => expect(queueItems()).toHaveLength(1));
    expect(queueItems()[0]!.kind).toBe("operacion.cancel-delay");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByText("Cancelado por demora")).toBeInTheDocument();
    expect(screen.queryByRole("button", BTN)).toBeNull();
  });
});

describe("Solo en PRESENTE y con permiso", () => {
  it.each(["EN_PROCESO", "FINALIZADO", "COBRADO", "RETIRADO", "CANCELADO_DEMORA"] as const)(
    "en %s no aparece aunque cancel_delay_from ya pasó",
    async (status) => {
      setup({ status });
      jest.setSystemTime(FROM_MS + 3600_000);
      renderUi(<JobPageClient id="j1" />);
      await screen.findByText(/Total/);
      await act(async () => {
        jest.advanceTimersByTime(20_000);
      });
      expect(screen.queryByRole("button", BTN)).toBeNull();
    },
  );

  it("sin cancel_delay_from (ingreso sin turno) nunca aparece", async () => {
    setup({ cancel_delay_from: null, booking_id: null, scheduled_at: null });
    await mountAt(FROM_MS + 99 * 3600_000);
    expect(screen.queryByRole("button", BTN)).toBeNull();
  });

  it("sin JOBS_OPERAR no aparece; con JOBS_OPERAR sí", async () => {
    loginAs(["AGENDA_VER"]);
    const a = await (async () => {
      jest.setSystemTime(FROM_MS + 1);
      const r = renderUi(<JobPageClient id="j1" />);
      await screen.findByText(/Total/);
      return r;
    })();
    expect(screen.queryByRole("button", BTN)).toBeNull();
    a.unmount();
    loginAs(["AGENDA_VER", "JOBS_OPERAR"]);
    await mountAt(FROM_MS + 1);
    expect(screen.getByRole("button", BTN)).toBeInTheDocument();
  });

  it("al iniciar el trabajo el botón desaparece", async () => {
    await mountAt(FROM_MS + 1);
    await user().click(screen.getByRole("button", { name: "INICIAR" }));
    await screen.findByRole("button", { name: "FINALIZAR" });
    expect(screen.queryByRole("button", BTN)).toBeNull();
  });
});
