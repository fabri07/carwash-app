import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import {
  AxiosError,
  AxiosHeaders,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import MyBusinessPage from "@/app/(protected)/configuracion/mi-negocio/page";
import OperationPage from "@/app/(protected)/configuracion/operacion/page";
import {
  createBusinessHour,
  createPaymentMethod,
  createResource,
  createVehicleSize,
  deleteBusinessHour,
  deletePaymentMethod,
  deleteResource,
  deleteVehicleSize,
  getSettings,
  listBusinessHours,
  listPaymentMethods,
  listResources,
  listVehicleSizes,
  updateBusinessHour,
  updatePaymentMethod,
  updateResource,
  updateSettings,
  updateVehicleSize,
} from "@/services/configuracion.service";
import { useAuthStore } from "@/stores/authStore";
import { useToastStore } from "@/stores/toastStore";
import type {
  BusinessHour,
  PaymentMethod,
  Resource,
  TenantSettings,
  VehicleSize,
} from "@/types/api";

jest.mock("@/services/configuracion.service", () => ({
  getSettings: jest.fn(),
  updateSettings: jest.fn(),
  listVehicleSizes: jest.fn(),
  createVehicleSize: jest.fn(),
  updateVehicleSize: jest.fn(),
  deleteVehicleSize: jest.fn(),
  listResources: jest.fn(),
  createResource: jest.fn(),
  updateResource: jest.fn(),
  deleteResource: jest.fn(),
  listBusinessHours: jest.fn(),
  createBusinessHour: jest.fn(),
  updateBusinessHour: jest.fn(),
  deleteBusinessHour: jest.fn(),
  listPaymentMethods: jest.fn(),
  createPaymentMethod: jest.fn(),
  updatePaymentMethod: jest.fn(),
  deletePaymentMethod: jest.fn(),
}));

// Flujos completos de formulario: bajo carga del CI pasan los 5 s por defecto.
jest.setTimeout(20_000);

// Radix (dialog, switch, checkbox, tabs) usa APIs de puntero que jsdom no trae.
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
function axiosError(status: number, data: unknown) {
  const response = { status, data, statusText: "", headers: {}, config } as AxiosResponse;
  return new AxiosError("x", "ERR_BAD_REQUEST", config, null, response);
}
const apiError = (status: number, code: string) =>
  axiosError(status, { detail: { code, message: "x" } });

const owner = {
  id: "u1",
  email: "dueno@ejemplo.com",
  username: null,
  role: "OWNER" as const,
  tenant_id: "t1",
  permission_profile_id: null,
};
const staff = { ...owner, id: "u2", role: "STAFF" as const, email: null, username: "ana" };

const stamps = {
  tenant_id: "t1",
  created_at: "2026-10-12T00:00:00Z",
  updated_at: "2026-10-12T00:00:00Z",
};

const settings: TenantSettings = {
  slug: "lavadero-ejemplo",
  address: "Calle Falsa 123",
  whatsapp_e164: null,
  instagram_url: null,
  welcome_text: "Hola",
  cancellation_policy: null,
  deposit_enabled: false,
  deposit_bps: 2000,
  tolerance_min: 20,
  late_cancel_threshold_min: 720,
  hold_min: 30,
  min_advance_min: 5,
  palette: "default",
  font: "default",
  onboarding_completed: false,
  updated_at: "2026-10-12T00:00:00Z",
};

const hours: BusinessHour[] = [
  { id: "h2", weekday: 2, opens_at: "09:00:00", closes_at: "18:00:00", ...stamps },
  { id: "h1", weekday: 1, opens_at: "14:00:00", closes_at: "19:00:00", ...stamps },
  { id: "h0", weekday: 1, opens_at: "08:30:00", closes_at: "12:00:00", ...stamps },
];
const box1: Resource = { id: "r1", name: "Box 1", sort_order: 0, ...stamps };
const credito: PaymentMethod = {
  id: "m1",
  code: "CREDITO",
  label: "Crédito",
  commission_bps: 350,
  settlement_account: "Banco",
  for_income: true,
  for_expense: false,
  ...stamps,
};
const chico: VehicleSize = { id: "v1", code: "CHICO", label: "Chico", sort_order: 0, ...stamps };

function page<T>(items: T[]) {
  return { items, total: items.length, limit: 25, offset: 0, has_more: false };
}

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const lastToast = () => useToastStore.getState().toasts.at(-1);

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
  useAuthStore.setState({ user: owner, permissions: [] });
  (getSettings as jest.Mock).mockResolvedValue(settings);
  (updateSettings as jest.Mock).mockImplementation(async (patch) => ({ ...settings, ...patch }));
  (listBusinessHours as jest.Mock).mockResolvedValue(page(hours));
  (listResources as jest.Mock).mockResolvedValue(page([box1]));
  (listPaymentMethods as jest.Mock).mockResolvedValue(page([credito]));
  (listVehicleSizes as jest.Mock).mockResolvedValue(page([chico]));
});

// ── Mi negocio ──────────────────────────────────────────────────────────────

describe("/configuracion/mi-negocio", () => {
  async function renderPage() {
    renderWithClient(<MyBusinessPage />);
    await screen.findByRole("heading", { name: "Datos del negocio" });
  }
  const section = (name: string) => screen.getByRole("region", { name });

  it("un STAFF ve el aviso y no se pide nada al backend", () => {
    useAuthStore.setState({ user: staff, permissions: ["CATALOGO_EDITAR"] });
    renderWithClient(<MyBusinessPage />);
    expect(screen.getByRole("heading", { name: /no tenés acceso/i })).toBeInTheDocument();
    expect(getSettings).not.toHaveBeenCalled();
  });

  it("si la configuración no carga, lo dice", async () => {
    (getSettings as jest.Mock).mockRejectedValue(apiError(500, "INTERNAL_ERROR"));
    renderWithClient(<MyBusinessPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/no pudo procesar/i);
  });

  it("Datos del negocio manda solo sus campos; vacío es null", async () => {
    await renderPage();
    const datos = section("Datos del negocio");
    expect(within(datos).getByText(/4\/280 caracteres/)).toBeInTheDocument();
    await userEvent.clear(within(datos).getByLabelText("Dirección del local (opcional)"));
    await userEvent.type(within(datos).getByLabelText("WhatsApp (opcional)"), "11 2345-6789");
    await userEvent.type(within(datos).getByLabelText("Texto de bienvenida (opcional)"), "!");
    expect(within(datos).getByText(/5\/280 caracteres/)).toBeInTheDocument();
    await userEvent.click(within(datos).getByRole("button", { name: "Guardar" }));
    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith({
        slug: "lavadero-ejemplo",
        address: null,
        whatsapp_e164: "11 2345-6789",
        instagram_url: null,
        welcome_text: "Hola!",
        cancellation_policy: null,
      }),
    );
    await waitFor(() => expect(lastToast()?.message).toBe("Cambios guardados."));
  });

  it("valida en el cliente: dirección, Instagram y bienvenida", async () => {
    await renderPage();
    const datos = section("Datos del negocio");
    const slug = within(datos).getByLabelText("Dirección de tu página");
    await userEvent.clear(slug);
    await userEvent.type(slug, "ab");
    await userEvent.type(within(datos).getByLabelText("Instagram (opcional)"), "instagram.com/x");
    fireEvent.change(within(datos).getByLabelText("Texto de bienvenida (opcional)"), {
      target: { value: "x".repeat(281) },
    });
    await userEvent.click(within(datos).getByRole("button", { name: "Guardar" }));
    expect(await within(datos).findByText(/de 3 a 40 caracteres/i)).toBeInTheDocument();
    expect(within(datos).getByText(/que empiece con https/i)).toBeInTheDocument();
    expect(within(datos).getByText("Máximo 280 caracteres")).toBeInTheDocument();
    expect(updateSettings).not.toHaveBeenCalled();
  });

  it("SLUG_TAKEN cae en el campo de la dirección", async () => {
    (updateSettings as jest.Mock).mockRejectedValue(apiError(409, "SLUG_TAKEN"));
    await renderPage();
    const datos = section("Datos del negocio");
    await userEvent.click(within(datos).getByRole("button", { name: "Guardar" }));
    expect(
      await within(datos).findByText("Esa dirección ya la usa otro negocio."),
    ).toBeInTheDocument();
    expect(within(datos).getByLabelText("Dirección de tu página")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("un 422 del WhatsApp cae en el campo WhatsApp", async () => {
    (updateSettings as jest.Mock).mockRejectedValue(
      axiosError(422, {
        detail: [{ loc: ["body", "whatsapp_e164"], msg: "Value error", type: "value_error" }],
      }),
    );
    await renderPage();
    const datos = section("Datos del negocio");
    await userEvent.type(within(datos).getByLabelText("WhatsApp (opcional)"), "15 1234");
    await userEvent.click(within(datos).getByRole("button", { name: "Guardar" }));
    expect(await within(datos).findByText(/no parece un celular argentino/i)).toBeInTheDocument();
    expect(within(datos).getByLabelText("WhatsApp (opcional)")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("Seña: activarla manda el % en puntos básicos y solo esos dos campos", async () => {
    await renderPage();
    const sena = section("Seña");
    expect(
      within(sena).getByText(/el auto se recibe igual aunque no se haya pagado/i),
    ).toBeVisible();
    const pct = within(sena).getByLabelText("Porcentaje de la seña");
    expect(pct).toHaveValue("20");
    await userEvent.click(within(sena).getByRole("switch", { name: "Pedir seña al reservar" }));
    await userEvent.clear(pct);
    await userEvent.type(pct, "25");
    await userEvent.click(within(sena).getByRole("button", { name: "Guardar" }));
    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith({ deposit_enabled: true, deposit_bps: 2500 }),
    );
  });

  it("Seña: un % con decimales se muestra tal cual y guardar sin tocarlo no lo redondea", async () => {
    (getSettings as jest.Mock).mockResolvedValue({ ...settings, deposit_bps: 2550 });
    await renderPage();
    const sena = section("Seña");
    expect(within(sena).getByLabelText("Porcentaje de la seña")).toHaveValue("25,5");
    await userEvent.click(within(sena).getByRole("button", { name: "Guardar" }));
    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith({ deposit_enabled: false, deposit_bps: 2550 }),
    );
  });

  it("Seña: con el interruptor apagado el % se edita y se conserva; fuera de rango no se manda", async () => {
    await renderPage();
    const sena = section("Seña");
    const pct = within(sena).getByLabelText("Porcentaje de la seña");
    expect(pct).toBeEnabled();
    await userEvent.clear(pct);
    await userEvent.type(pct, "0");
    await userEvent.click(within(sena).getByRole("button", { name: "Guardar" }));
    expect(await within(sena).findByText("Entre 0,01 y 100")).toBeInTheDocument();
    await userEvent.clear(pct);
    await userEvent.type(pct, "10");
    await userEvent.click(within(sena).getByRole("button", { name: "Guardar" }));
    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith({ deposit_enabled: false, deposit_bps: 1000 }),
    );
  });

  it("Reglas de agenda: la cancelación tardía se ve en horas y viaja en minutos", async () => {
    await renderPage();
    const reglas = section("Reglas de agenda");
    const horas = within(reglas).getByLabelText("Cancelación tardía");
    expect(horas).toHaveValue("12");
    await userEvent.clear(horas);
    await userEvent.type(horas, "1,5");
    const tolerancia = within(reglas).getByLabelText("Tolerancia de llegada");
    await userEvent.clear(tolerancia);
    await userEvent.type(tolerancia, "15");
    await userEvent.click(within(reglas).getByRole("button", { name: "Guardar" }));
    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith({
        tolerance_min: 15,
        late_cancel_threshold_min: 90,
        hold_min: 30,
        min_advance_min: 5,
      }),
    );
  });

  it("Reglas de agenda: límites del backend en el cliente", async () => {
    await renderPage();
    const reglas = section("Reglas de agenda");
    const horas = within(reglas).getByLabelText("Cancelación tardía");
    await userEvent.clear(horas);
    await userEvent.type(horas, "200");
    const tolerancia = within(reglas).getByLabelText("Tolerancia de llegada");
    await userEvent.clear(tolerancia);
    await userEvent.type(tolerancia, "1.5");
    await userEvent.clear(within(reglas).getByLabelText("Tiempo para pagar la seña"));
    await userEvent.click(within(reglas).getByRole("button", { name: "Guardar" }));
    expect(await within(reglas).findByText(/168 horas/)).toBeInTheDocument();
    expect(within(reglas).getByText("Tiene que ser un número entero")).toBeInTheDocument();
    expect(within(reglas).getByText("Poné los minutos")).toBeInTheDocument();
    expect(updateSettings).not.toHaveBeenCalled();
  });
});

// ── Operación ───────────────────────────────────────────────────────────────

describe("/configuracion/operacion — acceso", () => {
  it("sin CATALOGO_EDITAR se ve el aviso y no se pide nada", () => {
    useAuthStore.setState({ user: staff, permissions: ["AGENDA_VER"] });
    renderWithClient(<OperationPage />);
    expect(screen.getByRole("heading", { name: /no tenés acceso/i })).toBeInTheDocument();
    expect(listBusinessHours).not.toHaveBeenCalled();
  });

  it("un STAFF con CATALOGO_EDITAR entra", async () => {
    useAuthStore.setState({ user: staff, permissions: ["CATALOGO_EDITAR"] });
    renderWithClient(<OperationPage />);
    expect(await screen.findByRole("table", { name: "Horarios de atención" })).toBeInTheDocument();
    expect(listBusinessHours).toHaveBeenCalledWith({ limit: 25, offset: 0 });
  });
});

async function openTab(name: string, table: string) {
  renderWithClient(<OperationPage />);
  if (name !== "Horarios") {
    await userEvent.click(await screen.findByRole("tab", { name }));
  }
  return screen.findByRole("table", { name: table });
}

describe("Horarios", () => {
  it("ordena por día y hora y muestra el nombre del día", async () => {
    const table = await openTab("Horarios", "Horarios de atención");
    await within(table).findAllByText("Lunes");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("Lunes08:3012:00"),
      expect.stringContaining("Lunes14:0019:00"),
      expect.stringContaining("Martes09:0018:00"),
    ]);
  });

  it("alta: valida cierre > apertura y manda el día ISO con la Idempotency-Key", async () => {
    (createBusinessHour as jest.Mock).mockResolvedValue(hours[0]);
    await openTab("Horarios", "Horarios de atención");
    await userEvent.click(screen.getByRole("button", { name: /nuevo horario/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo horario" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Agregar horario" }));
    expect(await within(dialog).findByText("Elegí el día")).toBeInTheDocument();

    await userEvent.selectOptions(within(dialog).getByLabelText("Día"), "Sábado");
    fireEvent.change(within(dialog).getByLabelText("Abre"), { target: { value: "13:00" } });
    fireEvent.change(within(dialog).getByLabelText("Cierra"), { target: { value: "09:00" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Agregar horario" }));
    expect(
      await within(dialog).findByText("Tiene que cerrar después de abrir"),
    ).toBeInTheDocument();
    expect(createBusinessHour).not.toHaveBeenCalled();

    fireEvent.change(within(dialog).getByLabelText("Cierra"), { target: { value: "18:30" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Agregar horario" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(createBusinessHour).toHaveBeenCalledWith(
      { weekday: 6, opens_at: "13:00", closes_at: "18:30" },
      expect.any(String),
    );
    expect(lastToast()?.message).toBe("Horario agregado.");
    expect(listBusinessHours).toHaveBeenCalledTimes(2);
  });

  it("edición: el VALIDATION_ERROR del backend cae en el cierre", async () => {
    (updateBusinessHour as jest.Mock)
      .mockRejectedValueOnce(apiError(422, "VALIDATION_ERROR"))
      .mockResolvedValueOnce(hours[0]);
    const table = await openTab("Horarios", "Horarios de atención");
    await within(table).findAllByText("Lunes");
    await userEvent.click(
      screen.getByRole("button", { name: "Editar el horario del martes de 09:00 a 18:00" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Editar el horario del martes" });
    expect(within(dialog).getByLabelText("Día")).toHaveValue("2");
    expect(within(dialog).getByLabelText("Abre")).toHaveValue("09:00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar cambios" }));
    expect(
      await within(dialog).findByText("Tiene que cerrar después de abrir."),
    ).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(updateBusinessHour).toHaveBeenLastCalledWith("h2", {
      weekday: 2,
      opens_at: "09:00",
      closes_at: "18:00",
    });
    expect(lastToast()?.message).toBe("Cambios guardados.");
  });

  it("borrar pide confirmación", async () => {
    (deleteBusinessHour as jest.Mock).mockResolvedValue(undefined);
    const table = await openTab("Horarios", "Horarios de atención");
    await within(table).findAllByText("Lunes");
    await userEvent.click(
      screen.getByRole("button", { name: "Borrar el horario del lunes de 08:30 a 12:00" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Borrar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(deleteBusinessHour).toHaveBeenCalledWith("h0");
    expect(lastToast()?.message).toBe("Horario borrado.");
  });

  it("si la lista falla, la tabla lo dice", async () => {
    (listBusinessHours as jest.Mock).mockRejectedValue(apiError(403, "FORBIDDEN"));
    renderWithClient(<OperationPage />);
    expect(await screen.findByText(/no tenés permiso/i)).toBeInTheDocument();
  });
});

describe("Puestos", () => {
  it("alta: NAME_TAKEN va al nombre; después se crea", async () => {
    (createResource as jest.Mock)
      .mockRejectedValueOnce(apiError(409, "NAME_TAKEN"))
      .mockResolvedValueOnce(box1);
    await openTab("Puestos", "Puestos");
    await userEvent.click(screen.getByRole("button", { name: /nuevo puesto/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo puesto" });
    await userEvent.type(within(dialog).getByLabelText("Nombre del puesto"), "Box 1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear puesto" }));
    expect(await within(dialog).findByText("Ya hay un puesto con ese nombre.")).toBeInTheDocument();
    await userEvent.clear(within(dialog).getByLabelText("Nombre del puesto"));
    await userEvent.type(within(dialog).getByLabelText("Nombre del puesto"), "Rampa");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear puesto" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [[, firstKey], [payload, secondKey]] = (createResource as jest.Mock).mock.calls;
    expect(payload).toEqual({ name: "Rampa", sort_order: 0 });
    expect(secondKey).toBe(firstKey);
  });

  it("edición manda el puesto completo", async () => {
    (updateResource as jest.Mock).mockResolvedValue(box1);
    await openTab("Puestos", "Puestos");
    await userEvent.click(await screen.findByRole("button", { name: "Editar el puesto Box 1" }));
    const dialog = await screen.findByRole("dialog", { name: "Editar Box 1" });
    const orden = within(dialog).getByLabelText("Orden en la lista");
    await userEvent.clear(orden);
    await userEvent.type(orden, "2");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() =>
      expect(updateResource).toHaveBeenCalledWith("r1", { name: "Box 1", sort_order: 2 }),
    );
  });

  it("borrar un puesto con turnos por delante muestra el 409 en el diálogo, sin cerrar", async () => {
    (deleteResource as jest.Mock)
      .mockRejectedValueOnce(apiError(409, "RESOURCE_IN_USE"))
      .mockResolvedValueOnce(undefined);
    await openTab("Puestos", "Puestos");
    await userEvent.click(await screen.findByRole("button", { name: "Borrar el puesto Box 1" }));
    const dialog = await screen.findByRole("dialog", { name: "¿Borrar el puesto Box 1?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Borrar" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "El puesto tiene turnos por delante.",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Borrar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(deleteResource).toHaveBeenLastCalledWith("r1");
    expect(lastToast()?.message).toBe('Puesto "Box 1" borrado.');
  });
});

describe("Medios de pago", () => {
  it("muestra la comisión como % y los usos", async () => {
    const table = await openTab("Medios de pago", "Medios de pago");
    const row = (await within(table).findByText("Crédito")).closest("tr")!;
    expect(within(row).getByText("3,5 %")).toBeInTheDocument();
    expect(within(row).getByText("Cobrar")).toBeInTheDocument();
    expect(within(row).getByText("Banco")).toBeInTheDocument();
  });

  it("alta: código a mayúsculas, comisión con coma a puntos básicos, CODE_TAKEN al código", async () => {
    (createPaymentMethod as jest.Mock)
      .mockRejectedValueOnce(apiError(409, "CODE_TAKEN"))
      .mockResolvedValueOnce(credito);
    await openTab("Medios de pago", "Medios de pago");
    await userEvent.click(screen.getByRole("button", { name: /nuevo medio de pago/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo medio de pago" });
    await userEvent.type(within(dialog).getByLabelText("Nombre"), "Débito");
    await userEvent.type(within(dialog).getByLabelText("Código"), "debito");
    const comision = within(dialog).getByLabelText("Comisión");
    await userEvent.clear(comision);
    await userEvent.type(comision, "120");
    await userEvent.click(
      within(dialog).getByRole("checkbox", { name: "Se usa para pagar gastos" }),
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear medio de pago" }));
    expect(await within(dialog).findByText("Entre 0 y 100")).toBeInTheDocument();

    await userEvent.clear(comision);
    await userEvent.type(comision, "1,25");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear medio de pago" }));
    expect(await within(dialog).findByText(/ya hay uno con ese código/i)).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Código")).toHaveAttribute("aria-invalid", "true");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear medio de pago" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect((createPaymentMethod as jest.Mock).mock.calls[1]![0]).toEqual({
      code: "DEBITO",
      label: "Débito",
      commission_bps: 125,
      settlement_account: null,
      for_income: true,
      for_expense: true,
    });
    expect(lastToast()?.message).toBe("Medio de pago creado.");
  });

  it("edición parte de lo guardado y avisa 'Cambios guardados.'", async () => {
    (updatePaymentMethod as jest.Mock).mockResolvedValue(credito);
    await openTab("Medios de pago", "Medios de pago");
    await userEvent.click(
      await screen.findByRole("button", { name: "Editar el medio de pago Crédito" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Editar Crédito" });
    expect(within(dialog).getByLabelText("Comisión")).toHaveValue("3,5");
    await userEvent.clear(within(dialog).getByLabelText("Cuenta de destino (opcional)"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() =>
      expect(updatePaymentMethod).toHaveBeenCalledWith("m1", {
        code: "CREDITO",
        label: "Crédito",
        commission_bps: 350,
        settlement_account: null,
        for_income: true,
        for_expense: false,
      }),
    );
    expect(lastToast()?.message).toBe("Cambios guardados.");
  });

  it("borrar", async () => {
    (deletePaymentMethod as jest.Mock).mockResolvedValue(undefined);
    await openTab("Medios de pago", "Medios de pago");
    await userEvent.click(
      await screen.findByRole("button", { name: "Borrar el medio de pago Crédito" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Borrar" }));
    await waitFor(() => expect(deletePaymentMethod).toHaveBeenCalledWith("m1"));
  });
});

describe("Tamaños", () => {
  it("alta: un replay DUPLICATE_IDEMPOTENT cuenta como hecho", async () => {
    (createVehicleSize as jest.Mock).mockRejectedValueOnce(apiError(409, "DUPLICATE_IDEMPOTENT"));
    await openTab("Tamaños", "Tamaños de vehículo");
    await userEvent.click(screen.getByRole("button", { name: /nuevo tamaño/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo tamaño" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear tamaño" }));
    expect(await within(dialog).findByText("Poné un código")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Nombre"), "Camioneta");
    await userEvent.type(within(dialog).getByLabelText("Código"), "con espacio");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear tamaño" }));
    expect(await within(dialog).findByText(/letras sin tilde/)).toBeInTheDocument();
    await userEvent.clear(within(dialog).getByLabelText("Código"));
    await userEvent.type(within(dialog).getByLabelText("Código"), "camioneta");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear tamaño" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(createVehicleSize).toHaveBeenCalledWith(
      { code: "CAMIONETA", label: "Camioneta", sort_order: 0 },
      expect.any(String),
    );
    expect(lastToast()?.message).toMatch(/ya se había creado/);
  });

  it("edición: un error sin campo queda arriba del botón; borrar", async () => {
    (updateVehicleSize as jest.Mock).mockRejectedValue(apiError(404, "NOT_FOUND"));
    (deleteVehicleSize as jest.Mock).mockResolvedValue(undefined);
    await openTab("Tamaños", "Tamaños de vehículo");
    await userEvent.click(await screen.findByRole("button", { name: "Editar el tamaño Chico" }));
    const dialog = await screen.findByRole("dialog", { name: "Editar Chico" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar cambios" }));
    expect(await within(dialog).findByText(/no se encontró/i)).toHaveAttribute("role", "alert");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await userEvent.click(screen.getByRole("button", { name: "Borrar el tamaño Chico" }));
    const confirm = await screen.findByRole("dialog", { name: "¿Borrar el tamaño Chico?" });
    await userEvent.click(within(confirm).getByRole("button", { name: "Borrar" }));
    await waitFor(() => expect(deleteVehicleSize).toHaveBeenCalledWith("v1"));
    await waitFor(() => expect(lastToast()?.message).toBe('Tamaño "Chico" borrado.'));
  });

  it("borrar la única fila de la última página vuelve a la página anterior", async () => {
    const muchos = Array.from({ length: 25 }, (_, i) => ({
      ...chico,
      id: `v${i + 10}`,
      code: `T${i}`,
      label: `Tamaño ${i}`,
    }));
    (listVehicleSizes as jest.Mock).mockImplementation(({ offset }: { offset: number }) =>
      Promise.resolve({
        items: offset === 0 ? muchos : [chico],
        total: 26,
        limit: 25,
        offset,
        has_more: offset === 0,
      }),
    );
    (deleteVehicleSize as jest.Mock).mockResolvedValue(undefined);
    await openTab("Tamaños", "Tamaños de vehículo");
    await userEvent.click(await screen.findByRole("button", { name: "Página siguiente" }));
    await userEvent.click(await screen.findByRole("button", { name: "Borrar el tamaño Chico" }));
    const confirm = await screen.findByRole("dialog", { name: "¿Borrar el tamaño Chico?" });
    await userEvent.click(within(confirm).getByRole("button", { name: "Borrar" }));
    await waitFor(() => expect(deleteVehicleSize).toHaveBeenCalledWith("v1"));
    await waitFor(() =>
      expect(listVehicleSizes).toHaveBeenLastCalledWith({ limit: 25, offset: 0 }),
    );
  });

  it("borrar un tamaño que tienen vehículos muestra el 409 en el diálogo", async () => {
    (deleteVehicleSize as jest.Mock).mockRejectedValueOnce(apiError(409, "VEHICLE_SIZE_IN_USE"));
    await openTab("Tamaños", "Tamaños de vehículo");
    await userEvent.click(await screen.findByRole("button", { name: "Borrar el tamaño Chico" }));
    const dialog = await screen.findByRole("dialog", { name: "¿Borrar el tamaño Chico?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Borrar" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Hay vehículos cargados con este tamaño.",
    );
  });
});
