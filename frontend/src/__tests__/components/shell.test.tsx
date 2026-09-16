import { act, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { toast as sonner } from "sonner";

import { AuthHydrationBoundary } from "@/components/auth/AuthHydrationBoundary";
import { Header, getInitials, getPageLabel } from "@/components/layout/Header";
import { Sidebar, isActive } from "@/components/layout/Sidebar";
import { ServiceWorkerRegistrar } from "@/components/pwa/ServiceWorkerRegistrar";
import { ToastBridge } from "@/components/ToastBridge";
import { EmptyState } from "@/components/ui/empty-state";
import { pendingLogoutMessage, useLogout } from "@/features/auth/useLogout";
import { clearSwCaches, registrarSW } from "@/lib/sw-register";
import { logoutRequest } from "@/services/auth.service";
import { useAuthStore } from "@/stores/authStore";
import { useOfflineQueueStore } from "@/stores/offlineQueueStore";
import { useToastStore } from "@/stores/toastStore";
import { nav, renderWithClient, resetNav } from "@/test/test-utils";

jest.mock("next/navigation", () => ({
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  usePathname: () => require("@/test/test-utils").nav.pathname,
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  useRouter: () => require("@/test/test-utils").nav.router,
}));
jest.mock("@/services/auth.service", () => ({ logoutRequest: jest.fn() }));
jest.mock("@/lib/sw-register", () => ({
  registrarSW: jest.fn(),
  clearSwCaches: jest.fn(async () => undefined),
}));
jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), info: jest.fn(), warning: jest.fn() },
}));

// Radix (sheet) usa APIs de puntero que jsdom no trae.
beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.releasePointerCapture = () => undefined;
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

const session = {
  user: { id: "u1", email: "dueno@lavadero.com", role: "OWNER" as const, tenant_id: "t1" },
  tenant: { id: "t1", name: "Sola CleanCars" },
};

beforeEach(() => {
  resetNav();
  jest.clearAllMocks();
});

describe("AuthHydrationBoundary", () => {
  it("no renderiza los hijos hasta rehidratar (evita el parpadeo de 'no logueado')", () => {
    useAuthStore.setState({ _hasHydrated: false });
    const { rerender } = render(
      <AuthHydrationBoundary>
        <p>contenido</p>
      </AuthHydrationBoundary>,
    );
    expect(screen.queryByText("contenido")).toBeNull();
    expect(screen.getByRole("status")).toBeInTheDocument();
    act(() => useAuthStore.setState({ _hasHydrated: true }));
    rerender(
      <AuthHydrationBoundary>
        <p>contenido</p>
      </AuthHydrationBoundary>,
    );
    expect(screen.getByText("contenido")).toBeInTheDocument();
  });
});

describe("EmptyState", () => {
  it("renderiza título, descripción y acción por link", () => {
    render(<EmptyState title="Vacío" description="Nada" action={{ label: "Ir", href: "/x" }} />);
    expect(screen.getByRole("heading", { name: "Vacío" })).toBeInTheDocument();
    expect(screen.getByText("Nada")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ir" })).toHaveAttribute("href", "/x");
  });

  it("variante compacta con acción por botón e ícono propio", async () => {
    const onClick = jest.fn();
    render(
      <EmptyState
        variant="compact"
        icon={<span>i</span>}
        title="T"
        action={{ label: "Hacer", onClick }}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Hacer" }));
    expect(onClick).toHaveBeenCalled();
    expect(screen.getByText("i")).toBeInTheDocument();
  });
});

describe("Sidebar", () => {
  it("marca el ítem activo y colapsa en escritorio", async () => {
    nav.pathname = "/dashboard";
    render(<Sidebar mobileOpen={false} onMobileOpenChange={jest.fn()} />);
    const desktop = screen.getByTestId("sidebar-desktop");
    const link = within(desktop).getByRole("link", { name: "Inicio" });
    expect(link).toHaveAttribute("aria-current", "page");
    expect(link.className).toMatch(/min-h-touch/);
    const toggle = within(desktop).getByRole("button", { name: "Colapsar menú" });
    await userEvent.click(toggle);
    expect(within(desktop).getByRole("button", { name: "Expandir menú" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("en móvil abre un cajón que se cierra al navegar", async () => {
    const onChange = jest.fn();
    render(<Sidebar mobileOpen onMobileOpenChange={onChange} />);
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("link", { name: "Inicio" }));
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("isActive no confunde prefijos", () => {
    expect(isActive("/dashboard/x", "/dashboard")).toBe(true);
    expect(isActive("/dashboardx", "/dashboard")).toBe(false);
  });
});

describe("Header", () => {
  it("helpers", () => {
    expect(getPageLabel("/dashboard")).toBe("Inicio");
    expect(getPageLabel("/otra")).toBe("");
    expect(getInitials("Sola CleanCars")).toBe("SC");
    expect(getInitials("a@b.com")).toBe("AB");
  });

  /*
   * El DropdownMenu de Radix no se abre en estos tests: abrirlo en jsdom (por
   * click o por teclado) cuelga el worker de Jest. El comportamiento del
   * logout se prueba en `useLogout`; acá, que el shell lo cablea.
   */
  it("muestra el disparador del menú de usuario y el botón de menú móvil", async () => {
    useAuthStore.setState(session);
    const onMenuToggle = jest.fn();
    renderWithClient(<Header onMenuToggle={onMenuToggle} />);
    const trigger = screen.getByRole("button", { name: "Menú de usuario" });
    expect(trigger).toHaveTextContent("SC");
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    await userEvent.click(screen.getByRole("button", { name: "Abrir menú" }));
    expect(onMenuToggle).toHaveBeenCalled();
  });
});

describe("useLogout", () => {
  const pendiente = {
    id: "p1",
    userId: "u1",
    tenantId: "t1",
    kind: "k",
    payload: {},
    createdAt: "x",
    attempts: 0,
  };

  beforeEach(() => useOfflineQueueStore.setState({ items: [] }));

  function setup(confirmFn?: (m: string) => boolean) {
    const { qc } = renderWithClient(<></>);
    const clearSpy = jest.spyOn(qc, "clear");
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    return { clearSpy, ...renderHook(() => useLogout(confirmFn), { wrapper }) };
  }

  it("cierra sesión en el servidor, limpia todo y va a /login", async () => {
    useAuthStore.setState(session);
    (logoutRequest as jest.Mock).mockResolvedValue(undefined);
    const { result, clearSpy } = setup();
    await act(() => result.current.logout());
    expect(logoutRequest).toHaveBeenCalled();
    expect(clearSpy).toHaveBeenCalled();
    expect(useAuthStore.getState().user).toBeNull();
    expect(nav.router.replace).toHaveBeenCalledWith("/login");
    expect(clearSwCaches).toHaveBeenCalled();
    expect(result.current.loggingOut).toBe(true);
  });

  it("con cargas pendientes avisa; si cancela, no cierra sesión ni borra la cola", async () => {
    useAuthStore.setState(session);
    useOfflineQueueStore.setState({ items: [pendiente] });
    const confirmFn = jest.fn(() => false);
    const { result } = setup(confirmFn);
    let done: boolean | undefined;
    await act(async () => {
      done = await result.current.logout();
    });
    expect(done).toBe(false);
    expect(confirmFn).toHaveBeenCalledWith(pendingLogoutMessage(1));
    expect(logoutRequest).not.toHaveBeenCalled();
    expect(useAuthStore.getState().user).toEqual(session.user);
    expect(useOfflineQueueStore.getState().items).toHaveLength(1);
  });

  it("si confirma, cierra sesión y la cola queda para su dueño", async () => {
    useAuthStore.setState(session);
    useOfflineQueueStore.setState({ items: [pendiente, { ...pendiente, id: "p2" }] });
    (logoutRequest as jest.Mock).mockResolvedValue(undefined);
    const confirmFn = jest.fn(() => true);
    const { result } = setup(confirmFn);
    await act(() => result.current.logout());
    expect(confirmFn).toHaveBeenCalledWith(expect.stringContaining("2 cargas"));
    expect(logoutRequest).toHaveBeenCalled();
    expect(useOfflineQueueStore.getState().items).toHaveLength(2);
  });

  it("las cargas de otro usuario no disparan el aviso", async () => {
    useAuthStore.setState(session);
    useOfflineQueueStore.setState({ items: [{ ...pendiente, userId: "otro" }] });
    (logoutRequest as jest.Mock).mockResolvedValue(undefined);
    const confirmFn = jest.fn(() => false);
    const { result } = setup(confirmFn);
    await act(() => result.current.logout());
    expect(confirmFn).not.toHaveBeenCalled();
    expect(logoutRequest).toHaveBeenCalled();
  });

  it("por defecto el aviso usa window.confirm", async () => {
    useAuthStore.setState(session);
    useOfflineQueueStore.setState({ items: [pendiente] });
    const confirmSpy = jest.spyOn(window, "confirm").mockReturnValue(false);
    const { qc } = renderWithClient(<></>);
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useLogout(), { wrapper });
    await act(() => result.current.logout());
    expect(confirmSpy).toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("si el servidor no responde, limpia igual y va a /login?expired=1", async () => {
    useAuthStore.setState(session);
    (logoutRequest as jest.Mock).mockRejectedValue(new Error("offline"));
    const { result } = setup();
    await act(() => result.current.logout());
    expect(useAuthStore.getState().user).toBeNull();
    expect(nav.router.replace).toHaveBeenCalledWith("/login?expired=1");
  });
});

describe("ToastBridge", () => {
  it("vacía la cola hacia sonner respetando variante y duración", () => {
    useToastStore.setState({ toasts: [] });
    render(<ToastBridge />);
    act(() => {
      useToastStore.getState().add("guardado", "success", 1000);
      useToastStore.getState().add("falló", "error");
    });
    expect(sonner.success).toHaveBeenCalledWith(
      "guardado",
      expect.objectContaining({ duration: 1000 }),
    );
    expect(sonner.error).toHaveBeenCalledWith("falló", expect.objectContaining({ duration: 8000 }));
    expect(useToastStore.getState().toasts).toEqual([]);
  });
});

describe("ServiceWorkerRegistrar", () => {
  it("registra al montar y no rompe si falla", async () => {
    (registrarSW as jest.Mock).mockRejectedValueOnce(new Error("x"));
    render(<ServiceWorkerRegistrar />);
    await waitFor(() => expect(registrarSW).toHaveBeenCalled());
  });
});
