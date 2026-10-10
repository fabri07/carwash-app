import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { redirect } from "next/navigation";

import ProtectedLayout from "@/app/(protected)/layout";
import DashboardPage from "@/app/(protected)/dashboard/page";
import PublicLayout from "@/app/(public)/layout";
import LoginPage from "@/app/(public)/login/page";
import RegisterPage from "@/app/(public)/register/page";
import RootPage from "@/app/page";
import ChangePasswordPage from "@/app/(account)/cambiar-clave/page";
import AccountLayout from "@/app/(account)/layout";
import { refreshSession } from "@/lib/api";
import {
  changePasswordRequest,
  getMeRequest,
  loginRequest,
  registerRequest,
} from "@/services/auth.service";
import { useAuthStore } from "@/stores/authStore";
import { nav, renderWithClient, resetNav } from "@/test/test-utils";

jest.mock("next/navigation", () => ({
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  usePathname: () => require("@/test/test-utils").nav.pathname,
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  useRouter: () => require("@/test/test-utils").nav.router,
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  useSearchParams: () => require("@/test/test-utils").nav.search,
  redirect: jest.fn(),
}));
jest.mock("@/services/auth.service", () => ({
  loginRequest: jest.fn(),
  registerRequest: jest.fn(),
  getMeRequest: jest.fn(),
  logoutRequest: jest.fn(),
  changePasswordRequest: jest.fn(),
}));
jest.mock("@/lib/api", () => ({ refreshSession: jest.fn() }));

beforeAll(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

const session = {
  user: {
    id: "u1",
    email: "a@b.com",
    role: "OWNER" as const,
    tenant_id: "t1",
    username: null,
    permission_profile_id: null,
  },
  tenant: { id: "t1", name: "Lavadero" },
  permissions: [],
  must_change_password: false,
};

beforeEach(() => {
  resetNav();
  jest.clearAllMocks();
  useAuthStore.setState({ user: null, tenant: null, permissions: [], mustChangePassword: false });
  (refreshSession as jest.Mock).mockRejectedValue(new Error("sin sesión"));
});

describe("login → dashboard", () => {
  async function fillLogin() {
    await userEvent.type(await screen.findByLabelText(/email/i), "a@b.com");
    await userEvent.type(screen.getByLabelText(/contraseña/i), "secreto123");
    await userEvent.click(screen.getByRole("button", { name: /ingresar/i }));
  }

  it("un login correcto guarda la sesión y navega al next validado", async () => {
    nav.search = new URLSearchParams("next=/dashboard/algo");
    (loginRequest as jest.Mock).mockResolvedValue(session);
    render(<LoginPage />);
    expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeInTheDocument();
    await fillLogin();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/dashboard/algo"));
    expect(useAuthStore.getState().tenant).toEqual(session.tenant);
  });

  it("un empleado con clave elegida por el dueño va a /cambiar-clave, no al next", async () => {
    nav.search = new URLSearchParams("next=/dashboard/algo");
    (loginRequest as jest.Mock).mockResolvedValue({ ...session, must_change_password: true });
    render(<LoginPage />);
    await fillLogin();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/cambiar-clave"));
    expect(loginRequest).toHaveBeenCalledWith({ identifier: "a@b.com", password: "secreto123" });
    expect(useAuthStore.getState().mustChangePassword).toBe(true);
  });

  it("un next externo se ignora (open redirect)", async () => {
    nav.search = new URLSearchParams("next=https://evil.example");
    (loginRequest as jest.Mock).mockResolvedValue(session);
    render(<LoginPage />);
    await fillLogin();
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/dashboard"));
  });

  it("credenciales inválidas se muestran y no navega", async () => {
    const { AxiosError } = jest.requireActual<typeof import("axios")>("axios");
    (loginRequest as jest.Mock).mockRejectedValue(
      new AxiosError("x", "ERR", undefined, null, {
        status: 401,
        data: { detail: { code: "INVALID_CREDENTIALS", message: "x" } },
      } as never),
    );
    render(<LoginPage />);
    await fillLogin();
    expect(await screen.findByRole("alert")).toHaveTextContent(/incorrectos/);
    expect(nav.router.replace).not.toHaveBeenCalled();
  });

  it("si queda una sesión renovable, /login refresca en silencio y vuelve", async () => {
    nav.search = new URLSearchParams("next=/dashboard");
    (refreshSession as jest.Mock).mockResolvedValue(session);
    render(<LoginPage />);
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/dashboard"));
    expect(useAuthStore.getState().user).toEqual(session.user);
  });

  it("con ?expired=1 no intenta refrescar", async () => {
    nav.search = new URLSearchParams("expired=1");
    render(<LoginPage />);
    await screen.findByRole("heading", { name: "Iniciar sesión" });
    expect(refreshSession).not.toHaveBeenCalled();
  });

  it("registro crea la cuenta y entra; un error se muestra", async () => {
    (registerRequest as jest.Mock)
      .mockRejectedValueOnce(new Error("no axios"))
      .mockResolvedValueOnce(session);
    render(<RegisterPage />);
    await userEvent.type(await screen.findByLabelText(/nombre del negocio/i), "Lavadero");
    await userEvent.type(screen.getByLabelText(/email/i), "a@b.com");
    await userEvent.type(screen.getByLabelText(/contraseña/i), "secreto123");
    await userEvent.click(screen.getByRole("button", { name: /crear cuenta/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/inesperado/);
    await userEvent.click(screen.getByRole("button", { name: /crear cuenta/i }));
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/dashboard"));
    expect(screen.getByRole("link", { name: /iniciá sesión/i })).toHaveAttribute("href", "/login");
  });
});

describe("app shell y dashboard vacío", () => {
  it("el dashboard es un EmptyState y nada más", () => {
    render(<DashboardPage />);
    expect(screen.getByRole("heading", { name: "Inicio" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /todavía no hay nada/i })).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("el layout protegido pide /auth/me y actualiza la sesión", async () => {
    (getMeRequest as jest.Mock).mockResolvedValue(session);
    renderWithClient(
      <ProtectedLayout>
        <p>hijo</p>
      </ProtectedLayout>,
    );
    expect(screen.getByText("hijo")).toBeInTheDocument();
    await waitFor(() => expect(useAuthStore.getState().user).toEqual(session.user));
    await userEvent.click(screen.getByRole("button", { name: "Abrir menú" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("con must_change_password el shell no muestra nada y manda a /cambiar-clave", async () => {
    (getMeRequest as jest.Mock).mockResolvedValue({ ...session, must_change_password: true });
    renderWithClient(
      <ProtectedLayout>
        <p>hijo</p>
      </ProtectedLayout>,
    );
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/cambiar-clave"));
    expect(screen.queryByText("hijo")).toBeNull();
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("layout público y raíz", () => {
    render(
      <PublicLayout>
        <p>form</p>
      </PublicLayout>,
    );
    expect(screen.getByText("form")).toBeInTheDocument();
    RootPage();
    expect(redirect).toHaveBeenCalledWith("/dashboard");
  });
});

describe("/cambiar-clave (§2.3)", () => {
  function renderChange() {
    return renderWithClient(
      <AccountLayout>
        <ChangePasswordPage />
      </AccountLayout>,
    );
  }

  async function fill(actual: string, nueva: string, repetir = nueva) {
    await userEvent.type(screen.getByLabelText("Contraseña actual"), actual);
    await userEvent.type(screen.getByLabelText("Contraseña nueva"), nueva);
    await userEvent.type(screen.getByLabelText("Repetí la contraseña nueva"), repetir);
    await userEvent.click(screen.getByRole("button", { name: "Cambiar contraseña" }));
  }

  it("obligatorio: explica por qué, cambia y va al inicio con el flag apagado", async () => {
    (getMeRequest as jest.Mock).mockResolvedValue({ ...session, must_change_password: true });
    (changePasswordRequest as jest.Mock).mockResolvedValue(session);
    renderChange();
    expect(await screen.findByText(/la eligió el dueño/)).toBeInTheDocument();
    await fill("clave-del-dueno", "mi-clave-nueva");
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/dashboard"));
    expect(changePasswordRequest).toHaveBeenCalledWith({
      current_password: "clave-del-dueno",
      new_password: "mi-clave-nueva",
    });
    expect(useAuthStore.getState().mustChangePassword).toBe(false);
  });

  it("valida en el cliente: confirmación distinta y nueva igual a la actual", async () => {
    (getMeRequest as jest.Mock).mockResolvedValue(session);
    renderChange();
    expect(await screen.findByText(/otras sesiones abiertas/)).toBeInTheDocument();
    await fill("clave-actual", "clave-actual", "otra-cosa");
    expect(await screen.findByText("Las contraseñas no coinciden")).toBeInTheDocument();
    expect(screen.getByText("La nueva tiene que ser distinta de la actual")).toBeInTheDocument();
    expect(changePasswordRequest).not.toHaveBeenCalled();
  });

  it("clave actual incorrecta (400) aparece en su campo; otro error arriba del botón", async () => {
    const { AxiosError } = jest.requireActual<typeof import("axios")>("axios");
    (getMeRequest as jest.Mock).mockResolvedValue(session);
    (changePasswordRequest as jest.Mock)
      .mockRejectedValueOnce(
        new AxiosError("x", "ERR", undefined, null, {
          status: 400,
          data: { detail: { code: "INVALID_CREDENTIALS", message: "x" } },
        } as never),
      )
      .mockRejectedValueOnce(new Error("raro"));
    renderChange();
    await fill("mal", "mi-clave-nueva");
    expect(await screen.findByText("La contraseña actual no es correcta.")).toBeInTheDocument();
    expect(screen.getByLabelText("Contraseña actual")).toHaveAttribute("aria-invalid", "true");
    await userEvent.click(screen.getByRole("button", { name: "Cambiar contraseña" }));
    expect(await screen.findByText(/inesperado/)).toBeInTheDocument();
    expect(nav.router.replace).not.toHaveBeenCalled();
  });

  it("se puede cerrar sesión desde ahí", async () => {
    (getMeRequest as jest.Mock).mockResolvedValue(session);
    renderChange();
    await userEvent.click(screen.getByRole("button", { name: "Cerrar sesión" }));
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/login"));
  });
});
