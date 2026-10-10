import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import {
  AxiosError,
  AxiosHeaders,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import TeamPage from "@/app/(protected)/configuracion/equipo/page";
import {
  createProfile,
  createStaff,
  deleteProfile,
  deleteStaff,
  listProfiles,
  listStaff,
  resetStaffPassword,
  updateProfile,
  updateStaff,
} from "@/services/team.service";
import { useAuthStore } from "@/stores/authStore";
import { useToastStore } from "@/stores/toastStore";
import type { PermissionProfile, StaffMember } from "@/types/api";

jest.mock("@/services/team.service", () => ({
  listProfiles: jest.fn(),
  createProfile: jest.fn(),
  updateProfile: jest.fn(),
  deleteProfile: jest.fn(),
  listStaff: jest.fn(),
  createStaff: jest.fn(),
  updateStaff: jest.fn(),
  deleteStaff: jest.fn(),
  resetStaffPassword: jest.fn(),
}));

// Flujos completos de formulario (tipear, validar, reintentar): bajo carga del
// CI pasan los 5 s por defecto sin estar colgados.
jest.setTimeout(20_000);

// Radix (dialog, checkbox) usa APIs de puntero que jsdom no trae.
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

const profile = (id: string, name: string, permissions: PermissionProfile["permissions"]) => ({
  id,
  name,
  permissions,
  tenant_id: "t1",
  created_at: "2026-10-09T00:00:00Z",
  updated_at: "2026-10-09T00:00:00Z",
});

const profiles = [
  profile("p-cajero", "Cajero", ["AGENDA_VER", "CAJA_VER"]),
  profile("p-lavador", "Lavador", ["AGENDA_VER", "JOBS_OPERAR"]),
  profile("p-vacio", "Mirón", []),
];

const juan: StaffMember = {
  id: "s1",
  username: "juan",
  email: null,
  role: "STAFF",
  tenant_id: "t1",
  permission_profile_id: "p-lavador",
  must_change_password: true,
  created_at: "2026-10-09T00:00:00Z",
  updated_at: "2026-10-09T00:00:00Z",
};

function page<T>(items: T[]) {
  return { items, total: items.length, limit: 25, offset: 0, has_more: false };
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TeamPage />
    </QueryClientProvider>,
  );
}

async function pickProfile(dialog: HTMLElement, name: string) {
  await userEvent.selectOptions(within(dialog).getByLabelText("Perfil"), name);
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
  useAuthStore.setState({ user: owner, permissions: [] });
  (listProfiles as jest.Mock).mockResolvedValue(page(profiles));
  (listStaff as jest.Mock).mockResolvedValue(page([juan]));
});

describe("/configuracion/equipo — acceso", () => {
  it("un STAFF ve el aviso y no se pide nada al backend", () => {
    useAuthStore.setState({
      user: { ...owner, role: "STAFF", email: null, username: "ana" },
      permissions: ["AGENDA_VER"],
    });
    renderPage();
    expect(screen.getByRole("heading", { name: /no tenés acceso/i })).toBeInTheDocument();
    expect(listStaff).not.toHaveBeenCalled();
    expect(listProfiles).not.toHaveBeenCalled();
  });
});

describe("Empleados", () => {
  it("lista con usuario, email, perfil y 'debe cambiar clave'", async () => {
    renderPage();
    const table = await screen.findByRole("table", { name: "Empleados" });
    const row = (await within(table).findByText("juan")).closest("tr")!;
    expect(within(row).getByText("Lavador")).toBeInTheDocument();
    expect(within(row).getByText("Sí")).toBeInTheDocument();
    expect(listStaff).toHaveBeenCalledWith({ limit: 25, offset: 0 });
  });

  it("alta: valida, mapea USERNAME_TAKEN al campo y reintenta con la misma Idempotency-Key", async () => {
    (createStaff as jest.Mock)
      .mockRejectedValueOnce(apiError(409, "USERNAME_TAKEN"))
      .mockResolvedValueOnce(juan);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /nuevo empleado/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo empleado" });

    await userEvent.click(within(dialog).getByRole("button", { name: "Crear empleado" }));
    expect(await within(dialog).findByText("Elegí un usuario")).toBeInTheDocument();
    expect(within(dialog).getByText("Falta elegir el perfil")).toBeInTheDocument();
    expect(createStaff).not.toHaveBeenCalled();

    await userEvent.type(within(dialog).getByLabelText("Usuario"), "Juan");
    await userEvent.type(within(dialog).getByLabelText("Clave inicial"), "clave-inicial");
    await pickProfile(dialog, "Cajero");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear empleado" }));

    const usuario = within(dialog).getByLabelText("Usuario");
    expect(await within(dialog).findByText(/ese usuario ya existe/i)).toBeInTheDocument();
    expect(usuario).toHaveAttribute("aria-invalid", "true");

    await userEvent.clear(usuario);
    await userEvent.type(usuario, "juan.p");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear empleado" }));
    await waitFor(() => expect(createStaff).toHaveBeenCalledTimes(2));
    const [firstPayload, firstKey] = (createStaff as jest.Mock).mock.calls[0]!;
    const [secondPayload, secondKey] = (createStaff as jest.Mock).mock.calls[1]!;
    expect(firstPayload).toEqual({
      username: "juan",
      password: "clave-inicial",
      permission_profile_id: "p-cajero",
      email: null,
    });
    expect(secondPayload.username).toBe("juan.p");
    expect(secondKey).toBe(firstKey);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(useToastStore.getState().toasts[0]?.message).toMatch(/juan.p ya puede entrar/);
  });

  it("alta: un replay DUPLICATE_IDEMPOTENT cuenta como hecho; EMAIL_TAKEN va al email", async () => {
    (createStaff as jest.Mock)
      .mockRejectedValueOnce(apiError(409, "EMAIL_TAKEN"))
      .mockRejectedValueOnce(apiError(409, "DUPLICATE_IDEMPOTENT"));
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /nuevo empleado/i }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Usuario"), "ana");
    await userEvent.type(within(dialog).getByLabelText("Clave inicial"), "12345678");
    await userEvent.type(within(dialog).getByLabelText("Email (opcional)"), "no-es-email");
    await pickProfile(dialog, "Lavador");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear empleado" }));
    expect(await within(dialog).findByText("Email inválido")).toBeInTheDocument();

    await userEvent.clear(within(dialog).getByLabelText("Email (opcional)"));
    await userEvent.type(within(dialog).getByLabelText("Email (opcional)"), "ana@ejemplo.com");
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear empleado" }));
    expect(await within(dialog).findByText(/ya está en uso/i)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear empleado" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect((createStaff as jest.Mock).mock.calls[1]![0].email).toBe("ana@ejemplo.com");
    // Un replay no confirma los datos del diálogo: lo guardado es el primer envío.
    expect(useToastStore.getState().toasts.at(-1)?.message).toMatch(/ya se había guardado/);
  });

  it("edición: email vacío se manda como null (lo borra)", async () => {
    (updateStaff as jest.Mock).mockResolvedValue(juan);
    (listStaff as jest.Mock).mockResolvedValue(page([{ ...juan, email: "juan@ejemplo.com" }]));
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Editar juan" }));
    const dialog = await screen.findByRole("dialog", { name: "Editar a juan" });
    const email = within(dialog).getByLabelText("Email (opcional)");
    expect(email).toHaveValue("juan@ejemplo.com");
    await userEvent.clear(email);
    await pickProfile(dialog, "Cajero");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() =>
      expect(updateStaff).toHaveBeenCalledWith("s1", {
        username: "juan",
        permission_profile_id: "p-cajero",
        email: null,
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("edición: un error sin campo queda arriba del botón; cancelar cierra", async () => {
    (updateStaff as jest.Mock).mockRejectedValue(apiError(404, "NOT_FOUND"));
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Editar juan" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar cambios" }));
    expect(await within(dialog).findByText(/no se encontró/i)).toHaveAttribute("role", "alert");
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("reset de clave: confirma la clave y avisa que se cierran sus sesiones", async () => {
    (resetStaffPassword as jest.Mock).mockResolvedValue(juan);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Resetear la clave de juan" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Clave nueva"), "otra-clave-1");
    await userEvent.type(within(dialog).getByLabelText("Repetí la clave"), "otra-clave-2");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar clave" }));
    expect(await within(dialog).findByText("Las contraseñas no coinciden")).toBeInTheDocument();
    await userEvent.clear(within(dialog).getByLabelText("Repetí la clave"));
    await userEvent.type(within(dialog).getByLabelText("Repetí la clave"), "otra-clave-1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar clave" }));
    await waitFor(() =>
      expect(resetStaffPassword).toHaveBeenCalledWith("s1", { password: "otra-clave-1" }),
    );
    await waitFor(() =>
      expect(useToastStore.getState().toasts[0]?.message).toMatch(/sesiones abiertas se cerraron/),
    );
  });

  it("reset de clave: el error del servidor se muestra", async () => {
    (resetStaffPassword as jest.Mock).mockRejectedValue(apiError(404, "NOT_FOUND"));
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Resetear la clave de juan" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Clave nueva"), "otra-clave-1");
    await userEvent.type(within(dialog).getByLabelText("Repetí la clave"), "otra-clave-1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar clave" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/no se encontró/i);
  });

  it("desactivar pide confirmación con un diálogo propio, no window.confirm", async () => {
    const confirmSpy = jest.spyOn(window, "confirm");
    (deleteStaff as jest.Mock)
      .mockRejectedValueOnce(apiError(404, "NOT_FOUND"))
      .mockResolvedValueOnce(undefined);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Desactivar a juan" }));
    const dialog = await screen.findByRole("dialog", { name: "¿Desactivar a juan?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Desactivar" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/no se encontró/i);
    await userEvent.click(within(dialog).getByRole("button", { name: "Desactivar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(deleteStaff).toHaveBeenLastCalledWith("s1");
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("si la lista falla, la tabla lo dice", async () => {
    (listStaff as jest.Mock).mockRejectedValue(apiError(403, "FORBIDDEN"));
    renderPage();
    expect(await screen.findByText(/no tenés permiso/i)).toBeInTheDocument();
  });
});

describe("Perfiles", () => {
  async function openTab() {
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: "Perfiles" }));
    return screen.findByRole("table", { name: "Perfiles de permisos" });
  }

  it("lista cada perfil con sus permisos en castellano", async () => {
    const table = await openTab();
    const row = (await within(table).findByText("Cajero")).closest("tr")!;
    expect(within(row).getByText("Ver agenda")).toBeInTheDocument();
    expect(within(row).getByText("Ver caja")).toBeInTheDocument();
    expect(within(table).getByText("Sin permisos")).toBeInTheDocument();
  });

  it("alta con checkboxes agrupados por área; NAME_TAKEN va al nombre", async () => {
    (createProfile as jest.Mock)
      .mockRejectedValueOnce(apiError(409, "NAME_TAKEN"))
      .mockResolvedValueOnce(profile("p-new", "Encargado de turno", []));
    await openTab();
    await userEvent.click(screen.getByRole("button", { name: /nuevo perfil/i }));
    const dialog = await screen.findByRole("dialog", { name: "Nuevo perfil" });
    expect(within(dialog).getByRole("group", { name: "Cobros y caja" })).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear perfil" }));
    expect(await within(dialog).findByText("Poné un nombre")).toBeInTheDocument();

    await userEvent.type(within(dialog).getByLabelText("Nombre del perfil"), "Encargado de turno");
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Ver reportes" }));
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Ver agenda" }));
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Anular cobros" }));
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Anular cobros" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear perfil" }));
    expect(await within(dialog).findByText("Ya hay un perfil con ese nombre.")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Crear perfil" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    const [payload, key] = (createProfile as jest.Mock).mock.calls[1]!;
    // En el orden del contrato, aunque se tildaron al revés.
    expect(payload).toEqual({
      name: "Encargado de turno",
      permissions: ["AGENDA_VER", "REPORTES_VER"],
    });
    expect(key).toEqual(expect.any(String));
    expect((createProfile as jest.Mock).mock.calls[0]![1]).toBe(key);
  });

  it("edición: parte de lo guardado; un replay de alta no aplica a la edición", async () => {
    (updateProfile as jest.Mock).mockResolvedValue(profiles[0]);
    await openTab();
    await userEvent.click(await screen.findByRole("button", { name: "Editar el perfil Cajero" }));
    const dialog = await screen.findByRole("dialog", { name: "Editar Cajero" });
    expect(within(dialog).getByRole("checkbox", { name: "Ver caja" })).toBeChecked();
    await userEvent.click(within(dialog).getByRole("checkbox", { name: "Ver caja" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Guardar cambios" }));
    await waitFor(() =>
      expect(updateProfile).toHaveBeenCalledWith("p-cajero", {
        name: "Cajero",
        permissions: ["AGENDA_VER"],
      }),
    );
    expect(useToastStore.getState().toasts.at(-1)?.message).toBe("Perfil actualizado.");
  });

  it("borrar un perfil en uso muestra el 409 PROFILE_IN_USE claro, sin cerrar", async () => {
    (deleteProfile as jest.Mock)
      .mockRejectedValueOnce(apiError(409, "PROFILE_IN_USE"))
      .mockResolvedValueOnce(undefined);
    await openTab();
    await userEvent.click(await screen.findByRole("button", { name: "Borrar el perfil Lavador" }));
    const dialog = await screen.findByRole("dialog", { name: "¿Borrar el perfil Lavador?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Borrar" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      /hay empleados con este perfil/,
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Borrar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(deleteProfile).toHaveBeenLastCalledWith("p-lavador");
  });
});
