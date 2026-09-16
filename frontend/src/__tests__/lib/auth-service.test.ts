import type { InternalAxiosRequestConfig } from "axios";

import { api } from "@/lib/api";
import {
  getMeRequest,
  loginRequest,
  logoutRequest,
  registerRequest,
} from "@/services/auth.service";

const session = {
  user: { id: "u1", email: "a@b.com", role: "OWNER", tenant_id: "t1" },
  tenant: { id: "t1", name: "L" },
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
    await expect(loginRequest({ email: "a@b.com", password: "x" })).resolves.toEqual(session);
    await expect(
      registerRequest({ email: "a@b.com", password: "12345678", tenant: "L" }),
    ).resolves.toEqual(session);
    await expect(getMeRequest()).resolves.toEqual(session);
    await expect(logoutRequest()).resolves.toBeUndefined();
    expect(seen.map((c) => `${c.method} ${c.url}`)).toEqual([
      "post /auth/login",
      "post /auth/register",
      "get /auth/me",
      "post /auth/logout",
    ]);
    expect(JSON.parse(seen[1]!.data as string)).toEqual({
      email: "a@b.com",
      password: "12345678",
      tenant: "L",
    });
    expect(seen.every((c) => c.withCredentials && !c.headers.Authorization)).toBe(true);
  });
});
