/**
 * T3 (6.2) — Permisos: cada acción se oculta sin su permiso (AGENDA_VER, JOBS_OPERAR,
 * COBROS_REGISTRAR, COBROS_ANULAR, TURNOS_GESTIONAR); las rutas sin AGENDA_VER no cargan nada; el
 * servidor manda (403 mostrado bien).
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AgendaPageClient } from "@/features/operacion/AgendaPageClient";
import { BookingArrivalClient } from "@/features/operacion/BookingArrivalClient";
import { InspectionPageClient } from "@/features/operacion/InspectionForm";
import { JobPageClient } from "@/features/operacion/JobPageClient";
import ManualBookingPage from "@/app/(protected)/operacion/turno-a-mano/page";
import RetrospectivePage from "@/app/(protected)/operacion/trabajo-hecho/page";
import WalkInPage from "@/app/(protected)/operacion/nuevo/page";
import { useAuthStore } from "@/stores/authStore";
import type { PermissionCode } from "@/types/api";
import {
  ALL_PERMS,
  bookingEntry,
  createServer,
  day,
  entry,
  job,
  loginAs,
  renderUi,
  resetUnits,
  staff,
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

const without = (p: PermissionCode): PermissionCode[] => ALL_PERMS.filter((x) => x !== p);
const DENIED = /No tenés acceso a/;

describe("Rutas sin AGENDA_VER", () => {
  it("/agenda: no se ve nada y NO se pide la agenda al servidor", async () => {
    loginAs(without("AGENDA_VER"));
    renderUi(<AgendaPageClient />);
    expect(await screen.findByText(DENIED)).toBeInTheDocument();
    expect(screen.queryByText("Ingreso sin turno")).toBeNull();
    await new Promise((r) => setTimeout(r, 50));
    expect(server.gets).toHaveLength(0);
  });

  it("/operacion/{id}: ni el job ni los botones, y no se hace ningún GET del job", async () => {
    loginAs(without("AGENDA_VER"));
    renderUi(<JobPageClient id="j1" />);
    expect(await screen.findByText(DENIED)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    await new Promise((r) => setTimeout(r, 50));
    expect(server.gets).toHaveLength(0);
  });

  it("/operacion/turno/{id}: ni el turno ni LLEGÓ", async () => {
    loginAs(without("AGENDA_VER"));
    server.defaultDay = day([bookingEntry()]);
    renderUi(<BookingArrivalClient bookingId="b1" />);
    expect(await screen.findByText(DENIED)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "LLEGÓ" })).toBeNull();
    expect(server.gets).toHaveLength(0);
  });

  it.each([
    ["/operacion/nuevo", WalkInPage],
    ["/operacion/turno-a-mano", ManualBookingPage],
    ["/operacion/trabajo-hecho", RetrospectivePage],
  ])(
    "HALLAZGO (a decidir): %s con todos los permisos MENOS AGENDA_VER no debería abrir (la operación pide AGENDA_VER, §1)",
    async (_route, Page) => {
      loginAs(without("AGENDA_VER"));
      renderUi(<Page />);
      await new Promise((r) => setTimeout(r, 100));
      expect(screen.queryByText(DENIED)).toBeInTheDocument();
    },
  );

  it("sin permisos cargados todavía (recarga) un empleado ve 'Cargando…', no los botones ni el 'sin acceso'", async () => {
    useAuthStore.setState({ user: staff, permissions: [], permissionsLoaded: false });
    renderUi(<JobPageClient id="j1" />);
    expect(screen.getByText("Cargando…")).toBeInTheDocument();
    expect(screen.queryByText(DENIED)).toBeNull();
    expect(server.gets).toHaveLength(0);
  });

  it("el dueño (OWNER) ve todo aunque su lista de permisos esté vacía", async () => {
    loginAs("owner");
    renderUi(<AgendaPageClient />);
    expect(await screen.findByText("Ingreso sin turno")).toBeInTheDocument();
    expect(screen.getByText("Turno a mano")).toBeInTheDocument();
    expect(screen.getByText("Cargar trabajo hecho")).toBeInTheDocument();
  });
});

describe("Accesos de la agenda según permiso", () => {
  it.each([
    ["JOBS_OPERAR", ["Ingreso sin turno", "Cargar trabajo hecho"]],
    ["TURNOS_GESTIONAR", ["Turno a mano"]],
  ] as const)("sin %s desaparecen %j y quedan los demás", async (perm, hidden) => {
    loginAs(without(perm));
    renderUi(<AgendaPageClient />);
    await screen.findByText(/Agenda/);
    for (const h of hidden) expect(screen.queryByText(h)).toBeNull();
    const all = ["Ingreso sin turno", "Turno a mano", "Cargar trabajo hecho"];
    for (const v of all.filter((a) => !(hidden as readonly string[]).includes(a))) {
      expect(screen.getByText(v)).toBeInTheDocument();
    }
  });

  it("solo AGENDA_VER: ve la agenda y ningún acceso para cargar", async () => {
    loginAs(["AGENDA_VER"]);
    renderUi(<AgendaPageClient />);
    await screen.findByText(/Puesto 1/);
    expect(screen.queryByText("Ingreso sin turno")).toBeNull();
    expect(screen.queryByText("Turno a mano")).toBeNull();
    expect(screen.queryByText("Cargar trabajo hecho")).toBeNull();
  });
});

describe("Acciones del job según permiso", () => {
  const cases: [string, Partial<ReturnType<typeof job>>, PermissionCode, RegExp | string][] = [
    ["INICIAR", { status: "PRESENTE" }, "JOBS_OPERAR", "INICIAR"],
    ["FINALIZAR", { status: "EN_PROCESO" }, "JOBS_OPERAR", "FINALIZAR"],
    ["RETIRAR", { status: "COBRADO", balance_due_cents: 0 }, "JOBS_OPERAR", "RETIRAR"],
    ["COBRAR", { status: "FINALIZADO" }, "COBROS_REGISTRAR", /^COBRAR/],
  ];
  it.each(cases)("%s: está con su permiso y NO está sin él", async (_n, over, perm, name) => {
    server = createServer([job(over)]);
    loginAs(ALL_PERMS);
    const a = renderUi(<JobPageClient id="j1" />);
    expect(await screen.findByRole("button", { name })).toBeInTheDocument();
    a.unmount();

    loginAs(without(perm));
    renderUi(<JobPageClient id="j1" />);
    await screen.findByText(/Total/);
    expect(screen.queryByRole("button", { name })).toBeNull();
  });

  it("COBRAR no depende de JOBS_OPERAR (solo COBROS_REGISTRAR) y RETIRAR no depende de COBROS_*", async () => {
    server = createServer([job({ status: "FINALIZADO" })]);
    loginAs(["AGENDA_VER", "COBROS_REGISTRAR"]);
    const a = renderUi(<JobPageClient id="j1" />);
    expect(await screen.findByRole("button", { name: /^COBRAR/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "RETIRAR" })).toBeNull();
    a.unmount();
    loginAs(["AGENDA_VER", "JOBS_OPERAR"]);
    renderUi(<JobPageClient id="j1" />);
    expect(await screen.findByRole("button", { name: "RETIRAR" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^COBRAR/ })).toBeNull();
  });

  it("Anular cobro exige COBROS_ANULAR; la recepción completa exige JOBS_OPERAR", async () => {
    server = createServer([
      job({
        status: "FINALIZADO",
        balance_due_cents: 1,
        payments: [
          {
            id: "pay1",
            kind: "SALDO",
            amount_cents: 100,
            occurred_at: "2030-11-25T14:30:00Z",
            payment_method_id: "m1",
            voided: false,
          },
        ] as never,
      }),
    ]);
    loginAs(without("COBROS_ANULAR"));
    const a = renderUi(<JobPageClient id="j1" />);
    await screen.findByText(/Total/);
    expect(screen.queryByRole("button", { name: "Anular cobro" })).toBeNull();
    a.unmount();
    loginAs(ALL_PERMS);
    const b = renderUi(<JobPageClient id="j1" />);
    expect(await screen.findByRole("button", { name: "Anular cobro" })).toBeInTheDocument();
    b.unmount();

    server = createServer([job({ status: "EN_PROCESO" })]);
    loginAs(without("JOBS_OPERAR"));
    const c = renderUi(<JobPageClient id="j1" />);
    await screen.findByText(/Total/);
    expect(screen.queryByText(/Recepción completa/)).toBeNull();
    c.unmount();
    loginAs(ALL_PERMS);
    renderUi(<JobPageClient id="j1" />);
    expect(await screen.findByText(/Recepción completa/)).toBeInTheDocument();
  });

  it("la recepción (formulario) sin JOBS_OPERAR no se abre", async () => {
    loginAs(without("JOBS_OPERAR"));
    renderUi(<InspectionPageClient id="j1" />);
    expect(await screen.findByText(DENIED)).toBeInTheDocument();
  });

  it("el servidor manda: con el botón visible y permisos vencidos, el 403 se muestra en español sin perder el estado", async () => {
    loginAs(ALL_PERMS);
    const { apiError } = await import("./harness");
    server.failPost.push({
      match: /\/start$/,
      error: () => apiError(403, "FORBIDDEN", "Traceback"),
      times: -1,
    });
    renderUi(<JobPageClient id="j1" />);
    await userEvent.click(await screen.findByRole("button", { name: "INICIAR" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No tenés permiso para hacer esto.");
    expect(screen.getByRole("button", { name: "INICIAR" })).toBeEnabled();
  });
});

describe("Cargas a mano y permisos de cobro (contrato §1 y §12.5/12.7)", () => {
  it("HALLAZGO: sin COBROS_REGISTRAR la carga retrospectiva no ofrece cargar el cobro (el servidor responde 403)", async () => {
    loginAs(["AGENDA_VER", "JOBS_OPERAR"]);
    renderUi(<RetrospectivePage />);
    await screen.findByLabelText("Inicio");
    // Hoy arranca en "Cobrado" con medio e importe: el dueño del permiso de operar, sin cobros, siempre pega el 403.
    expect(screen.queryByLabelText("Importe cobrado")).toBeNull();
  });

  it("HALLAZGO: sin COBROS_REGISTRAR el turno a mano no ofrece cargar una seña cobrada", async () => {
    loginAs(["AGENDA_VER", "TURNOS_GESTIONAR"]);
    renderUi(<ManualBookingPage />);
    await screen.findByLabelText("Hora de inicio");
    expect(screen.queryByText(/Sin seña \(tocá para cargar una\)/)).toBeNull();
  });
});

describe("La agenda no muestra datos a quien no corresponde (renglones)", () => {
  it("con AGENDA_VER se ven nombre y patente; la patente no aparece en ningún href", async () => {
    server.defaultDay = day([entry({ job_status: "PRESENTE" })]);
    loginAs(["AGENDA_VER"]);
    const { container } = renderUi(<AgendaPageClient />);
    await screen.findByText(/Cliente Demo/);
    const hrefs = Array.from(container.querySelectorAll("a")).map((a) => a.getAttribute("href"));
    expect(hrefs.join(" ")).not.toMatch(/AA000AA|Cliente|5493510000001|%2B54/);
    await waitFor(() => expect(hrefs).toContain("/operacion/j1"));
  });
});
