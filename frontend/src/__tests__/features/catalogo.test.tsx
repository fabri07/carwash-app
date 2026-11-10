import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import {
  AxiosError,
  AxiosHeaders,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import CatalogPage from "@/app/(protected)/catalogo/page";
import {
  applyTemplate,
  getTemplate,
  serviceCategoriesApi,
  servicesApi,
} from "@/services/catalog.service";
import { getSettings, vehicleSizesApi } from "@/services/config.service";
import { useAuthStore } from "@/stores/authStore";
import { useToastStore } from "@/stores/toastStore";
import type { PermissionCode, Service, ServiceCategory, Template, VehicleSize } from "@/types/api";

jest.mock("@/services/catalog.service", () => {
  const crud = () => ({ list: jest.fn(), create: jest.fn(), update: jest.fn(), remove: jest.fn() });
  return {
    serviceCategoriesApi: crud(),
    servicesApi: { ...crud(), replacePrices: jest.fn() },
    getTemplate: jest.fn(),
    applyTemplate: jest.fn(),
  };
});
jest.mock("@/services/config.service", () => ({
  getSettings: jest.fn(),
  vehicleSizesApi: { list: jest.fn(), create: jest.fn(), update: jest.fn(), remove: jest.fn() },
}));

jest.setTimeout(30_000);

// Radix (dialog, tabs, checkbox) usa APIs de puntero que jsdom no trae.
beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

const config = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
function apiError(status: number, code: string, message = "x") {
  const response = {
    status,
    data: { detail: { code, message } },
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
const staff = { ...owner, id: "u2", role: "STAFF" as const };

function as(user: typeof owner | typeof staff, permissions: PermissionCode[], loaded = true) {
  useAuthStore.setState({ user, permissions, permissionsLoaded: loaded });
}

const stamp = {
  tenant_id: "t1",
  created_at: "2026-11-05T00:00:00Z",
  updated_at: "2026-11-05T00:00:00Z",
};
const auto: VehicleSize = { id: "s1", code: "AUTO", label: "Auto", sort_order: 0, ...stamp };
const camioneta: VehicleSize = {
  id: "s2",
  code: "PICKUP",
  label: "Camioneta",
  sort_order: 1,
  ...stamp,
};
const lavado: ServiceCategory = { id: "c1", name: "Lavado", sort_order: 1, ...stamp };

function service(over: Partial<Service> = {}): Service {
  return {
    id: "sv1",
    name: "Lavado completo",
    pricing_mode: "PRECIO_FIJO",
    notes: "Incluye aspirado",
    category_id: "c1",
    glossary_code: null,
    deposit_bps: null,
    prices: [
      { id: "p1", vehicle_size_id: "s1", price_cents: 2_000_000, duration_min: 60 },
      { id: "p2", vehicle_size_id: "s2", price_cents: 3_000_000, duration_min: 90 },
    ],
    missing_price_size_ids: [],
    ...stamp,
    ...over,
  };
}

function page<T>(items: T[]) {
  return { items, total: items.length, limit: 25, offset: 0, has_more: false };
}

const template: Template = {
  kind: "detailing",
  categories: [
    {
      name: "Lavado",
      sort_order: 1,
      items: [
        {
          glossary_code: "lavado_simple",
          name: "Lavado simple",
          type: "servicio",
          english: "Wash",
          synonyms: [],
          already_added: false,
        },
        {
          glossary_code: "lavado_motor",
          name: "Lavado de motor",
          type: "adicional",
          english: "Engine wash",
          synonyms: [],
          already_added: true,
        },
      ],
    },
    {
      name: "Protección",
      sort_order: 4,
      items: [
        {
          glossary_code: "ceramico",
          name: "Tratamiento cerámico",
          type: "servicio",
          english: "Ceramic coating",
          synonyms: [],
          already_added: false,
        },
      ],
    },
  ],
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CatalogPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
  as(owner, []);
  (getSettings as jest.Mock).mockResolvedValue({});
  (servicesApi.list as jest.Mock).mockResolvedValue(page([service()]));
  (serviceCategoriesApi.list as jest.Mock).mockResolvedValue(page([lavado]));
  (vehicleSizesApi.list as jest.Mock).mockResolvedValue(page([auto, camioneta]));
  (getTemplate as jest.Mock).mockResolvedValue(template);
});

async function openTab(name: string) {
  await userEvent.click(screen.getByRole("tab", { name }));
}

/** Escribe precios (formato argentino) y minutos en el orden de los tamaños: Auto, Camioneta. */
async function fillPrices(scope: HTMLElement, prices: string[], minutes: string[]) {
  const sizes = ["Auto", "Camioneta"];
  for (const [i, size] of sizes.entries()) {
    if (prices[i] !== undefined) {
      await userEvent.type(within(scope).getByLabelText(`Precio (${size})`), prices[i]!);
    }
    if (minutes[i] !== undefined) {
      await userEvent.type(
        within(scope).getByLabelText(`Duración en minutos (${size})`),
        minutes[i]!,
      );
    }
  }
}

describe("/catalogo — acceso", () => {
  it("el dueño ve las tres pestañas y todos los botones", async () => {
    renderPage();
    expect(await screen.findByText("Lavado completo")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Servicios" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Categorías" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Plantillas" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /nuevo servicio/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Precios de Lavado completo" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Anular servicio Lavado completo" }),
    ).toBeInTheDocument();
  });

  it("solo CATALOGO_EDITAR: edita nombre y notas, pero sin precios, alta ni baja ni plantillas", async () => {
    as(staff, ["CATALOGO_EDITAR"]);
    renderPage();
    expect(await screen.findByText("Lavado completo")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Editar servicio Lavado completo" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /nuevo servicio/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Precios de/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Anular servicio/ })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Plantillas" })).toBeNull();
  });

  it("con CATALOGO_EDITAR y PRECIOS_EDITAR: todo menos las plantillas", async () => {
    as(staff, ["CATALOGO_EDITAR", "PRECIOS_EDITAR"]);
    renderPage();
    expect(await screen.findByText("Lavado completo")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /nuevo servicio/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Precios de Lavado completo" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Anular servicio Lavado completo" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Plantillas" })).toBeNull();
  });

  it("solo PRECIOS_EDITAR: ve la lista y 'Precios', sin editar, alta, baja ni categorías", async () => {
    as(staff, ["PRECIOS_EDITAR"]);
    renderPage();
    expect(await screen.findByText("Lavado completo")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Precios de Lavado completo" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Editar servicio/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /nuevo servicio/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Anular servicio/ })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Categorías" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Plantillas" })).toBeNull();
  });

  it("sin permisos: aviso de acceso denegado y ninguna consulta", () => {
    as(staff, ["AGENDA_VER"]);
    renderPage();
    expect(screen.getByText("No tenés acceso a esta sección")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Servicios" })).toBeNull();
    expect(servicesApi.list).not.toHaveBeenCalled();
  });

  it("tras recargar, hasta que llegan los permisos muestra 'Cargando' y no un 'sin acceso' falso", () => {
    as(staff, [], false);
    renderPage();
    expect(screen.getByText("Cargando…")).toBeInTheDocument();
    expect(screen.queryByText("No tenés acceso a esta sección")).toBeNull();
    expect(servicesApi.list).not.toHaveBeenCalled();
  });
});

describe("Servicios — lista", () => {
  it("resume los precios, marca 'Faltan precios' y muestra la categoría", async () => {
    (servicesApi.list as jest.Mock).mockResolvedValue(
      page([
        service(),
        service({
          id: "sv2",
          name: "Pulido",
          category_id: null,
          pricing_mode: "A_COTIZAR",
          missing_price_size_ids: ["s2"],
          prices: [{ id: "p3", vehicle_size_id: "s1", price_cents: 5_000_000, duration_min: 300 }],
        }),
      ]),
    );
    renderPage();
    const row1 = (await screen.findByText("Lavado completo")).closest("tr")!;
    expect(within(row1).getByText("$ 20.000,00 – $ 30.000,00")).toBeInTheDocument();
    expect(within(row1).getByText("1 h – 1 h 30 min")).toBeInTheDocument();
    expect(within(row1).getByText("Lavado")).toBeInTheDocument();
    expect(within(row1).queryByText("Faltan precios")).toBeNull();
    const row2 = screen.getByText("Pulido").closest("tr")!;
    expect(within(row2).getByText("Faltan precios")).toBeInTheDocument();
    expect(within(row2).getByText("Desde $ 50.000,00")).toBeInTheDocument();
    expect(within(row2).getByText("Sin categoría")).toBeInTheDocument();
    expect(within(row2).getByText("5 h")).toBeInTheDocument();
  });

  it("busca con debounce: una sola consulta con el texto final", async () => {
    renderPage();
    await screen.findByText("Lavado completo");
    (servicesApi.list as jest.Mock).mockClear();
    await userEvent.type(screen.getByLabelText("Buscar servicios"), "cera");
    await waitFor(() =>
      expect(servicesApi.list).toHaveBeenCalledWith(expect.objectContaining({ q: "cera" })),
    );
    const qs = (servicesApi.list as jest.Mock).mock.calls.map((c) => c[0].q);
    expect(qs.filter((q) => q !== "" && q !== "cera")).toEqual([]);
  });

  it("al cambiar la búsqueda vuelve a la primera página, sin pedir antes el q nuevo con el offset viejo", async () => {
    (servicesApi.list as jest.Mock).mockResolvedValue({
      items: [service()],
      total: 60,
      limit: 25,
      offset: 0,
      has_more: true,
    });
    renderPage();
    await screen.findByText("Lavado completo");
    await userEvent.click(screen.getByRole("button", { name: "Página siguiente" }));
    await waitFor(() =>
      expect(servicesApi.list).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 25 })),
    );
    (servicesApi.list as jest.Mock).mockClear();
    await userEvent.type(screen.getByLabelText("Buscar servicios"), "cera");
    await waitFor(() =>
      expect(servicesApi.list).toHaveBeenCalledWith(
        expect.objectContaining({ q: "cera", offset: 0 }),
      ),
    );
    const calls = (servicesApi.list as jest.Mock).mock.calls.map((c) => c[0]);
    expect(calls.filter((c) => c.offset === 25)).toEqual([]);
    expect(calls.filter((c) => c.q === "cera" && c.offset !== 0)).toEqual([]);
  });

  it("filtra por categoría y avisa cuando nada coincide", async () => {
    renderPage();
    await screen.findByText("Lavado completo");
    await waitFor(() => expect(screen.getByRole("option", { name: "Lavado" })).toBeInTheDocument());
    (servicesApi.list as jest.Mock).mockResolvedValue(page([]));
    await userEvent.selectOptions(screen.getByLabelText("Filtrar por categoría"), "c1");
    await waitFor(() =>
      expect(servicesApi.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ category_id: "c1", offset: 0 }),
      ),
    );
    expect(
      await screen.findByText("Ningún servicio coincide con la búsqueda."),
    ).toBeInTheDocument();
  });

  it("sin servicios sugiere crear uno o usar una plantilla", async () => {
    (servicesApi.list as jest.Mock).mockResolvedValue(page([]));
    renderPage();
    expect(await screen.findByText(/todavía no cargaste servicios/i)).toBeInTheDocument();
  });
});

describe("Servicios — alta, edición y baja", () => {
  it("crea con precio por tamaño: payload en centavos, minutos numéricos y Idempotency-Key", async () => {
    (servicesApi.create as jest.Mock).mockResolvedValueOnce(service());
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /nuevo servicio/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo servicio" });
    await userEvent.type(await within(dialog).findByLabelText("Nombre"), "Lavado premium");
    await userEvent.selectOptions(within(dialog).getByLabelText("Categoría"), "c1");
    await userEvent.type(within(dialog).getByLabelText("Notas (opcional)"), "Con cera");
    await fillPrices(dialog, ["20.000", "30.500,50"], ["60", "90"]);
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear servicio" }));
    await waitFor(() => expect(servicesApi.create).toHaveBeenCalledTimes(1));
    const [payload, key] = (servicesApi.create as jest.Mock).mock.calls[0]!;
    expect(payload).toEqual({
      name: "Lavado premium",
      pricing_mode: "PRECIO_FIJO",
      notes: "Con cera",
      category_id: "c1",
      deposit_bps: null,
      prices: [
        { vehicle_size_id: "s1", price_cents: 2_000_000, duration_min: 60 },
        { vehicle_size_id: "s2", price_cents: 3_050_050, duration_min: 90 },
      ],
    });
    expect(typeof key).toBe("string");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(servicesApi.list).toHaveBeenCalledTimes(2);
  });

  it("modalidad a cotizar y 'completar los vacíos con el primero'", async () => {
    (servicesApi.create as jest.Mock).mockResolvedValueOnce(service());
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /nuevo servicio/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo servicio" });
    await userEvent.type(await within(dialog).findByLabelText("Nombre"), "Pulido");
    await userEvent.selectOptions(within(dialog).getByLabelText("Modalidad"), "A_COTIZAR");
    await fillPrices(dialog, ["50000"], ["300"]);
    expect(within(dialog).getByText("5 h")).toBeInTheDocument();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Completar los vacíos con el primero" }),
    );
    expect(within(dialog).getByLabelText("Precio (Camioneta)")).toHaveValue("50.000,00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear servicio" }));
    await waitFor(() => expect(servicesApi.create).toHaveBeenCalledTimes(1));
    const [payload] = (servicesApi.create as jest.Mock).mock.calls[0]!;
    expect(payload).toMatchObject({
      pricing_mode: "A_COTIZAR",
      notes: null,
      category_id: null,
      prices: [
        { vehicle_size_id: "s1", price_cents: 5_000_000, duration_min: 300 },
        { vehicle_size_id: "s2", price_cents: 5_000_000, duration_min: 300 },
      ],
    });
  });

  it("completar vacíos no pisa lo ya cargado y limpia los errores de lo que completa", async () => {
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /nuevo servicio/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo servicio" });
    await userEvent.type(await within(dialog).findByLabelText("Nombre"), "Lavado");
    await fillPrices(dialog, ["50000", "70000"], ["60"]);
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear servicio" }));
    expect(
      await within(dialog).findByText("Poné los minutos, en número entero"),
    ).toBeInTheDocument();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Completar los vacíos con el primero" }),
    );
    expect(within(dialog).getByLabelText("Precio (Camioneta)")).toHaveValue("70.000,00");
    await waitFor(() =>
      expect(within(dialog).getByLabelText("Duración en minutos (Camioneta)")).toHaveValue("60"),
    );
    await waitFor(() =>
      expect(within(dialog).queryByText("Poné los minutos, en número entero")).toBeNull(),
    );
  });

  it("un precio faltante o inválido bloquea el alta y lo dice debajo del campo", async () => {
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /nuevo servicio/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo servicio" });
    await userEvent.type(await within(dialog).findByLabelText("Nombre"), "Lavado");
    await fillPrices(dialog, ["20.000"], ["60", "0"]); // sin precio de Camioneta, duración 0
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear servicio" }));
    expect(await within(dialog).findByText(/^Poné un precio/)).toBeInTheDocument();
    expect(within(dialog).getByText("Tiene que durar al menos 1 minuto")).toBeInTheDocument();
    expect(servicesApi.create).not.toHaveBeenCalled();
  });

  it("rechaza un nombre que no se puede buscar y un precio sobre el tope", async () => {
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /nuevo servicio/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo servicio" });
    await userEvent.type(await within(dialog).findByLabelText("Nombre"), "洗車");
    await fillPrices(dialog, ["100.000.001", "1"], ["60", "60"]);
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear servicio" }));
    expect(await within(dialog).findByText(/no se puede buscar/i)).toBeInTheDocument();
    expect(within(dialog).getByText("Máximo $ 100.000.000")).toBeInTheDocument();
    expect(servicesApi.create).not.toHaveBeenCalled();
  });

  it("NAME_TAKEN va debajo del nombre", async () => {
    (servicesApi.create as jest.Mock).mockRejectedValueOnce(apiError(409, "NAME_TAKEN"));
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /nuevo servicio/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo servicio" });
    await userEvent.type(await within(dialog).findByLabelText("Nombre"), "Lavado completo");
    await fillPrices(dialog, ["1000", "2000"], ["30", "40"]);
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear servicio" }));
    expect(await within(dialog).findByText("Ya existe uno con ese nombre.")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("un 422 de cobertura de precios se explica arriba del botón", async () => {
    (servicesApi.create as jest.Mock).mockRejectedValueOnce(
      apiError(422, "VALIDATION_ERROR", "a price is required for every vehicle size; missing: X"),
    );
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /nuevo servicio/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo servicio" });
    await userEvent.type(await within(dialog).findByLabelText("Nombre"), "Otro");
    await fillPrices(dialog, ["1000", "2000"], ["30", "40"]);
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear servicio" }));
    expect(
      await within(dialog).findByText(
        /Los precios no se pudieron guardar.*Detalle del servidor: a price/s,
      ),
    ).toBeInTheDocument();
  });

  it("un replay (DUPLICATE_IDEMPOTENT) se avisa y cierra el diálogo", async () => {
    (servicesApi.create as jest.Mock).mockRejectedValueOnce(apiError(409, "DUPLICATE_IDEMPOTENT"));
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /nuevo servicio/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo servicio" });
    await userEvent.type(await within(dialog).findByLabelText("Nombre"), "Otro");
    await fillPrices(dialog, ["1000", "2000"], ["30", "40"]);
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear servicio" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(useToastStore.getState().toasts.map((t) => t.message)).toEqual([
      "Ese servicio ya se había creado. Revisá en la lista cómo quedó.",
    ]);
  });

  it("sin tamaños de vehículo no deja crear y manda a cargarlos", async () => {
    (vehicleSizesApi.list as jest.Mock).mockResolvedValue(page([]));
    renderPage();
    await screen.findByText("Lavado completo");
    await userEvent.click(screen.getByRole("button", { name: /nuevo servicio/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo servicio" });
    expect(await within(dialog).findByText(/cargá al menos un tamaño/i)).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Crear servicio" })).toBeNull();
  });

  it("edita nombre, categoría y notas; vaciar las notas manda null", async () => {
    (servicesApi.update as jest.Mock).mockResolvedValueOnce(service());
    renderPage();
    await userEvent.click(
      await screen.findByRole("button", { name: "Editar servicio Lavado completo" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Editar servicio Lavado completo" });
    const notes = await within(dialog).findByLabelText("Notas (opcional)");
    expect(notes).toHaveValue("Incluye aspirado");
    await userEvent.clear(notes);
    await userEvent.selectOptions(within(dialog).getByLabelText("Categoría"), "");
    await userEvent.clear(within(dialog).getByLabelText("Nombre"));
    await userEvent.type(within(dialog).getByLabelText("Nombre"), "Lavado total");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() =>
      expect(servicesApi.update).toHaveBeenCalledWith("sv1", {
        name: "Lavado total",
        notes: null,
        category_id: null,
      }),
    );
  });

  it("cambia los precios de todos los tamaños de una vez", async () => {
    (servicesApi.replacePrices as jest.Mock).mockResolvedValueOnce(service());
    renderPage();
    await userEvent.click(
      await screen.findByRole("button", { name: "Precios de Lavado completo" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Precios de Lavado completo" });
    const autoPrice = await within(dialog).findByLabelText("Precio (Auto)");
    expect(autoPrice).toHaveValue("20.000,00");
    await userEvent.clear(autoPrice);
    await userEvent.type(autoPrice, "25000");
    await userEvent.selectOptions(within(dialog).getByLabelText("Modalidad"), "A_COTIZAR");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar precios" }));
    await waitFor(() => expect(servicesApi.replacePrices).toHaveBeenCalledTimes(1));
    // La seña no cambió: no se manda, así no pisa la que otro haya fijado mientras tanto.
    expect(servicesApi.replacePrices).toHaveBeenCalledWith("sv1", {
      pricing_mode: "A_COTIZAR",
      prices: [
        { vehicle_size_id: "s1", price_cents: 2_500_000, duration_min: 60 },
        { vehicle_size_id: "s2", price_cents: 3_000_000, duration_min: 90 },
      ],
    });
  });

  it("seña propia: el alta manda los puntos básicos", async () => {
    (servicesApi.create as jest.Mock).mockResolvedValueOnce(service({ deposit_bps: 1250 }));
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /nuevo servicio/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo servicio" });
    await userEvent.type(await within(dialog).findByLabelText("Nombre"), "Lavado camión");
    await userEvent.type(within(dialog).getByLabelText("Seña propia (%) (opcional)"), "12,5");
    await fillPrices(dialog, ["20.000", "30.000"], ["60", "90"]);
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear servicio" }));
    await waitFor(() => expect(servicesApi.create).toHaveBeenCalledTimes(1));
    expect((servicesApi.create as jest.Mock).mock.calls[0]![0]).toMatchObject({
      deposit_bps: 1250,
    });
  });

  it("seña propia: se ve en la tabla y vaciarla vuelve a la general", async () => {
    (servicesApi.list as jest.Mock).mockResolvedValue(page([service({ deposit_bps: 3000 })]));
    (servicesApi.replacePrices as jest.Mock).mockResolvedValueOnce(service());
    renderPage();
    expect(await screen.findByText("30 %")).toBeInTheDocument();
    await userEvent.click(
      await screen.findByRole("button", { name: "Precios de Lavado completo" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Precios de Lavado completo" });
    const propia = within(dialog).getByLabelText("Seña propia (%) (opcional)");
    expect(propia).toHaveValue("30");
    await userEvent.clear(propia);
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar precios" }));
    await waitFor(() => expect(servicesApi.replacePrices).toHaveBeenCalledTimes(1));
    expect((servicesApi.replacePrices as jest.Mock).mock.calls[0]![1]).toMatchObject({
      deposit_bps: null,
    });
  });

  it("con la seña apagada en el negocio, la tabla lo dice", async () => {
    (getSettings as jest.Mock).mockResolvedValue({ deposit_enabled: false });
    (servicesApi.list as jest.Mock).mockResolvedValue(page([service({ deposit_bps: 3000 })]));
    renderPage();
    expect(await screen.findByText("30 % (seña apagada)")).toBeInTheDocument();
  });

  it("seña propia inválida no se manda", async () => {
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /nuevo servicio/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo servicio" });
    await userEvent.type(await within(dialog).findByLabelText("Nombre"), "Lavado camión");
    await userEvent.type(within(dialog).getByLabelText("Seña propia (%) (opcional)"), "150");
    await fillPrices(dialog, ["20.000", "30.000"], ["60", "90"]);
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear servicio" }));
    expect(await within(dialog).findByText("Entre 0,01 % y 100 %")).toBeInTheDocument();
    expect(servicesApi.create).not.toHaveBeenCalled();
  });

  it("un tamaño sin precio arranca vacío y hay que completarlo", async () => {
    (servicesApi.list as jest.Mock).mockResolvedValue(
      page([
        service({
          prices: [{ id: "p1", vehicle_size_id: "s1", price_cents: 2_000_000, duration_min: 60 }],
          missing_price_size_ids: ["s2"],
        }),
      ]),
    );
    renderPage();
    await userEvent.click(
      await screen.findByRole("button", { name: "Precios de Lavado completo" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Precios de Lavado completo" });
    expect(await within(dialog).findByLabelText("Precio (Camioneta)")).toHaveValue("");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar precios" }));
    expect(await within(dialog).findByText(/^Poné un precio/)).toBeInTheDocument();
    expect(servicesApi.replacePrices).not.toHaveBeenCalled();
  });

  it("anular un servicio en uso deja el motivo en el diálogo (409 IN_USE)", async () => {
    (servicesApi.remove as jest.Mock).mockRejectedValueOnce(apiError(409, "IN_USE"));
    renderPage();
    await userEvent.click(
      await screen.findByRole("button", { name: "Anular servicio Lavado completo" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Anular" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      /hay turnos, cotizaciones o trabajos sin cerrar/i,
    );
    expect(servicesApi.remove).toHaveBeenCalledWith("sv1");
  });

  it("anular un servicio libre lo saca y avisa", async () => {
    (servicesApi.remove as jest.Mock).mockResolvedValueOnce(undefined);
    renderPage();
    await userEvent.click(
      await screen.findByRole("button", { name: "Anular servicio Lavado completo" }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Anular" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(useToastStore.getState().toasts.map((t) => t.message)).toContain("Servicio anulado.");
  });

  it("si falla la lista, muestra el error en la tabla", async () => {
    (servicesApi.list as jest.Mock).mockRejectedValue(apiError(403, "FORBIDDEN"));
    renderPage();
    expect(await screen.findByText("No tenés permiso para hacer esto.")).toBeInTheDocument();
  });
});

describe("Categorías", () => {
  it("crea una categoría con su orden", async () => {
    (serviceCategoriesApi.create as jest.Mock).mockResolvedValueOnce(lavado);
    renderPage();
    await openTab("Categorías");
    await userEvent.click(await screen.findByRole("button", { name: /nueva categoría/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nueva categoría" });
    await userEvent.type(within(dialog).getByLabelText("Nombre"), "Interior");
    await userEvent.clear(within(dialog).getByLabelText("Orden en las listas"));
    await userEvent.type(within(dialog).getByLabelText("Orden en las listas"), "2");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear categoría" }));
    await waitFor(() => expect(serviceCategoriesApi.create).toHaveBeenCalledTimes(1));
    expect((serviceCategoriesApi.create as jest.Mock).mock.calls[0]![0]).toEqual({
      name: "Interior",
      sort_order: 2,
    });
  });

  it("anular una categoría con servicios muestra el 409 IN_USE", async () => {
    (serviceCategoriesApi.remove as jest.Mock).mockRejectedValueOnce(apiError(409, "IN_USE"));
    renderPage();
    await openTab("Categorías");
    await userEvent.click(await screen.findByRole("button", { name: "Quitar categoría Lavado" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Quitar" }));
    expect(await within(dialog).findByText(/una categoría con servicios/i)).toBeInTheDocument();
  });
});

describe("Plantillas", () => {
  async function openDetailing() {
    renderPage();
    await openTab("Plantillas");
    await userEvent.click(await screen.findByRole("button", { name: /Detailing/ }));
    await screen.findByText("Tratamiento cerámico");
  }

  it("lista los términos por categoría y marca los que ya están", async () => {
    await openDetailing();
    expect(getTemplate).toHaveBeenCalledWith("detailing");
    expect(screen.getByText("Protección")).toBeInTheDocument();
    expect(screen.getByText("Ya lo tenés")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Lavado de motor/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cargar precios (0)" })).toBeDisabled();
  });

  it("no avanza sin elegir, y sin precio completo no aplica nada (A8)", async () => {
    await openDetailing();
    await userEvent.click(screen.getByRole("checkbox", { name: /Lavado simple/ }));
    await userEvent.click(screen.getByRole("button", { name: "Cargar precios (1)" }));
    const section = await screen.findByRole("region", { name: "Precios de Lavado simple" });
    await fillPrices(section, ["10.000"], ["45"]); // falta Camioneta
    await userEvent.click(screen.getByRole("button", { name: "Crear 1 servicio" }));
    expect(await within(section).findByText(/^Poné un precio/)).toBeInTheDocument();
    expect(applyTemplate).not.toHaveBeenCalled();
  });

  it("aplica con precio por tamaño y muestra creados y salteados con su motivo", async () => {
    (applyTemplate as jest.Mock).mockResolvedValueOnce({
      created: [service({ id: "n1", name: "Lavado simple" })],
      skipped: [{ glossary_code: "ceramico", service_id: "x", reason: "NAME" }],
      categories_created: [{ id: "c9", name: "Lavado", sort_order: 1, ...stamp }],
    });
    await openDetailing();
    await userEvent.click(screen.getByRole("checkbox", { name: /Lavado simple/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Tratamiento cerámico/ }));
    await userEvent.click(screen.getByRole("button", { name: "Cargar precios (2)" }));
    const first = await screen.findByRole("region", { name: "Precios de Lavado simple" });
    const second = screen.getByRole("region", { name: "Precios de Tratamiento cerámico" });
    await fillPrices(first, ["10.000", "12.000"], ["45", "60"]);
    await fillPrices(second, ["200.000", "250.000"], ["480", "600"]);
    await userEvent.selectOptions(within(second).getByLabelText("Modalidad"), "A_COTIZAR");
    await userEvent.click(screen.getByRole("button", { name: "Crear 2 servicios" }));
    await waitFor(() => expect(applyTemplate).toHaveBeenCalledTimes(1));
    expect(applyTemplate).toHaveBeenCalledWith({
      kind: "detailing",
      items: [
        {
          glossary_code: "lavado_simple",
          pricing_mode: "PRECIO_FIJO",
          prices: [
            { vehicle_size_id: "s1", price_cents: 1_000_000, duration_min: 45 },
            { vehicle_size_id: "s2", price_cents: 1_200_000, duration_min: 60 },
          ],
        },
        {
          glossary_code: "ceramico",
          pricing_mode: "A_COTIZAR",
          prices: [
            { vehicle_size_id: "s1", price_cents: 20_000_000, duration_min: 480 },
            { vehicle_size_id: "s2", price_cents: 25_000_000, duration_min: 600 },
          ],
        },
      ],
    });
    expect(await screen.findByText("Plantilla aplicada")).toBeInTheDocument();
    expect(screen.getByText(/Categorías nuevas: Lavado/)).toBeInTheDocument();
    expect(
      screen.getByText("Tratamiento cerámico: ya tenías un servicio propio con ese nombre"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Elegir más servicios" }));
    expect(await screen.findByText("Partir de una plantilla")).toBeInTheDocument();
  });

  it("un error del servidor al aplicar queda arriba del botón y conserva lo cargado", async () => {
    (applyTemplate as jest.Mock).mockRejectedValueOnce(apiError(422, "VALIDATION_ERROR"));
    await openDetailing();
    await userEvent.click(screen.getByRole("checkbox", { name: /Lavado simple/ }));
    await userEvent.click(screen.getByRole("button", { name: "Cargar precios (1)" }));
    const section = await screen.findByRole("region", { name: "Precios de Lavado simple" });
    await fillPrices(section, ["10.000", "12.000"], ["45", "60"]);
    await userEvent.click(screen.getByRole("button", { name: "Crear 1 servicio" }));
    expect(await screen.findByText(/Los precios no se pudieron guardar/)).toBeInTheDocument();
    expect(within(section).getByLabelText("Precio (Auto)")).toHaveValue("10.000,00");
  });

  it("'elegir todos' de una categoría salta los que ya están, y volver conserva la selección", async () => {
    await openDetailing();
    await userEvent.click(
      screen.getByRole("button", { name: "Elegir o quitar todos en Protección" }),
    );
    expect(screen.getByRole("button", { name: "Cargar precios (1)" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Cargar precios (1)" }));
    await userEvent.click(await screen.findByRole("button", { name: "Volver a la selección" }));
    expect(await screen.findByRole("checkbox", { name: /Tratamiento cerámico/ })).toBeChecked();
    // Quitar todos en la categoría.
    await userEvent.click(
      screen.getByRole("button", { name: "Elegir o quitar todos en Protección" }),
    );
    expect(screen.getByRole("button", { name: "Cargar precios (0)" })).toBeDisabled();
  });

  it("cambiar de plantilla limpia la selección; sin tamaños avisa y no deja avanzar", async () => {
    (vehicleSizesApi.list as jest.Mock).mockResolvedValue(page([]));
    await openDetailing();
    expect(screen.getByText(/cargá al menos un tamaño de vehículo/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: /Lavado simple/ }));
    expect(screen.getByRole("button", { name: "Cargar precios (1)" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /Lavadero/ }));
    expect(getTemplate).toHaveBeenLastCalledWith("lavadero");
    expect(await screen.findByRole("button", { name: "Cargar precios (0)" })).toBeInTheDocument();
  });

  it("si la plantilla no carga, lo dice", async () => {
    (getTemplate as jest.Mock).mockRejectedValue(apiError(403, "FORBIDDEN"));
    renderPage();
    await openTab("Plantillas");
    await userEvent.click(await screen.findByRole("button", { name: /Lavadero/ }));
    expect(await screen.findByText("No tenés permiso para hacer esto.")).toBeInTheDocument();
  });

  it("un refetch de la plantilla no cambia lo que se envía (términos congelados)", async () => {
    (applyTemplate as jest.Mock).mockResolvedValueOnce({
      created: [],
      skipped: [],
      categories_created: [],
    });
    renderPage();
    await openTab("Plantillas");
    await userEvent.click(await screen.findByRole("button", { name: /Detailing/ }));
    await screen.findByText("Tratamiento cerámico");
    await userEvent.click(screen.getByRole("checkbox", { name: /Lavado simple/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Tratamiento cerámico/ }));
    await userEvent.click(screen.getByRole("button", { name: "Cargar precios (2)" }));
    const first = await screen.findByRole("region", { name: "Precios de Lavado simple" });
    const second = screen.getByRole("region", { name: "Precios de Tratamiento cerámico" });
    await fillPrices(first, ["10.000", "12.000"], ["45", "60"]);
    // Otra pestaña ya agregó "Lavado simple": al refrescar vuelve con already_added.
    const refreshed: Template = JSON.parse(JSON.stringify(template));
    refreshed.categories[0]!.items[0]!.already_added = true;
    (getTemplate as jest.Mock).mockResolvedValue(refreshed);
    const before = (getTemplate as jest.Mock).mock.calls.length;
    window.dispatchEvent(new Event("visibilitychange"));
    await waitFor(() =>
      expect((getTemplate as jest.Mock).mock.calls.length).toBeGreaterThan(before),
    );
    await fillPrices(second, ["200.000", "250.000"], ["480", "600"]);
    expect(screen.getByRole("region", { name: "Precios de Lavado simple" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Crear 2 servicios" }));
    await waitFor(() => expect(applyTemplate).toHaveBeenCalledTimes(1));
    const items = (applyTemplate as jest.Mock).mock.calls[0]![0].items;
    expect(items.map((i: { glossary_code: string }) => i.glossary_code)).toEqual([
      "lavado_simple",
      "ceramico",
    ]);
    expect(items[0].prices[0].price_cents).toBe(1_000_000);
    expect(items[1].prices[0].price_cents).toBe(20_000_000);
  });

  it("'Volver a la selección' conserva los precios cargados de los términos que siguen elegidos", async () => {
    await openDetailing();
    await userEvent.click(screen.getByRole("checkbox", { name: /Lavado simple/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Tratamiento cerámico/ }));
    await userEvent.click(screen.getByRole("button", { name: "Cargar precios (2)" }));
    const first = await screen.findByRole("region", { name: "Precios de Lavado simple" });
    const second = screen.getByRole("region", { name: "Precios de Tratamiento cerámico" });
    await fillPrices(first, ["10.000", "12.000"], ["45", "60"]);
    await fillPrices(second, ["200.000"], ["480"]);
    await userEvent.click(screen.getByRole("button", { name: "Volver a la selección" }));
    // Se saca el cerámico y se vuelve.
    await userEvent.click(await screen.findByRole("checkbox", { name: /Tratamiento cerámico/ }));
    await userEvent.click(screen.getByRole("button", { name: "Cargar precios (1)" }));
    const again = await screen.findByRole("region", { name: "Precios de Lavado simple" });
    expect(within(again).getByLabelText("Precio (Auto)")).toHaveValue("10.000,00");
    expect(within(again).getByLabelText("Precio (Camioneta)")).toHaveValue("12.000,00");
    expect(within(again).getByLabelText("Duración en minutos (Camioneta)")).toHaveValue("60");
    expect(screen.queryByRole("region", { name: "Precios de Tratamiento cerámico" })).toBeNull();
  });
});
