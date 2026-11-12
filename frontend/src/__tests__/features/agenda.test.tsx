import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import {
  AxiosError,
  AxiosHeaders,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import { AgendaTab } from "@/features/configuracion/AgendaTab";
import { formatRange, localDate, localTime } from "@/lib/agenda";
import {
  createBlock,
  createOpening,
  listBlocks,
  listOpenings,
  removeBlock,
} from "@/services/agenda.service";
import { getSettings, resourcesApi } from "@/services/config.service";
import { useAuthStore } from "@/stores/authStore";
import { useToastStore } from "@/stores/toastStore";
import type { AgendaBlock, AgendaOpening, Resource } from "@/types/api";
import { agendaRangeSchema, toAgendaPayload } from "@/validation/agenda";

const axiosConfig = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
function apiError(status: number, code: string, message = "x") {
  const response = {
    status,
    data: { detail: { code, message } },
    statusText: "",
    headers: {},
    config: axiosConfig,
  } as AxiosResponse;
  return new AxiosError("x", "ERR_BAD_REQUEST", axiosConfig, null, response);
}

jest.mock("@/services/agenda.service", () => ({
  listBlocks: jest.fn(),
  listOpenings: jest.fn(),
  createBlock: jest.fn(),
  createOpening: jest.fn(),
  removeBlock: jest.fn(),
  removeOpening: jest.fn(),
  getAvailability: jest.fn(),
}));
jest.mock("@/services/config.service", () => ({
  getSettings: jest.fn(),
  resourcesApi: { list: jest.fn(), create: jest.fn(), update: jest.fn(), remove: jest.fn() },
}));

jest.setTimeout(20_000);

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.setPointerCapture = () => undefined;
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

const TZ = "America/Argentina/Cordoba";
const stamp = {
  tenant_id: "t1",
  created_at: "2026-11-05T00:00:00Z",
  updated_at: "2026-11-05T00:00:00Z",
};
const puesto: Resource = { id: "r1", name: "Puesto 1", sort_order: 0, ...stamp };
// Lunes 23/11/2026 de 00:00 a 00:00 en Córdoba (UTC−3).
const bloqueo: AgendaBlock = {
  id: "b1",
  resource_id: null,
  starts_at: "2026-11-23T03:00:00Z",
  ends_at: "2026-11-24T03:00:00Z",
  reason: "Feriado",
  created_by_user_id: "u1",
  ...stamp,
};
const apertura: AgendaOpening = {
  id: "o1",
  resource_id: "r1",
  starts_at: "2026-11-29T13:00:00Z",
  ends_at: "2026-11-29T17:00:00Z",
  reason: "Domingo especial",
  created_by_user_id: "u1",
  ...stamp,
};

function page<T>(items: T[]) {
  return { items, total: items.length, limit: 200, offset: 0, has_more: false };
}

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AgendaTab />
    </QueryClientProvider>,
  );
}

const owner = {
  id: "u1",
  email: "dueno@example.com",
  username: null,
  role: "OWNER" as const,
  tenant_id: "t1",
  permission_profile_id: null,
};

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
  useAuthStore.setState({ user: owner, permissions: [], permissionsLoaded: true });
  (getSettings as jest.Mock).mockResolvedValue({ timezone: TZ });
  (resourcesApi.list as jest.Mock).mockResolvedValue(page([puesto]));
  (listBlocks as jest.Mock).mockResolvedValue(page([bloqueo]));
  (listOpenings as jest.Mock).mockResolvedValue(page([apertura]));
});

describe("Bloqueos y aperturas", () => {
  it("lista lo vigente en la hora del negocio", async () => {
    renderTab();
    expect(await screen.findByText(/23\/11 · todo el día/)).toBeInTheDocument();
    expect(screen.getByText("Todos los puestos · Feriado")).toBeInTheDocument();
    expect(await screen.findByText(/29\/11 · 10:00 a 14:00/)).toBeInTheDocument();
    expect(await screen.findByText("Puesto 1 · Domingo especial")).toBeInTheDocument();
  });

  it("crea un bloqueo de franja y avisa los turnos que quedan adentro", async () => {
    (createBlock as jest.Mock).mockResolvedValueOnce({
      block: bloqueo,
      overlapping_bookings: [
        {
          id: "bk1",
          code: "TUR-ABC234",
          status: "CONFIRMADO",
          resource_id: "r1",
          start_at: "2026-11-23T13:00:00Z",
          end_at: "2026-11-23T14:00:00Z",
          customer_name: "Cliente Demo",
        },
      ],
    });
    renderTab();
    await userEvent.click(await screen.findByRole("button", { name: /nuevo bloqueo/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo bloqueo" });
    const dia = within(dialog).getByLabelText("Día");
    await userEvent.clear(dia);
    await userEvent.type(dia, "2026-11-23");
    await userEvent.click(within(dialog).getByRole("switch"));
    await userEvent.type(within(dialog).getByLabelText("Desde"), "09:00");
    await userEvent.type(within(dialog).getByLabelText("Hasta"), "12:00");
    await userEvent.selectOptions(within(dialog).getByLabelText("Puesto"), "r1");
    await userEvent.type(within(dialog).getByLabelText("Motivo (opcional)"), "Mantenimiento");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear bloqueo" }));
    await waitFor(() => expect(createBlock).toHaveBeenCalledTimes(1));
    const [payload, key] = (createBlock as jest.Mock).mock.calls[0]!;
    expect(payload).toEqual({
      date: "2026-11-23",
      start_time: "09:00",
      end_time: "12:00",
      resource_id: "r1",
      reason: "Mantenimiento",
    });
    expect(typeof key).toBe("string");
    const aviso = await screen.findByText(/quedan dentro del bloqueo: avisales/i);
    expect(aviso.parentElement).toHaveTextContent("Cliente Demo");
    expect(aviso.parentElement).toHaveTextContent("TUR-ABC234");
    await userEvent.click(screen.getByRole("button", { name: "Entendido" }));
    expect(screen.queryByText(/quedan dentro del bloqueo/i)).toBeNull();
  });

  it("un reintento que el servidor ya había guardado no dice 'creado' sin avisar de los turnos", async () => {
    (createBlock as jest.Mock).mockRejectedValueOnce(apiError(409, "DUPLICATE_IDEMPOTENT"));
    renderTab();
    await userEvent.click(await screen.findByRole("button", { name: /nuevo bloqueo/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo bloqueo" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear bloqueo" }));
    await waitFor(() => expect(createBlock).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.message)).toEqual([
        "Ese bloqueo ya se había creado. Revisá si quedaron turnos dentro para avisarles.",
      ]),
    );
    expect(screen.queryByRole("dialog", { name: "Nuevo bloqueo" })).toBeNull();
  });

  it("una franja que termina antes de empezar no se manda", async () => {
    renderTab();
    await userEvent.click(await screen.findByRole("button", { name: /nueva apertura/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nueva apertura especial" });
    const hasta = within(dialog).getByLabelText("Hasta");
    await userEvent.clear(hasta);
    await userEvent.type(hasta, "08:00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear apertura" }));
    expect(await within(dialog).findByText(/terminar después de empezar/i)).toBeInTheDocument();
    expect(createOpening).not.toHaveBeenCalled();
  });

  it("crea una apertura y quita un bloqueo con confirmación", async () => {
    (createOpening as jest.Mock).mockResolvedValueOnce(apertura);
    (removeBlock as jest.Mock).mockResolvedValueOnce(undefined);
    renderTab();
    await userEvent.click(await screen.findByRole("button", { name: /nueva apertura/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nueva apertura especial" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear apertura" }));
    await waitFor(() => expect(createOpening).toHaveBeenCalledTimes(1));
    expect((createOpening as jest.Mock).mock.calls[0]![0]).toMatchObject({
      start_time: "09:00",
      end_time: "13:00",
      resource_id: null,
    });
    await userEvent.click(await screen.findByRole("button", { name: /quitar bloqueo: .*23\/11/i }));
    await userEvent.click(await screen.findByRole("button", { name: "Quitar" }));
    await waitFor(() => expect(removeBlock).toHaveBeenCalledWith("b1"));
  });

  it("el error al quitar queda en el diálogo", async () => {
    const config = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
    const response = {
      status: 404,
      data: { detail: { code: "NOT_FOUND", message: "x" } },
      statusText: "",
      headers: {},
      config,
    } as AxiosResponse;
    (removeBlock as jest.Mock).mockRejectedValueOnce(
      new AxiosError("x", "ERR_BAD_REQUEST", config, null, response),
    );
    renderTab();
    await userEvent.click(await screen.findByRole("button", { name: /quitar bloqueo/i }));
    await userEvent.click(await screen.findByRole("button", { name: "Quitar" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("sin TURNOS_GESTIONAR se ve pero no se edita", async () => {
    useAuthStore.setState({
      user: { ...owner, role: "STAFF" as const, permission_profile_id: "p1" },
      permissions: ["AGENDA_VER"],
      permissionsLoaded: true,
    });
    renderTab();
    expect(await screen.findByText(/23\/11 · todo el día/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /nuevo bloqueo/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /quitar bloqueo/i })).toBeNull();
  });

  it("muestra el error si no se pueden cargar", async () => {
    (listBlocks as jest.Mock).mockRejectedValue(new Error("boom"));
    renderTab();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});

describe("lib/agenda y validation/agenda", () => {
  it("formatea rangos en la zona del negocio", () => {
    expect(formatRange("2026-11-23T03:00:00Z", "2026-11-25T03:00:00Z", TZ)).toMatch(
      /23\/11 al .*24\/11 · todo el día/,
    );
    expect(formatRange("2026-11-24T01:00:00Z", "2026-11-24T05:00:00Z", TZ)).toMatch(
      /23\/11 22:00 a .*24\/11 02:00/,
    );
    expect(localTime("2026-11-23T12:30:00Z", TZ)).toBe("09:30");
    expect(localDate(new Date("2026-11-24T02:00:00Z"), TZ)).toBe("2026-11-23");
  });

  it("arma el payload: día completo sin horas, todos los puestos y sin motivo", () => {
    const v = agendaRangeSchema.parse({
      date: "2026-11-23",
      full_day: true,
      start_time: "",
      end_time: "",
      resource_id: "",
      reason: " ",
    });
    expect(toAgendaPayload(v)).toEqual({ date: "2026-11-23", resource_id: null });
    expect(() =>
      agendaRangeSchema.parse({ ...v, full_day: false, start_time: "", end_time: "" }),
    ).toThrow();
  });
});
