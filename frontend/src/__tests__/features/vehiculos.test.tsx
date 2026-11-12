import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import {
  AxiosError,
  AxiosHeaders,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";
import { useState } from "react";

import ConfigPage from "@/app/(protected)/configuracion/page";
import { getSettings, vehicleSizesApi } from "@/services/config.service";
import {
  getBodyTypeSizes,
  putBodyTypeSizes,
  vehicleColorsApi,
  vehicleMakesApi,
  vehicleModelsApi,
} from "@/services/vehicles.service";
import { BodyTypeSizesForm } from "@/features/vehiculos/BodyTypeSizesForm";
import { VehicleCatalogPanel } from "@/features/vehiculos/VehicleCatalogPanel";
import {
  EMPTY_SELECTION,
  VehiclePicker,
  type VehicleSelection,
} from "@/features/vehiculos/VehiclePicker";
import { useAuthStore } from "@/stores/authStore";
import { useToastStore } from "@/stores/toastStore";
import type {
  BodyTypeSize,
  PermissionCode,
  VehicleColor,
  VehicleMake,
  VehicleModel,
  VehicleSize,
} from "@/types/api";
import { BODY_TYPES } from "@/validation/vehicles";

jest.mock("@/services/vehicles.service", () => {
  const api = () => ({ list: jest.fn(), create: jest.fn(), update: jest.fn() });
  return {
    vehicleMakesApi: api(),
    vehicleModelsApi: api(),
    vehicleColorsApi: api(),
    getBodyTypeSizes: jest.fn(),
    putBodyTypeSizes: jest.fn(),
  };
});
jest.mock("@/services/config.service", () => {
  const crud = () => ({ list: jest.fn(), create: jest.fn(), update: jest.fn(), remove: jest.fn() });
  return {
    getSettings: jest.fn(),
    patchSettings: jest.fn(),
    vehicleSizesApi: crud(),
    resourcesApi: crud(),
    businessHoursApi: crud(),
    paymentMethodsApi: crud(),
  };
});

jest.setTimeout(30_000);

// Radix (dialog, tabs, switch) usa APIs de puntero que jsdom no trae.
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
const staff = { ...owner, id: "u2", role: "STAFF" as const };

function as(user: typeof owner | typeof staff, permissions: PermissionCode[]) {
  useAuthStore.setState({ user, permissions, permissionsLoaded: true });
}

const stamp = {
  tenant_id: "t1",
  created_at: "2026-11-10T00:00:00Z",
  updated_at: "2026-11-10T00:00:00Z",
};
const toyota: VehicleMake = {
  id: "mk1",
  name: "Toyota",
  is_custom: false,
  hidden_at: null,
  ...stamp,
};
const propia: VehicleMake = {
  id: "mk2",
  name: "Marca Sintética",
  is_custom: true,
  hidden_at: "2026-11-10T01:00:00Z",
  ...stamp,
};
function model(over: Partial<VehicleModel> = {}): VehicleModel {
  return {
    id: "md1",
    name: "Hilux",
    make_id: "mk1",
    make_name: "Toyota",
    body_type: "PICKUP",
    is_custom: false,
    hidden_at: null,
    suggested_vehicle_size_id: "s2",
    ...stamp,
    ...over,
  };
}
const etios = model({
  id: "md2",
  name: "Etios",
  body_type: "HATCHBACK",
  suggested_vehicle_size_id: null,
});
const gris: VehicleColor = {
  id: "c1",
  name: "Gris",
  is_custom: false,
  hidden_at: null,
  ...stamp,
};
const auto: VehicleSize = { id: "s1", code: "AUTO", label: "Auto", sort_order: 0, ...stamp };
const pickup: VehicleSize = { id: "s2", code: "PICKUP", label: "Pickup", sort_order: 1, ...stamp };

function page<T>(items: T[]) {
  return { items, total: items.length, limit: 25, offset: 0, has_more: false };
}

function withClient(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
  as(owner, []);
  (getSettings as jest.Mock).mockResolvedValue({});
  (vehicleSizesApi.list as jest.Mock).mockResolvedValue(page([auto, pickup]));
  (vehicleMakesApi.list as jest.Mock).mockResolvedValue(page([toyota]));
  (vehicleModelsApi.list as jest.Mock).mockResolvedValue(page([model(), etios]));
  (vehicleColorsApi.list as jest.Mock).mockResolvedValue(page([gris]));
  (getBodyTypeSizes as jest.Mock).mockResolvedValue(
    BODY_TYPES.map((b) => ({
      body_type: b.value,
      vehicle_size_id: b.value === "PICKUP" ? "s2" : null,
    })) satisfies BodyTypeSize[],
  );
});

// ── Selector reutilizable (F6) ──────────────────────────────────────────────

function PickerHarness({ onSuggest }: { onSuggest: jest.Mock }) {
  const [value, setValue] = useState<VehicleSelection>(EMPTY_SELECTION);
  return (
    <>
      <VehiclePicker value={value} onChange={setValue} onSuggestSize={onSuggest} />
      <output data-testid="seleccion">{JSON.stringify(value)}</output>
    </>
  );
}

describe("VehiclePicker", () => {
  it("marca → modelo → color, y el modelo sugiere el tamaño de su carrocería", async () => {
    const onSuggest = jest.fn();
    withClient(<PickerHarness onSuggest={onSuggest} />);
    const marca = screen.getByLabelText("Marca");
    const modelo = screen.getByLabelText("Modelo");
    expect(modelo).toBeDisabled();
    await screen.findByRole("option", { name: "Toyota" });
    // Solo filas visibles: el selector nunca pide las ocultas.
    expect(vehicleMakesApi.list).toHaveBeenCalledWith(
      expect.objectContaining({ include_hidden: false }),
    );

    await userEvent.selectOptions(marca, "mk1");
    await waitFor(() => expect(modelo).toBeEnabled());
    expect(vehicleModelsApi.list).toHaveBeenCalledWith(
      expect.objectContaining({ make_id: "mk1", include_hidden: false }),
    );
    await screen.findByRole("option", { name: "Hilux" });
    await userEvent.selectOptions(modelo, "md1");
    expect(onSuggest).toHaveBeenLastCalledWith("s2", expect.objectContaining({ id: "md1" }));
    expect(await screen.findByText(/Tamaño sugerido: Pickup/)).toBeInTheDocument();

    await userEvent.selectOptions(modelo, "md2");
    expect(onSuggest).toHaveBeenLastCalledWith(null, expect.objectContaining({ id: "md2" }));
    expect(screen.getByText(/Sin tamaño sugerido/)).toBeInTheDocument();

    await screen.findByRole("option", { name: "Gris" });
    await userEvent.selectOptions(screen.getByLabelText("Color"), "c1");
    expect(JSON.parse(screen.getByTestId("seleccion").textContent!)).toEqual({
      make_id: "mk1",
      model_id: "md2",
      color_id: "c1",
    });
  });

  it("cambiar la marca borra el modelo y la sugerencia", async () => {
    const onSuggest = jest.fn();
    withClient(<PickerHarness onSuggest={onSuggest} />);
    await screen.findByRole("option", { name: "Toyota" });
    await userEvent.selectOptions(screen.getByLabelText("Marca"), "mk1");
    await screen.findByRole("option", { name: "Hilux" });
    await userEvent.selectOptions(screen.getByLabelText("Modelo"), "md1");
    await userEvent.selectOptions(screen.getByLabelText("Marca"), "");
    expect(onSuggest).toHaveBeenLastCalledWith(null, null);
    expect(JSON.parse(screen.getByTestId("seleccion").textContent!)).toEqual(EMPTY_SELECTION);
    expect(screen.getByLabelText("Modelo")).toBeDisabled();
  });
});

// ── Pantalla de administración ──────────────────────────────────────────────

describe("Configuración → Vehículos", () => {
  it("el dueño y quien tiene CATALOGO_EDITAR ven la pestaña", async () => {
    withClient(<ConfigPage />);
    expect(await screen.findByRole("tab", { name: "Vehículos" })).toBeInTheDocument();
  });

  it("un empleado con CATALOGO_EDITAR la ve; sin el permiso, no hay configuración", async () => {
    as(staff, ["CATALOGO_EDITAR"]);
    const { unmount } = withClient(<ConfigPage />);
    expect(await screen.findByRole("tab", { name: "Vehículos" })).toBeInTheDocument();
    unmount();
    // F5: con AGENDA_VER entra a Configuración, pero solo ve "Bloqueos y aperturas".
    as(staff, ["JOBS_OPERAR"]);
    withClient(<ConfigPage />);
    expect(screen.queryByRole("tab", { name: "Vehículos" })).not.toBeInTheDocument();
    expect(screen.getByText("No tenés acceso a esta sección")).toBeInTheDocument();
  });

  it("lista marcas con su estado y crea una propia", async () => {
    (vehicleMakesApi.list as jest.Mock).mockResolvedValue(page([toyota, propia]));
    (vehicleMakesApi.create as jest.Mock).mockResolvedValue(propia);
    withClient(<VehicleCatalogPanel />);
    const tabla = await screen.findByRole("table", { name: "Marcas" });
    const fila = (await within(tabla).findByText("Marca Sintética")).closest("tr")!;
    expect(within(fila).getByText("Propia")).toBeInTheDocument();
    expect(within(fila).getByText("Oculta")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Nueva marca" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Nombre"), "  Marca Nueva ");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear marca" }));
    await waitFor(() =>
      expect(vehicleMakesApi.create).toHaveBeenCalledWith(
        { name: "Marca Nueva" },
        expect.any(String),
      ),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("un nombre repetido (409 NAME_TAKEN) queda debajo del campo", async () => {
    (vehicleMakesApi.create as jest.Mock).mockRejectedValue(apiError(409, "NAME_TAKEN"));
    withClient(<VehicleCatalogPanel />);
    await screen.findByRole("table", { name: "Marcas" });
    await userEvent.click(screen.getByRole("button", { name: "Nueva marca" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Nombre"), "Toyota");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear marca" }));
    expect(await within(dialog).findByText("Ya existe uno con ese nombre.")).toBeInTheDocument();
  });

  it("el nombre tiene que poder buscarse", async () => {
    withClient(<VehicleCatalogPanel />);
    await screen.findByRole("table", { name: "Marcas" });
    await userEvent.click(screen.getByRole("button", { name: "Nueva marca" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Nombre"), "!!!");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear marca" }));
    expect(
      await within(dialog).findByText("Tiene que tener al menos una letra o un número"),
    ).toBeInTheDocument();
    expect(vehicleMakesApi.create).not.toHaveBeenCalled();
  });

  it("ocultar y mostrar mandan `hidden`", async () => {
    (vehicleMakesApi.list as jest.Mock).mockResolvedValue(page([toyota, propia]));
    (vehicleMakesApi.update as jest.Mock).mockResolvedValue(toyota);
    withClient(<VehicleCatalogPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Ocultar marca Toyota" }));
    await waitFor(() =>
      expect(vehicleMakesApi.update).toHaveBeenCalledWith("mk1", { hidden: true }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Mostrar marca Marca Sintética" }));
    await waitFor(() =>
      expect(vehicleMakesApi.update).toHaveBeenCalledWith("mk2", { hidden: false }),
    );
    await userEvent.click(screen.getByRole("switch"));
    await waitFor(() =>
      expect(vehicleMakesApi.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ include_hidden: true }),
      ),
    );
  });

  it("los modelos se crean en la marca elegida, con su carrocería", async () => {
    (vehicleModelsApi.create as jest.Mock).mockResolvedValue(model());
    withClient(<VehicleCatalogPanel />);
    await userEvent.click(screen.getByRole("tab", { name: "Modelos" }));
    const nuevo = await screen.findByRole("button", { name: "Nuevo modelo" });
    expect(nuevo).toBeDisabled(); // sin marca elegida
    await screen.findByRole("option", { name: "Toyota" });
    await userEvent.selectOptions(screen.getByLabelText("Marca"), "mk1");
    await waitFor(() =>
      expect(vehicleModelsApi.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ make_id: "mk1" }),
      ),
    );
    await userEvent.click(screen.getByRole("button", { name: "Nuevo modelo" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Nombre"), "Hilux GR");
    await userEvent.selectOptions(within(dialog).getByLabelText("Carrocería"), "PICKUP");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear modelo" }));
    await waitFor(() =>
      expect(vehicleModelsApi.create).toHaveBeenCalledWith(
        { make_id: "mk1", name: "Hilux GR", body_type: "PICKUP" },
        expect.any(String),
      ),
    );
  });

  it("renombrar un color", async () => {
    (vehicleColorsApi.update as jest.Mock).mockResolvedValue(gris);
    withClient(<VehicleCatalogPanel />);
    await userEvent.click(screen.getByRole("tab", { name: "Colores" }));
    await userEvent.click(await screen.findByRole("button", { name: "Editar color Gris" }));
    const dialog = await screen.findByRole("dialog");
    const nombre = within(dialog).getByLabelText("Nombre");
    await userEvent.clear(nombre);
    await userEvent.type(nombre, "Gris plomo");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() =>
      expect(vehicleColorsApi.update).toHaveBeenCalledWith("c1", { name: "Gris plomo" }),
    );
  });
});

describe("BodyTypeSizesForm", () => {
  it("guarda el mapa entero; 'Sin sugerencia' va como null", async () => {
    (putBodyTypeSizes as jest.Mock).mockResolvedValue([]);
    withClient(<BodyTypeSizesForm />);
    const sedan = await screen.findByLabelText("Sedán");
    expect(screen.getByLabelText("Pickup")).toHaveValue("s2");
    await userEvent.selectOptions(sedan, "s1");
    await userEvent.selectOptions(screen.getByLabelText("Pickup"), "");
    await userEvent.click(screen.getByRole("button", { name: "Guardar carrocerías" }));
    await waitFor(() => expect(putBodyTypeSizes).toHaveBeenCalled());
    const { items } = (putBodyTypeSizes as jest.Mock).mock.calls[0][0] as {
      items: { body_type: string; vehicle_size_id: string | null }[];
    };
    expect(items).toHaveLength(BODY_TYPES.length);
    expect(items.find((i) => i.body_type === "SEDAN")?.vehicle_size_id).toBe("s1");
    expect(items.find((i) => i.body_type === "PICKUP")?.vehicle_size_id).toBeNull();
  });

  it("sin tamaños cargados avisa en vez de mostrar selectores vacíos", async () => {
    (vehicleSizesApi.list as jest.Mock).mockResolvedValue(page([]));
    withClient(<BodyTypeSizesForm />);
    expect(await screen.findByText(/Todavía no hay tamaños/)).toBeInTheDocument();
  });

  it("un error del servidor queda arriba del botón", async () => {
    (putBodyTypeSizes as jest.Mock).mockRejectedValue(apiError(422, "VALIDATION_ERROR"));
    withClient(<BodyTypeSizesForm />);
    await screen.findByLabelText("Sedán");
    await userEvent.click(screen.getByRole("button", { name: "Guardar carrocerías" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Revisá los datos ingresados.");
  });

  it("un tamaño que ya no está vivo no se reenvía (evita el 422)", async () => {
    // El mapa en caché apunta a "s2", pero ese tamaño se anuló: solo queda "s1".
    (vehicleSizesApi.list as jest.Mock).mockResolvedValue(page([auto]));
    (putBodyTypeSizes as jest.Mock).mockResolvedValue([]);
    withClient(<BodyTypeSizesForm />);
    await waitFor(() => expect(screen.getByLabelText("Pickup")).toHaveValue(""));
    await userEvent.click(screen.getByRole("button", { name: "Guardar carrocerías" }));
    await waitFor(() => expect(putBodyTypeSizes).toHaveBeenCalled());
    const { items } = (putBodyTypeSizes as jest.Mock).mock.calls[0][0] as {
      items: { body_type: string; vehicle_size_id: string | null }[];
    };
    expect(items.every((i) => i.vehicle_size_id === null)).toBe(true);
  });
});

describe("hallazgos del /code-review", () => {
  it("con keepPreviousData no se ofrecen modelos de la marca anterior", async () => {
    const ford: VehicleMake = { ...toyota, id: "mk3", name: "Ford" };
    const ranger = model({ id: "md3", name: "Ranger", make_id: "mk3", make_name: "Ford" });
    (vehicleMakesApi.list as jest.Mock).mockResolvedValue(page([toyota, ford]));
    let soltarToyota: (v: unknown) => void = () => undefined;
    (vehicleModelsApi.list as jest.Mock).mockImplementation(({ make_id }: { make_id: string }) =>
      make_id === "mk3"
        ? Promise.resolve(page([ranger]))
        : new Promise((resolve) => {
            soltarToyota = resolve;
          }),
    );
    const onSuggest = jest.fn();
    withClient(<PickerHarness onSuggest={onSuggest} />);
    await screen.findByRole("option", { name: "Ford" });
    await userEvent.selectOptions(screen.getByLabelText("Marca"), "mk3");
    await screen.findByRole("option", { name: "Ranger" });
    // Cambia a Toyota: sus modelos todavía no llegaron y la query guarda los de Ford.
    await userEvent.selectOptions(screen.getByLabelText("Marca"), "mk1");
    expect(screen.queryByRole("option", { name: "Ranger" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Modelo")).toBeDisabled();
    soltarToyota(page([model()]));
    await screen.findByRole("option", { name: "Hilux" });
    expect(screen.getByLabelText("Modelo")).toBeEnabled();
    expect(JSON.parse(screen.getByTestId("seleccion").textContent!).model_id).toBeNull();
  });

  it("filtrar por una marca oculta lista sus modelos y lo avisa", async () => {
    (vehicleMakesApi.list as jest.Mock).mockResolvedValue(page([toyota, propia]));
    withClient(<VehicleCatalogPanel />);
    await userEvent.click(screen.getByRole("tab", { name: "Modelos" }));
    await screen.findByRole("option", { name: "Marca Sintética (oculta)" });
    await userEvent.selectOptions(screen.getByLabelText("Marca"), "mk2");
    await waitFor(() =>
      expect(vehicleModelsApi.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ make_id: "mk2", include_hidden: true }),
      ),
    );
    expect(screen.getByRole("status")).toHaveTextContent("Marca Sintética está oculta");
  });

  it("el replay de una marca habla en femenino", async () => {
    (vehicleMakesApi.create as jest.Mock).mockRejectedValue(apiError(409, "DUPLICATE_IDEMPOTENT"));
    withClient(<VehicleCatalogPanel />);
    await screen.findByRole("table", { name: "Marcas" });
    await userEvent.click(screen.getByRole("button", { name: "Nueva marca" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Nombre"), "Repetida");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear marca" }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.at(-1)?.message).toBe(
        "Esa marca ya se había creado. Revisá en la lista cómo quedó.",
      ),
    );
  });
});
