/**
 * T3 (6.2) — Touch targets ≥ 44 px en TODAS las pantallas, en todos sus estados (también error,
 * vacío, cobro abierto, diálogos y banner de cola). jsdom no calcula layout: se miran las clases con
 * `findSmallTouchTargets` (alto y ancho) y `undersizedControls` (alto).
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AgendaPageClient } from "@/features/operacion/AgendaPageClient";
import { BookingArrivalClient } from "@/features/operacion/BookingArrivalClient";
import { InspectionPageClient } from "@/features/operacion/InspectionForm";
import { JobPageClient } from "@/features/operacion/JobPageClient";
import { ManualBookingForm } from "@/features/operacion/ManualBookingForm";
import { RetrospectiveForm } from "@/features/operacion/RetrospectiveForm";
import { WalkInForm } from "@/features/operacion/WalkInForm";
import { undersizedControls } from "@/features/operacion/touch";
import { findSmallTouchTargets } from "@/lib/touchTargets";
import { useOfflineQueueStore } from "@/stores/offlineQueueStore";
import {
  apiError,
  bookingEntry,
  createServer,
  day,
  entry,
  job,
  renderUi,
  resetUnits,
  type FakeServer,
} from "./harness";

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

let server: FakeServer;
beforeEach(() => {
  resetUnits();
  server = createServer([job()]);
});

/** Falla con la lista de controles que no garantizan 44 px (alto, y ancho si es un control sin texto). */
function expectBig(where: string) {
  const heights = undersizedControls(document.body).map((e) => e.outerHTML.slice(0, 140));
  const both = findSmallTouchTargets(document.body)
    // Un enlace/botón con texto es más ancho que 44 px: acá solo importa el alto. El ancho se exige a
    // los controles sin texto (iconos, casillas), que son los que pueden quedar chicos.
    .filter((el) => !(el.textContent ?? "").trim())
    .map((e) => e.outerHTML.slice(0, 140));
  expect({ where, altoChico: heights, sinTextoChicos: both }).toEqual({
    where,
    altoChico: [],
    sinTextoChicos: [],
  });
}

const ready = (name: RegExp | string) => screen.findByRole("button", { name });

describe("Agenda", () => {
  it("con datos, vacía, sin puestos, con error, truncada y con el banner de cola", async () => {
    useOfflineQueueStore.setState({
      conflicts: [
        {
          id: "c1",
          userId: "u1",
          tenantId: "t1",
          kind: "operacion.start",
          payload: {},
          createdAt: "2030-11-25T13:05:00.000Z",
          attempts: 0,
          failedAt: "2030-11-25T18:00:00.000Z",
        },
      ],
      failed: [
        {
          id: "f1",
          userId: "u1",
          tenantId: "t1",
          kind: "operacion.pay",
          payload: {},
          createdAt: "2030-11-25T13:05:00.000Z",
          attempts: 1,
          failedAt: "2030-11-25T18:00:00.000Z",
          status: 422,
        },
      ],
    });
    server.defaultDay = day(
      [entry({ job_status: "FINALIZADO" }), bookingEntry({ resource_id: null })],
      {
        truncated: true,
      },
    );
    const a = renderUi(<AgendaPageClient />);
    await screen.findByText(/se muestran los primeros/);
    await screen.findByRole("button", { name: "Reintentar" });
    expectBig("agenda con datos + banner");
    a.unmount();

    server.defaultDay = day([]);
    const b = renderUi(<AgendaPageClient />);
    await screen.findByText("No hay turnos ni trabajos este día.");
    expectBig("agenda vacía");
    b.unmount();

    server.defaultDay = day([], { resources: [] });
    const c = renderUi(<AgendaPageClient />);
    await screen.findByText(/No hay puestos configurados/);
    expectBig("agenda sin puestos");
    c.unmount();

    server.failGet.push({ match: /^\/agenda\/day$/, error: () => apiError(500) });
    renderUi(<AgendaPageClient />);
    await screen.findByText(/No se pudo cargar la agenda del día/);
    expectBig("agenda con error");
  });
});

describe("Job", () => {
  it.each([
    "PRESENTE",
    "EN_PROCESO",
    "FINALIZADO",
    "COBRADO",
    "RETIRADO",
    "CANCELADO_DEMORA",
  ] as const)("en %s (con cobros, línea de tiempo y enlace a la recepción)", async (status) => {
    server = createServer([
      job({
        status,
        cancel_delay_from: "2000-01-01T00:00:00Z",
        payments: [
          {
            id: "p1",
            kind: "SALDO",
            amount_cents: 100,
            occurred_at: "2030-11-25T14:30:00Z",
            payment_method_id: "m1",
            voided: false,
          },
          {
            id: "p2",
            kind: "SEÑA",
            amount_cents: 100,
            occurred_at: "2030-11-25T14:30:00Z",
            payment_method_id: "m1",
            voided: false,
          },
          {
            id: "p3",
            kind: "SALDO",
            amount_cents: 100,
            occurred_at: "2030-11-25T14:30:00Z",
            payment_method_id: "m1",
            voided: true,
          },
        ] as never,
      }),
    ]);
    renderUi(<JobPageClient id="j1" />);
    await screen.findByText(/Total/);
    await waitFor(() => expect(screen.queryByText("Cargando…")).toBeNull());
    expectBig(`job ${status}`);
  });

  it("con el cobro abierto, con el panel de 'sin medios', y con los diálogos de anular y de cancelar por demora", async () => {
    server = createServer([
      job({
        status: "FINALIZADO",
        payments: [
          {
            id: "p1",
            kind: "SALDO",
            amount_cents: 100,
            occurred_at: "2030-11-25T14:30:00Z",
            payment_method_id: "m1",
            voided: false,
          },
        ] as never,
      }),
    ]);
    const a = renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await ready(/^COBRAR/));
    await ready(/CONFIRMAR COBRO/);
    expectBig("cobro abierto");
    await userEvent.click(screen.getByRole("button", { name: "Anular cobro" }));
    await screen.findByRole("dialog");
    expectBig("diálogo anular cobro");
    a.unmount();

    server = createServer([job({ status: "PRESENTE", cancel_delay_from: "2000-01-01T00:00:00Z" })]);
    renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await ready("Cancelar por demora"));
    await screen.findByRole("dialog");
    expectBig("diálogo cancelar por demora");
  });

  it("estados de error: job no encontrado y tolerancia sin job", async () => {
    server.failGet.push({ match: /^\/jobs\/j1$/, error: () => apiError(404, "NOT_FOUND") });
    renderUi(<JobPageClient id="j1" />);
    await screen.findByRole("alert");
    expectBig("job no encontrado");
  });
});

describe("Recepción de un turno, cargas y recepción completa", () => {
  it("LLEGÓ de un turno (y su estado de error)", async () => {
    server.defaultDay = day([bookingEntry()]);
    const a = renderUi(<BookingArrivalClient bookingId="b1" />);
    await ready("LLEGÓ");
    expectBig("llegada");
    a.unmount();
    renderUi(<BookingArrivalClient bookingId="no-existe" />);
    await screen.findByRole("alert");
    expectBig("llegada no encontrada");
  });

  it("ingreso sin turno, con error de validación visible", async () => {
    renderUi(<WalkInForm />);
    await ready("LLEGÓ");
    await userEvent.click(screen.getByRole("button", { name: "LLEGÓ" }));
    await screen.findByText("Elegí al menos un servicio");
    expectBig("ingreso sin turno con errores");
  });

  it("turno a mano con seña abierta y errores", async () => {
    renderUi(<ManualBookingForm />);
    await ready("Cargar turno");
    await userEvent.click(screen.getByRole("button", { name: /Sin seña/ }));
    await userEvent.click(screen.getByRole("button", { name: "Cargar turno" }));
    await screen.findByText("Elegí el puesto");
    expectBig("turno a mano");
  });

  it("carga retrospectiva cobrada, pendiente y con errores", async () => {
    renderUi(<RetrospectiveForm />);
    await ready("Cargar trabajo");
    await userEvent.click(screen.getByRole("button", { name: "Cargar trabajo" }));
    await screen.findByText("Elegí al menos un servicio");
    expectBig("retrospectiva cobrada");
    await userEvent.click(screen.getByRole("button", { name: /Cobrado \(tocá/ }));
    expectBig("retrospectiva pendiente");
  });

  it("recepción completa", async () => {
    renderUi(<InspectionPageClient id="j1" />);
    await screen.findByLabelText("Suciedad");
    expectBig("recepción");
  });

  it("catálogo caído: el aviso de error de las cargas tampoco tiene controles chicos", async () => {
    const { resourcesApi } = await import("@/services/config.service");
    (resourcesApi.list as jest.Mock).mockRejectedValue(new Error("x"));
    renderUi(<WalkInForm />);
    await screen.findByRole("alert");
    expectBig("carga con catálogo caído");
  });
});
