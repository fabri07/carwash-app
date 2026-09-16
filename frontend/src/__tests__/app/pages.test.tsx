import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { redirect } from "next/navigation";

import ProtectedLayout from "@/app/(protected)/layout";
import DashboardPage from "@/app/(protected)/dashboard/page";
import PublicLayout from "@/app/(public)/layout";
import LoginPage from "@/app/(public)/login/page";
import RegisterPage from "@/app/(public)/register/page";
import RootPage from "@/app/page";
import { refreshSession } from "@/lib/api";
import { getMeRequest, loginRequest, registerRequest } from "@/services/auth.service";
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
  user: { id: "u1", email: "a@b.com", role: "OWNER" as const, tenant_id: "t1" },
  tenant: { id: "t1", name: "Lavadero" },
};

beforeEach(() => {
  resetNav();
  jest.clearAllMocks();
  useAuthStore.setState({ user: null, tenant: null });
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
