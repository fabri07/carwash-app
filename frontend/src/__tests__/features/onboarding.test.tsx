import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  AxiosError,
  AxiosHeaders,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import ProtectedLayout from "@/app/(protected)/layout";
import OnboardingPage from "@/app/(protected)/onboarding/page";
import { missingMessage, stepIndex, WIZARD_STEPS } from "@/features/onboarding/steps";
import { api } from "@/lib/api";
import { onboardingMissing } from "@/lib/errors";
import { getMeRequest } from "@/services/auth.service";
import { useAuthStore } from "@/stores/authStore";
import { nav, renderWithClient, resetNav } from "@/test/test-utils";
import type { BusinessSettings, OnboardingStatus, OnboardingStepCode, User } from "@/types/api";

jest.mock("next/navigation", () => ({
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  usePathname: () => require("@/test/test-utils").nav.pathname,
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  useRouter: () => require("@/test/test-utils").nav.router,
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  useSearchParams: () => require("@/test/test-utils").nav.search,
}));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), put: jest.fn(), delete: jest.fn() },
}));
jest.mock("@/services/auth.service", () => ({
  getMeRequest: jest.fn(),
  logoutRequest: jest.fn(),
}));
jest.mock("@/lib/sw-register", () => ({ registerServiceWorker: jest.fn() }));

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
  window.scrollTo = jest.fn();
});

// ── Backend falso (datos sintéticos) ─────────────────────────────────────────

const axiosConfig = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
function apiError(status: number, data: unknown) {
  return new AxiosError("x", "E", axiosConfig, null, {
    status,
    data,
    statusText: "",
    headers: {},
    config: axiosConfig,
  } as AxiosResponse);
}

const owner: User = {
  id: "u1",
  email: "duenia@example.com",
  username: null,
  role: "OWNER" as const,
  tenant_id: "t1",
  permission_profile_id: null,
};
const staff: User = {
  ...owner,
  id: "u2",
  email: null,
  username: "lavador.uno",
  role: "STAFF" as const,
};

const settings: BusinessSettings = {
  slug: "lavadero-prueba",
  name: "Lavadero Prueba",
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

const color = (hex: string) => ({ hex, oklch: `oklch(${hex})` });
const catalog = {
  palettes: [
    {
      code: "default",
      name: "Agua",
      background: color("#f8fafc"),
      primary: color("#0a7db2"),
      secondary: color("#f5ae39"),
      on_primary: color("#ffffff"),
    },
  ],
  fonts: [{ code: "default", name: "Sistema", family: null, description: "La del teléfono." }],
  default_palette: "default",
  default_font: "default",
};

const page = (items: unknown[] = []) => ({ items, total: items.length, limit: 50, offset: 0 });

function status(resume: OnboardingStepCode, notDone: OnboardingStepCode[] = []): OnboardingStatus {
  const until = stepIndex(resume);
  return {
    completed: false,
    completed_at: null,
    resume_step: resume,
    steps: WIZARD_STEPS.map((s, i) => ({
      step: s.id,
      required: !s.optional,
      done: i < until && !notDone.includes(s.id),
    })),
    missing: [],
  };
}

let current: OnboardingStatus;

function routeGet(url: string) {
  if (url === "/onboarding/status") return current;
  if (url === "/settings") return settings;
  if (url === "/branding/catalog") return catalog;
  if (url === "/body-type-sizes") return page([]);
  return page([]);
}

beforeEach(() => {
  resetNav();
  nav.pathname = "/onboarding";
  jest.clearAllMocks();
  useAuthStore.setState({
    user: owner,
    tenant: { id: "t1", name: "Lavadero Prueba" },
    permissions: [],
    mustChangePassword: false,
    onboardingCompleted: false,
    permissionsLoaded: true,
  });
  current = status("BUSINESS");
  (api.get as jest.Mock).mockImplementation(async (url: string) => ({ data: routeGet(url) }));
  (api.patch as jest.Mock).mockImplementation(async (_url: string, body: object) => ({
    data: { ...settings, ...body },
  }));
});

function renderWizard() {
  return renderWithClient(<OnboardingPage />);
}

function activeStep() {
  const steps = screen.getByRole("list", { name: "Pasos" });
  return within(steps)
    .getAllByRole("listitem")
    .find((li) => li.getAttribute("aria-current") === "step");
}

// ── Wizard ───────────────────────────────────────────────────────────────────

describe("wizard de alta", () => {
  it("se reanuda en el paso que indica el servidor", async () => {
    current = status("SCHEDULE");
    renderWizard();
    expect(
      await screen.findByRole("heading", { level: 2, name: /Puestos y horarios/ }),
    ).toBeInTheDocument();
    expect(activeStep()).toHaveTextContent("Puestos y horarios");
    expect(api.get).toHaveBeenCalledWith("/onboarding/status");
  });

  it("negocio: «Guardar y seguir» guarda en el servidor y pasa al paso siguiente", async () => {
    renderWizard();
    await screen.findByRole("heading", { level: 2, name: /Tu negocio/ });
    await userEvent.type(await screen.findByLabelText("Dirección del local"), "Calle Falsa 123");
    current = status("BRANDING");
    await userEvent.click(screen.getByRole("button", { name: "Guardar y seguir" }));
    await screen.findByRole("heading", { level: 2, name: /Personalización/ });
    expect(api.patch).toHaveBeenCalledWith(
      "/settings",
      expect.objectContaining({ address: "Calle Falsa 123" }),
    );
  });

  it("un paso opcional se puede saltar sin guardar", async () => {
    current = status("BRANDING");
    renderWizard();
    await screen.findByRole("heading", { level: 2, name: /Personalización/ });
    await userEvent.click(screen.getByRole("button", { name: "Saltar este paso" }));
    await screen.findByRole("heading", { level: 2, name: /Tamaños y carrocerías/ });
    expect(api.patch).not.toHaveBeenCalled();
    // y se puede volver
    await userEvent.click(screen.getByRole("button", { name: "Atrás" }));
    await screen.findByRole("heading", { level: 2, name: /Personalización/ });
  });

  it("un paso obligatorio no avanza hasta cumplirse y dice qué falta", async () => {
    current = {
      ...status("SIZES"),
      missing: [{ requirement: "VEHICLE_SIZE", step: "SIZES", service_ids: [] }],
    };
    renderWizard();
    await screen.findByRole("heading", { level: 2, name: /Tamaños y carrocerías/ });
    expect(screen.queryByRole("button", { name: /Saltar/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    const alert = await screen.findByRole("alert", { name: "Falta completar" });
    expect(alert).toHaveTextContent("Cargá al menos un tamaño de vehículo.");
    expect(activeStep()).toHaveTextContent("Tamaños");

    // el dueño cargó un tamaño: ahora avanza
    current = status("SERVICES");
    await userEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    await screen.findByRole("heading", { level: 2, name: /^Servicios/ });
    expect(screen.queryByRole("alert", { name: "Falta completar" })).toBeNull();
  });

  it("«Terminar» con faltantes (409) los muestra y lleva al primer paso que falta", async () => {
    current = status("STAFF");
    (api.post as jest.Mock).mockRejectedValue(
      apiError(409, {
        detail: {
          code: "ONBOARDING_INCOMPLETE",
          message: "x",
          missing: [
            { requirement: "PAYMENT_METHOD", step: "PAYMENTS", service_ids: [] },
            { requirement: "SERVICE_PRICES", step: "SERVICES", service_ids: ["s1", "s2"] },
          ],
        },
      }),
    );
    renderWizard();
    await screen.findByRole("heading", { level: 2, name: /Empleados/ });
    await userEvent.click(screen.getByRole("button", { name: "Terminar" }));
    await screen.findByRole("heading", { level: 2, name: /Medios de pago/ });
    const alert = screen.getByRole("alert", { name: "Falta completar" });
    expect(alert).toHaveTextContent("Medios de pago: Cargá al menos un medio de pago para cobrar.");
    expect(alert).toHaveTextContent("(2 servicios)");
    expect(api.post).toHaveBeenCalledWith("/onboarding/complete");
    expect(nav.router.replace).not.toHaveBeenCalled();
    expect(useAuthStore.getState().onboardingCompleted).toBe(false);
  });

  it("«Terminar» con todo listo marca el alta y va al inicio", async () => {
    current = status("STAFF");
    (api.post as jest.Mock).mockResolvedValue({
      data: { ...status("STAFF"), completed: true, completed_at: "2026-11-10T12:00:00Z" },
    });
    renderWizard();
    await screen.findByRole("heading", { level: 2, name: /Empleados/ });
    await userEvent.click(screen.getByRole("button", { name: "Terminar" }));
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/dashboard"));
    expect(useAuthStore.getState().onboardingCompleted).toBe(true);
  });

  it("un error que no es un faltante se muestra sin moverse", async () => {
    current = status("STAFF");
    (api.post as jest.Mock).mockRejectedValue(
      apiError(500, { detail: { code: "INTERNAL_ERROR" } }),
    );
    renderWizard();
    await screen.findByRole("heading", { level: 2, name: /Empleados/ });
    await userEvent.click(screen.getByRole("button", { name: "Terminar" }));
    expect(await screen.findByText(/no pudo procesar el pedido/)).toBeInTheDocument();
    expect(activeStep()).toHaveTextContent("Empleados");
  });

  it("si el estado no carga, lo dice", async () => {
    (api.get as jest.Mock).mockRejectedValue(apiError(403, { detail: { code: "FORBIDDEN" } }));
    renderWizard();
    expect(await screen.findByRole("alert")).toHaveTextContent(/permiso/);
  });

  it("un empleado no ve el wizard", async () => {
    useAuthStore.setState({ user: staff });
    renderWizard();
    expect(screen.getByRole("heading", { name: /El alta la hace el dueño/ })).toBeInTheDocument();
    expect(api.get).not.toHaveBeenCalledWith("/onboarding/status");
  });
});

describe("textos y errores del alta", () => {
  it("cada faltante dice en qué paso se resuelve", () => {
    expect(missingMessage({ requirement: "RESOURCE", step: "SCHEDULE", service_ids: [] })).toBe(
      "Puestos y horarios: Creá al menos un puesto.",
    );
    expect(
      missingMessage({ requirement: "SERVICE_PRICES", step: "SERVICES", service_ids: ["s1"] }),
    ).toMatch(/\(1 servicio\)$/);
    expect(stepIndex("STAFF")).toBe(WIZARD_STEPS.length - 1);
  });

  it("onboardingMissing solo lee el 409 del alta", () => {
    expect(onboardingMissing(apiError(409, { detail: { code: "IN_USE" } }))).toBeNull();
    expect(onboardingMissing(new Error("x"))).toBeNull();
    expect(
      onboardingMissing(apiError(409, { detail: { code: "ONBOARDING_INCOMPLETE", message: "x" } })),
    ).toEqual([]);
  });
});

// ── Redirección desde el layout ──────────────────────────────────────────────

const me = (user: User, onboarding_completed: boolean) => ({
  user,
  tenant: { id: "t1", name: "Lavadero Prueba" },
  permissions: [],
  must_change_password: false,
  onboarding_completed,
});

function renderShell() {
  return renderWithClient(
    <ProtectedLayout>
      <p>hijo</p>
    </ProtectedLayout>,
  );
}

describe("redirección por alta sin terminar", () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null, onboardingCompleted: null, permissionsLoaded: false });
  });

  it("el dueño sin alta va a /onboarding y no ve la pantalla", async () => {
    nav.pathname = "/dashboard";
    (getMeRequest as jest.Mock).mockResolvedValue(me(owner, false));
    renderShell();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/onboarding"));
    expect(screen.queryByText("hijo")).toBeNull();
  });

  it("en /onboarding el dueño sin alta ve el wizard (sin redirigir)", async () => {
    nav.pathname = "/onboarding";
    (getMeRequest as jest.Mock).mockResolvedValue(me(owner, false));
    renderShell();
    await waitFor(() => expect(useAuthStore.getState().onboardingCompleted).toBe(false));
    expect(screen.getByText("hijo")).toBeInTheDocument();
    expect(nav.router.replace).not.toHaveBeenCalled();
  });

  it("con el alta terminada, /onboarding vuelve al inicio", async () => {
    nav.pathname = "/onboarding";
    (getMeRequest as jest.Mock).mockResolvedValue(me(owner, true));
    renderShell();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/dashboard"));
  });

  it("un empleado no se redirige: ve un aviso", async () => {
    nav.pathname = "/dashboard";
    (getMeRequest as jest.Mock).mockResolvedValue(me(staff, false));
    renderShell();
    expect(await screen.findByText(/el dueño tiene que terminar el alta/)).toBeInTheDocument();
    expect(screen.getByText("hijo")).toBeInTheDocument();
    expect(nav.router.replace).not.toHaveBeenCalled();
  });

  it("con el alta terminada nadie ve el aviso ni se redirige", async () => {
    nav.pathname = "/dashboard";
    (getMeRequest as jest.Mock).mockResolvedValue(me(staff, true));
    renderShell();
    await waitFor(() => expect(useAuthStore.getState().onboardingCompleted).toBe(true));
    expect(screen.queryByText(/terminar el alta/)).toBeNull();
    expect(nav.router.replace).not.toHaveBeenCalled();
  });
});
