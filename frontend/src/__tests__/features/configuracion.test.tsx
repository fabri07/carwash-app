import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import {
  AxiosError,
  AxiosHeaders,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import ConfigPage from "@/app/(protected)/configuracion/page";
import {
  businessHoursApi,
  getSettings,
  patchSettings,
  paymentMethodsApi,
  resourcesApi,
  vehicleSizesApi,
} from "@/services/config.service";
import { useAuthStore } from "@/stores/authStore";
import { useToastStore } from "@/stores/toastStore";
import type { BusinessSettings, PaymentMethod, Resource } from "@/types/api";

jest.mock("@/services/config.service", () => {
  const crud = () => ({ list: jest.fn(), create: jest.fn(), update: jest.fn(), remove: jest.fn() });
  return {
    // La pestaña Vehículos (PR 4.4) arma sus APIs con el `crud` real al importarse.
    crud: jest.requireActual("@/services/config.service").crud,
    getSettings: jest.fn(),
    patchSettings: jest.fn(),
    vehicleSizesApi: crud(),
    resourcesApi: crud(),
    businessHoursApi: crud(),
    paymentMethodsApi: crud(),
  };
});

jest.setTimeout(20_000);

// Radix (dialog, switch, tabs) usa APIs de puntero que jsdom no trae.
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

const config = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
function apiError(status: number, code: string) {
  const response = {
    status,
    data: { detail: { code, message: "x" } },
    statusText: "",
    headers: {},
    config,
  } as AxiosResponse;
  return new AxiosError("x", "ERR_BAD_REQUEST", config, null, response);
}

const owner = {
  id: "u1",
  email: "dueno@ejemplo.com",
  username: null,
  role: "OWNER" as const,
  tenant_id: "t1",
  permission_profile_id: null,
};

const settings: BusinessSettings = {
  slug: "mi-lavadero",
  name: "Mi Lavadero",
  address: "Calle 1",
  whatsapp_e164: null,
  instagram_url: null,
  welcome_text: null,
  cancellation_policy: null,
  deposit_enabled: false,
  deposit_bps: 2000,
  tolerance_min: 20,
  late_cancel_threshold_min: 120,
  hold_min: 30,
  min_advance_min: 5,
  quote_validity_hours: 24,
  currency: "ARS",
  timezone: "America/Argentina/Cordoba",
  palette: "default",
  font: "default",
  logo_key: null,
  logo_url: null,
  onboarding_completed_at: null,
};

const stamp = {
  tenant_id: "t1",
  created_at: "2026-11-05T00:00:00Z",
  updated_at: "2026-11-05T00:00:00Z",
};
const puesto: Resource = { id: "r1", name: "Puesto 1", sort_order: 0, ...stamp };
const efectivo: PaymentMethod = {
  id: "m1",
  code: "EFECTIVO",
  label: "Efectivo",
  commission_bps: 0,
  settlement_account: null,
  for_income: true,
  for_expense: false,
  ...stamp,
};

function page<T>(items: T[]) {
  return { items, total: items.length, limit: 25, offset: 0, has_more: false };
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ConfigPage />
    </QueryClientProvider>,
  );
}

async function openTab(name: string) {
  await userEvent.click(screen.getByRole("tab", { name }));
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
  useAuthStore.setState({ user: owner, permissions: [], permissionsLoaded: true });
  (getSettings as jest.Mock).mockResolvedValue(settings);
  (patchSettings as jest.Mock).mockImplementation(async (patch) => ({ ...settings, ...patch }));
  (vehicleSizesApi.list as jest.Mock).mockResolvedValue(page([]));
  (resourcesApi.list as jest.Mock).mockResolvedValue(page([puesto]));
  (businessHoursApi.list as jest.Mock).mockResolvedValue(page([]));
  (paymentMethodsApi.list as jest.Mock).mockResolvedValue(page([efectivo]));
});

describe("/configuracion — acceso", () => {
  it("sin dueño ni CATALOGO_EDITAR: aviso y ninguna consulta", () => {
    useAuthStore.setState({
      user: { ...owner, role: "STAFF", email: null, username: "ana" },
      permissions: ["AGENDA_VER"],
      permissionsLoaded: true,
    });
    renderPage();
    expect(screen.getByRole("heading", { name: /no tenés acceso/i })).toBeInTheDocument();
    expect(getSettings).not.toHaveBeenCalled();
    expect(resourcesApi.list).not.toHaveBeenCalled();
  });

  it("tras recargar, hasta que llegan los permisos muestra 'Cargando' y no un 'sin acceso' falso", () => {
    useAuthStore.setState({
      user: { ...owner, role: "STAFF", email: null, username: "ana" },
      permissions: [],
      permissionsLoaded: false,
    });
    renderPage();
    expect(screen.getByText("Cargando…")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /no tenés acceso/i })).toBeNull();
  });

  it("STAFF con CATALOGO_EDITAR: solo las pestañas del catálogo, sin pedir /settings", async () => {
    useAuthStore.setState({
      user: { ...owner, role: "STAFF", email: null, username: "ana" },
      permissions: ["CATALOGO_EDITAR"],
      permissionsLoaded: true,
    });
    renderPage();
    expect(screen.queryByRole("tab", { name: "Negocio" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Seña y reservas" })).toBeNull();
    expect(screen.getByRole("tab", { name: "Horarios" })).toBeInTheDocument();
    await openTab("Puestos");
    expect(await screen.findByRole("table", { name: "Puestos" })).toBeInTheDocument();
    expect(getSettings).not.toHaveBeenCalled();
  });
});

describe("Negocio", () => {
  it("guarda sin mandar el slug si no cambió, y los vacíos van como texto vacío", async () => {
    renderPage();
    const address = await screen.findByLabelText("Dirección del local");
    expect(address).toHaveValue("Calle 1");
    await userEvent.clear(address);
    await userEvent.type(address, "Calle 2");
    await userEvent.click(screen.getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() => expect(patchSettings).toHaveBeenCalledTimes(1));
    expect(patchSettings).toHaveBeenCalledWith({
      address: "Calle 2",
      whatsapp_e164: "",
      instagram_url: "",
      welcome_text: "",
      cancellation_policy: "",
    });
    expect(useToastStore.getState().toasts[0]?.message).toMatch(/guardados/);
  });

  it("valida el WhatsApp y manda al campo el 409 SLUG_TAKEN", async () => {
    (patchSettings as jest.Mock).mockRejectedValueOnce(apiError(409, "SLUG_TAKEN"));
    renderPage();
    const whatsapp = await screen.findByLabelText("WhatsApp");
    await userEvent.type(whatsapp, "351555");
    await userEvent.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText(/formato internacional/i)).toBeInTheDocument();
    expect(patchSettings).not.toHaveBeenCalled();

    await userEvent.clear(whatsapp);
    const slug = screen.getByLabelText("Dirección de tu página");
    await userEvent.clear(slug);
    await userEvent.type(slug, "otro-nombre");
    await userEvent.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText(/la usa otro negocio/i)).toBeInTheDocument();
    expect(slug).toHaveAttribute("aria-invalid", "true");
    expect(patchSettings).toHaveBeenCalledWith(expect.objectContaining({ slug: "otro-nombre" }));
  });
});

describe("Seña y reservas", () => {
  it("convierte el porcentaje a puntos básicos y guarda todo junto", async () => {
    renderPage();
    await openTab("Seña y reservas");
    const percent = await screen.findByLabelText("Porcentaje de seña (%)");
    expect(percent).toHaveValue("20");
    expect(percent).toBeDisabled(); // seña apagada
    await userEvent.click(screen.getByRole("switch"));
    await waitFor(() => expect(percent).toBeEnabled());
    await userEvent.clear(percent);
    await userEvent.type(percent, "12,5");
    await userEvent.click(screen.getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() => expect(patchSettings).toHaveBeenCalledTimes(1));
    expect(patchSettings).toHaveBeenCalledWith({
      deposit_enabled: true,
      deposit_bps: 1250,
      hold_min: 30,
      min_advance_min: 5,
      tolerance_min: 20,
      late_cancel_threshold_min: 120,
      quote_validity_hours: 24,
    });
  });

  it("rechaza un minuto que no es entero", async () => {
    renderPage();
    await openTab("Seña y reservas");
    const hold = await screen.findByLabelText(/se guarda el turno esperando/i);
    await userEvent.clear(hold);
    await userEvent.type(hold, "1,5");
    await userEvent.click(screen.getByRole("button", { name: "Guardar cambios" }));
    expect(await screen.findByText(/poné un número entero/i)).toBeInTheDocument();
    expect(patchSettings).not.toHaveBeenCalled();
  });
});

describe("Catálogo", () => {
  it("puestos: alta con la misma Idempotency-Key en el reintento", async () => {
    (resourcesApi.create as jest.Mock)
      .mockRejectedValueOnce(apiError(409, "NAME_TAKEN"))
      .mockResolvedValueOnce(puesto);
    renderPage();
    await openTab("Puestos");
    await userEvent.click(await screen.findByRole("button", { name: /nuevo puesto/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo puesto" });
    await userEvent.type(within(dialog).getByLabelText("Nombre"), "Puesto 1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear puesto" }));
    expect(await within(dialog).findByText(/ya existe uno con ese nombre/i)).toBeInTheDocument();
    await userEvent.clear(within(dialog).getByLabelText("Nombre"));
    await userEvent.type(within(dialog).getByLabelText("Nombre"), "Puesto 2");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear puesto" }));
    await waitFor(() => expect(resourcesApi.create).toHaveBeenCalledTimes(2));
    const [first, firstKey] = (resourcesApi.create as jest.Mock).mock.calls[0]!;
    const [second, secondKey] = (resourcesApi.create as jest.Mock).mock.calls[1]!;
    expect(first).toEqual({ name: "Puesto 1", sort_order: 0 });
    expect(second).toEqual({ name: "Puesto 2", sort_order: 0 });
    expect(secondKey).toBe(firstKey);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("quitar algo en uso deja el motivo en el diálogo", async () => {
    (resourcesApi.remove as jest.Mock).mockRejectedValueOnce(apiError(409, "IN_USE"));
    renderPage();
    await openTab("Puestos");
    await userEvent.click(await screen.findByRole("button", { name: "Quitar puesto Puesto 1" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Quitar" }));
    expect(await within(dialog).findByText(/todavía se usa/i)).toBeInTheDocument();
    expect(resourcesApi.remove).toHaveBeenCalledWith("r1");
  });

  it("medios de pago: la comisión va en puntos básicos y la cuenta vacía como null", async () => {
    (paymentMethodsApi.create as jest.Mock).mockResolvedValueOnce(efectivo);
    renderPage();
    await openTab("Medios de pago");
    await userEvent.click(await screen.findByRole("button", { name: /nuevo medio de pago/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo medio de pago" });
    await userEvent.type(within(dialog).getByLabelText("Código"), "mp");
    await userEvent.type(within(dialog).getByLabelText("Nombre"), "Mercado Pago");
    await userEvent.clear(within(dialog).getByLabelText("Comisión (%)"));
    await userEvent.type(within(dialog).getByLabelText("Comisión (%)"), "1,5");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear medio de pago" }));
    await waitFor(() => expect(paymentMethodsApi.create).toHaveBeenCalledTimes(1));
    expect((paymentMethodsApi.create as jest.Mock).mock.calls[0]![0]).toEqual({
      code: "MP",
      label: "Mercado Pago",
      commission_bps: 150,
      settlement_account: null,
      for_income: true,
      for_expense: false,
    });
  });

  it("horarios: valida que cierre después de abrir y manda día numérico", async () => {
    (businessHoursApi.create as jest.Mock).mockResolvedValueOnce({});
    renderPage();
    await openTab("Horarios");
    await userEvent.click(await screen.findByRole("button", { name: /nueva franja/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nueva franja" });
    await userEvent.selectOptions(within(dialog).getByLabelText("Día"), "Martes");
    const closes = within(dialog).getByLabelText("Cierra");
    await userEvent.clear(closes);
    await userEvent.type(closes, "08:00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear franja" }));
    expect(await within(dialog).findByText(/cerrar después de abrir/i)).toBeInTheDocument();
    expect(businessHoursApi.create).not.toHaveBeenCalled();
    await userEvent.clear(closes);
    await userEvent.type(closes, "20:00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear franja" }));
    await waitFor(() => expect(businessHoursApi.create).toHaveBeenCalledTimes(1));
    expect((businessHoursApi.create as jest.Mock).mock.calls[0]![0]).toEqual({
      weekday: 2,
      opens_at: "09:00",
      closes_at: "20:00",
    });
  });

  it("franja que se pisa: explica el motivo en el diálogo", async () => {
    const overlap = apiError(422, "VALIDATION_ERROR");
    (overlap.response!.data as { detail: { message: string } }).detail.message =
      "the range overlaps another business-hours range that day";
    (businessHoursApi.create as jest.Mock).mockRejectedValueOnce(overlap);
    renderPage();
    await openTab("Horarios");
    await userEvent.click(await screen.findByRole("button", { name: /nueva franja/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nueva franja" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear franja" }));
    expect(await within(dialog).findByText(/se pisa con otra del mismo día/i)).toBeInTheDocument();
  });

  it("al quitar el último ítem de la última página vuelve a la anterior", async () => {
    let removed = false;
    (resourcesApi.list as jest.Mock).mockImplementation(async ({ offset }: { offset: number }) => {
      if (offset === 0)
        return { items: [puesto], total: removed ? 25 : 26, limit: 25, offset, has_more: !removed };
      return removed
        ? { items: [], total: 25, limit: 25, offset, has_more: false }
        : {
            items: [{ ...puesto, id: "r26", name: "Puesto 26" }],
            total: 26,
            limit: 25,
            offset,
            has_more: false,
          };
    });
    (resourcesApi.remove as jest.Mock).mockImplementation(async () => {
      removed = true;
    });
    renderPage();
    await openTab("Puestos");
    await userEvent.click(await screen.findByRole("button", { name: "Página siguiente" }));
    await userEvent.click(await screen.findByRole("button", { name: "Quitar puesto Puesto 26" }));
    const dialog = await screen.findByRole("dialog");
    (resourcesApi.list as jest.Mock).mockClear();
    await userEvent.click(within(dialog).getByRole("button", { name: "Quitar" }));
    // Pide la página vacía (offset 25) y retrocede a la que tiene datos (offset 0).
    await waitFor(() => {
      const offsets = (resourcesApi.list as jest.Mock).mock.calls.map(([p]) => p.offset);
      expect(offsets).toContain(25);
      expect(offsets[offsets.length - 1]).toBe(0);
    });
    expect(await screen.findByText("Puesto 1")).toBeInTheDocument();
  });
});

describe("/configuracion — tamaños y vehículos (PR 4.4)", () => {
  it("quitar un tamaño invalida también el catálogo de vehículos", async () => {
    const size = { id: "s9", code: "PICKUP", label: "Pickup", sort_order: 0, ...stamp };
    (vehicleSizesApi.list as jest.Mock).mockResolvedValue(page([size]));
    (vehicleSizesApi.remove as jest.Mock).mockResolvedValue(undefined);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = jest.spyOn(qc, "invalidateQueries");
    render(
      <QueryClientProvider client={qc}>
        <ConfigPage />
      </QueryClientProvider>,
    );
    await openTab("Tamaños");
    await userEvent.click(await screen.findByRole("button", { name: "Quitar tamaño Pickup" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Quitar" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ["vehicles"] }));
    expect(spy).toHaveBeenCalledWith({ queryKey: ["config", "vehicle-sizes"] });
  });
});
