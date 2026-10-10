import type { InternalAxiosRequestConfig } from "axios";

import { api } from "@/lib/api";
import {
  changePasswordRequest,
  getMeRequest,
  loginRequest,
  logoutRequest,
  registerRequest,
} from "@/services/auth.service";

const session = {
  user: {
    id: "u1",
    email: "a@b.com",
    role: "OWNER",
    tenant_id: "t1",
    username: null,
    permission_profile_id: null,
  },
  tenant: { id: "t1", name: "L" },
  permissions: [],
  must_change_password: false,
};

describe("auth.service", () => {
  const seen: InternalAxiosRequestConfig[] = [];
  beforeAll(() => {
    api.defaults.adapter = async (config) => {
      seen.push(config);
      return { data: session, status: 200, statusText: "OK", headers: {}, config };
    };
  });

  it("habla con los endpoints del contrato, sin tokens en ningún lado", async () => {
    await expect(loginRequest({ identifier: "a@b.com", password: "x" })).resolves.toEqual(session);
    await expect(
      registerRequest({ email: "a@b.com", password: "12345678", tenant: "L" }),
    ).resolves.toEqual(session);
    await expect(getMeRequest()).resolves.toEqual(session);
    await expect(
      changePasswordRequest({ current_password: "vieja-123", new_password: "nueva-123" }),
    ).resolves.toEqual(session);
    await expect(logoutRequest()).resolves.toBeUndefined();
    expect(seen.map((c) => `${c.method} ${c.url}`)).toEqual([
      "post /auth/login",
      "post /auth/register",
      "get /auth/me",
      "post /auth/change-password",
      "post /auth/logout",
    ]);
    // D4-3: un solo campo para email o usuario.
    expect(JSON.parse(seen[0]!.data as string)).toEqual({ identifier: "a@b.com", password: "x" });
    expect(JSON.parse(seen[1]!.data as string)).toEqual({
      email: "a@b.com",
      password: "12345678",
      tenant: "L",
    });
    expect(seen.every((c) => c.withCredentials && !c.headers.Authorization)).toBe(true);
  });
});
