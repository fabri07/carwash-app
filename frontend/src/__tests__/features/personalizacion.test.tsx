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
  deleteLogo,
  getBrandingCatalog,
  suggestPalette,
  uploadLogo,
} from "@/services/branding.service";
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
import type { BrandCatalog, BrandPalette, BusinessSettings } from "@/types/api";

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
jest.mock("@/services/branding.service", () => ({
  getBrandingCatalog: jest.fn(),
  uploadLogo: jest.fn(),
  suggestPalette: jest.fn(),
  deleteLogo: jest.fn(),
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

const axiosConfig = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
function apiError(status: number, code: string) {
  return new AxiosError("x", "E", axiosConfig, null, {
    status,
    data: { detail: { code, message: "x" } },
    statusText: "",
    headers: {},
    config: axiosConfig,
  } as AxiosResponse);
}

const color = (hex: string) => ({ hex, oklch: `oklch(${hex})` });
const pal = (code: string, name: string, primary: string): BrandPalette => ({
  code,
  name,
  background: color("#f8fafc"),
  primary: color(primary),
  secondary: color("#f5ae39"),
  on_primary: color("#ffffff"),
});
const catalog: BrandCatalog = {
  palettes: [
    pal("default", "Agua", "#0a7db2"),
    pal("bosque", "Bosque", "#176a4e"),
    pal("competicion", "Rojo competición", "#be2323"),
  ],
  fonts: [
    { code: "default", name: "Sistema", family: null, description: "La del teléfono." },
    { code: "inter", name: "Inter", family: "Inter", description: "Neutra." },
  ],
  default_palette: "default",
  default_font: "default",
};

const owner = {
  id: "u1",
  email: "dueno@ejemplo.com",
  username: null,
  role: "OWNER" as const,
  tenant_id: "t1",
  permission_profile_id: null,
};

const base: BusinessSettings = {
  slug: "mi-lavadero",
  name: "Mi Lavadero",
  address: null,
  whatsapp_e164: null,
  instagram_url: null,
  welcome_text: null,
  cancellation_policy: null,
  deposit_enabled: false,
  deposit_bps: 2000,
  tolerance_min: 20,
  late_cancel_threshold_min: 720,
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
let current: BusinessSettings;

function png(name = "logo.png", type = "image/png", size = 5000) {
  const f = new File(["x"], name, { type });
  Object.defineProperty(f, "size", { value: size });
  return f;
}

async function openBranding() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ConfigPage />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByRole("tab", { name: "Personalización" }));
  await screen.findByRole("button", { name: "Guardar personalización" });
}

function fileInput(): HTMLInputElement {
  return document.querySelector('input[type="file"]') as HTMLInputElement;
}

beforeEach(() => {
  jest.clearAllMocks();
  current = { ...base };
  useToastStore.setState({ toasts: [] });
  useAuthStore.setState({ user: owner, permissions: [], permissionsLoaded: true });
  (getSettings as jest.Mock).mockImplementation(async () => current);
  (patchSettings as jest.Mock).mockImplementation(async (patch) => {
    current = { ...current, ...patch };
    return current;
  });
  (getBrandingCatalog as jest.Mock).mockResolvedValue(catalog);
  const empty = { items: [], total: 0, limit: 25, offset: 0, has_more: false };
  for (const crud of [vehicleSizesApi, resourcesApi, businessHoursApi, paymentMethodsApi]) {
    (crud.list as jest.Mock).mockResolvedValue(empty);
  }
});

describe("Configuración → Personalización (PR 4.5)", () => {
  it("solo el dueño ve la pestaña", () => {
    useAuthStore.setState({
      user: { ...owner, role: "STAFF", email: null, username: "ana" },
      permissions: ["CATALOGO_EDITAR"],
      permissionsLoaded: true,
    });
    const qc = new QueryClient();
    render(
      <QueryClientProvider client={qc}>
        <ConfigPage />
      </QueryClientProvider>,
    );
    expect(screen.queryByRole("tab", { name: "Personalización" })).toBeNull();
    expect(getBrandingCatalog).not.toHaveBeenCalled();
  });

  it("elige paleta y tipografía, con vista previa, y guarda", async () => {
    await openBranding();
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(catalog.palettes.length + catalog.fonts.length);
    expect(screen.getByRole("radio", { name: "Agua" })).toBeChecked();

    await userEvent.click(screen.getByRole("radio", { name: "Bosque" }));
    await userEvent.click(screen.getByRole("radio", { name: /Inter/ }));
    const preview = screen.getByTestId("branding-preview");
    expect(within(preview).getByText("Reservar turno")).toHaveStyle({
      background: "#176a4e",
    });

    await userEvent.click(screen.getByRole("button", { name: "Guardar personalización" }));
    await waitFor(() =>
      expect(patchSettings).toHaveBeenCalledWith({ palette: "bosque", font: "inter" }),
    );
    expect(useToastStore.getState().toasts.at(-1)?.message).toBe("Personalización guardada.");
  });

  it("un error del servidor al guardar queda arriba del botón", async () => {
    (patchSettings as jest.Mock).mockRejectedValueOnce(apiError(422, "VALIDATION_ERROR"));
    await openBranding();
    await userEvent.click(screen.getByRole("radio", { name: "Bosque" }));
    await userEvent.click(screen.getByRole("button", { name: "Guardar personalización" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Revisá los datos/);
  });

  it("sube el logo, sugiere la paleta y la aplica con un clic", async () => {
    (uploadLogo as jest.Mock).mockImplementation(async () => {
      current = {
        ...current,
        logo_key: "logos/t1/abc.png",
        logo_url: "https://x/logos/t1/abc.png",
      };
      return {
        logo_key: current.logo_key,
        logo_url: current.logo_url,
        suggestion: { palette: "competicion", dominant_color: "#c81e28" },
      };
    });
    await openBranding();
    const f = png();
    await userEvent.upload(fileInput(), f);
    expect(uploadLogo).toHaveBeenCalledWith(f);

    const banner = await screen.findByText(/te sugerimos la paleta Rojo competición/);
    expect(await screen.findByRole("img", { name: "Logo de Mi Lavadero" })).toHaveAttribute(
      "src",
      "https://x/logos/t1/abc.png",
    );
    expect(screen.getByText("Cambiar logo")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Aplicar paleta" }));
    await waitFor(() => expect(patchSettings).toHaveBeenCalledWith({ palette: "competicion" }));
    await waitFor(() =>
      expect(banner).toHaveTextContent(/Estás usando la paleta Rojo competición/),
    );
    expect(screen.queryByRole("button", { name: "Aplicar paleta" })).toBeNull();
    expect(screen.getByRole("radio", { name: "Rojo competición" })).toBeChecked();
  });

  it("si aplicar la sugerencia falla, avisa", async () => {
    (uploadLogo as jest.Mock).mockResolvedValue({
      logo_key: "k",
      logo_url: null,
      suggestion: { palette: "bosque", dominant_color: "#176a4e" },
    });
    (patchSettings as jest.Mock).mockRejectedValueOnce(apiError(500, "INTERNAL_ERROR"));
    await openBranding();
    await userEvent.upload(fileInput(), png());
    await userEvent.click(await screen.findByRole("button", { name: "Aplicar paleta" }));
    await waitFor(() => expect(useToastStore.getState().toasts.at(-1)?.variant).toBe("error"));
  });

  it("valida tipo y peso en el cliente antes de subir", async () => {
    await openBranding();
    await userEvent.upload(fileInput(), png("logo.png", "image/png", 3 * 1024 * 1024));
    expect(await screen.findByRole("alert")).toHaveTextContent(/2 MB/);
    expect(uploadLogo).not.toHaveBeenCalled();
  });

  it("muestra el error del servidor (imagen chica)", async () => {
    (uploadLogo as jest.Mock).mockRejectedValue(apiError(422, "IMAGE_TOO_SMALL"));
    await openBranding();
    await userEvent.upload(fileInput(), png());
    expect(await screen.findByRole("alert")).toHaveTextContent(/200×200/);
    expect(suggestPalette).not.toHaveBeenCalled();
  });

  it("sin almacenamiento: avisa y sugiere la paleta igual", async () => {
    (uploadLogo as jest.Mock).mockRejectedValue(apiError(503, "STORAGE_UNAVAILABLE"));
    (suggestPalette as jest.Mock).mockResolvedValue({
      palette: "bosque",
      dominant_color: "#176a4e",
    });
    await openBranding();
    await userEvent.upload(fileInput(), png());
    expect(await screen.findByRole("alert")).toHaveTextContent(/no se pueden guardar logos/);
    expect(await screen.findByText(/te sugerimos la paleta Bosque/)).toHaveTextContent(
      /El logo no se guardó/,
    );
  });

  it("sin almacenamiento y sin sugerencia: queda solo el aviso", async () => {
    (uploadLogo as jest.Mock).mockRejectedValue(apiError(503, "STORAGE_UNAVAILABLE"));
    (suggestPalette as jest.Mock).mockRejectedValue(new Error("x"));
    await openBranding();
    await userEvent.upload(fileInput(), png());
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(/te sugerimos/)).toBeNull();
  });

  it("quita el logo con confirmación", async () => {
    current = { ...current, logo_key: "logos/t1/abc.png", logo_url: "https://x/a.png" };
    (deleteLogo as jest.Mock).mockImplementation(async () => {
      current = { ...current, logo_key: null, logo_url: null };
    });
    await openBranding();
    await userEvent.click(screen.getByRole("button", { name: "Quitar logo" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Quitar logo" }));
    await waitFor(() => expect(deleteLogo).toHaveBeenCalled());
    expect(await screen.findByLabelText("Sin logo")).toBeInTheDocument();
    expect(screen.getByText("Subir logo")).toBeInTheDocument();
  });

  it("si quitar el logo falla, el error queda en el diálogo", async () => {
    current = { ...current, logo_key: "logos/t1/abc.png", logo_url: "https://x/a.png" };
    (deleteLogo as jest.Mock).mockRejectedValue(apiError(403, "FORBIDDEN"));
    await openBranding();
    await userEvent.click(screen.getByRole("button", { name: "Quitar logo" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Quitar logo" }));
    expect(await within(dialog).findByText(/No tenés permiso/)).toBeInTheDocument();
  });

  it("si no carga el catálogo, lo dice", async () => {
    (getBrandingCatalog as jest.Mock).mockRejectedValue(new Error("x"));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <ConfigPage />
      </QueryClientProvider>,
    );
    await userEvent.click(screen.getByRole("tab", { name: "Personalización" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/No se pudieron cargar las paletas/);
  });
});
