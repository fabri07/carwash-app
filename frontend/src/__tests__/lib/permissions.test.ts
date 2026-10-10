import { renderHook } from "@testing-library/react";
import {
  AxiosError,
  AxiosHeaders,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import { useCan, useIsOwner, usePermission } from "@/hooks/usePermission";
import { changePasswordError, FormSubmitError, teamError } from "@/lib/errors";
import {
  ALL_PERMISSIONS,
  can,
  PERMISSION_GROUPS,
  PERMISSION_LABELS,
  sortPermissions,
} from "@/lib/permissions";
import { useAuthStore } from "@/stores/authStore";

const config = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
function httpError(status: number, code?: string) {
  const data = code ? { detail: { code, message: "x" } } : {};
  const response = { status, data, statusText: "", headers: {}, config } as AxiosResponse;
  return new AxiosError("x", "ERR_BAD_REQUEST", config, null, response);
}

const staff = {
  id: "u2",
  email: null,
  username: "lavador",
  role: "STAFF" as const,
  tenant_id: "t1",
  permission_profile_id: "p1",
};

describe("permisos (FASE-4-CONTRATO §2.1)", () => {
  it("cada permiso del enum tiene etiqueta y está en exactamente un área", () => {
    expect(ALL_PERMISSIONS).toHaveLength(12);
    const grouped = PERMISSION_GROUPS.flatMap((g) => g.permissions);
    expect([...grouped].sort()).toEqual([...ALL_PERMISSIONS].sort());
    expect(new Set(grouped).size).toBe(grouped.length);
    expect(PERMISSION_LABELS.COBROS_ANULAR).toBe("Anular cobros");
  });

  it("sortPermissions ordena como el contrato y saca repetidos", () => {
    expect(sortPermissions(["REPORTES_VER", "AGENDA_VER", "AGENDA_VER"])).toEqual([
      "AGENDA_VER",
      "REPORTES_VER",
    ]);
  });

  it("can: el OWNER puede todo; el STAFF solo lo de su perfil; sin usuario, nada", () => {
    expect(can({ role: "OWNER" }, [], "COBROS_ANULAR")).toBe(true);
    expect(can({ role: "STAFF" }, ["AGENDA_VER"], "AGENDA_VER")).toBe(true);
    expect(can({ role: "STAFF" }, ["AGENDA_VER"], "COBROS_ANULAR")).toBe(false);
    expect(can(null, ALL_PERMISSIONS, "AGENDA_VER")).toBe(false);
  });

  it("hooks leen el store", () => {
    useAuthStore.setState({ user: staff, permissions: ["JOBS_OPERAR"] });
    expect(renderHook(() => usePermission("JOBS_OPERAR")).result.current).toBe(true);
    expect(renderHook(() => usePermission("CAJA_VER")).result.current).toBe(false);
    expect(renderHook(() => useCan()).result.current("CAJA_VER")).toBe(false);
    expect(renderHook(() => useIsOwner()).result.current).toBe(false);
    useAuthStore.setState({ user: { ...staff, role: "OWNER" }, permissions: [] });
    expect(renderHook(() => useIsOwner()).result.current).toBe(true);
    expect(renderHook(() => useCan()).result.current("CAJA_VER")).toBe(true);
  });
});

describe("errores del equipo y del cambio de clave", () => {
  it("los 409 del contrato van a su campo, en castellano", () => {
    expect(teamError(httpError(409, "USERNAME_TAKEN"))).toEqual({
      field: "username",
      message: expect.stringMatching(/ya existe/),
    });
    expect(teamError(httpError(409, "EMAIL_TAKEN")).field).toBe("email");
    expect(teamError(httpError(409, "NAME_TAKEN")).field).toBe("name");
    expect(teamError(httpError(409, "PROFILE_IN_USE"))).toEqual({
      field: null,
      message: expect.stringMatching(/hay empleados con este perfil/),
    });
    expect(teamError(httpError(403, "PASSWORD_CHANGE_REQUIRED")).message).toMatch(/cambiar/);
  });

  it("lo demás cae en un mensaje por estado", () => {
    expect(teamError(httpError(403)).message).toMatch(/permiso/);
    expect(teamError(httpError(404)).message).toMatch(/No se encontró/);
    expect(teamError(httpError(422)).message).toMatch(/Revisá/);
    expect(teamError(httpError(429)).message).toMatch(/Demasiados/);
    expect(teamError(httpError(500)).message).toMatch(/servidor/);
    expect(teamError(new AxiosError("net", "ERR_NETWORK", config)).message).toMatch(/conexión/);
    expect(teamError(new Error("x")).message).toMatch(/inesperado/);
  });

  it("cambio de clave: actual incorrecta y nueva igual van a su campo", () => {
    expect(changePasswordError(httpError(400, "INVALID_CREDENTIALS")).field).toBe(
      "current_password",
    );
    expect(changePasswordError(httpError(400, "VALIDATION_ERROR")).field).toBe("new_password");
    expect(changePasswordError(httpError(500)).field).toBeNull();
  });

  it("FormSubmitError lleva el campo", () => {
    const e = new FormSubmitError({ field: "username", message: "tomado" });
    expect(e).toBeInstanceOf(Error);
    expect(e.field).toBe("username");
    expect(e.message).toBe("tomado");
  });
});
