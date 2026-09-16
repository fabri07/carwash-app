import * as Sentry from "@sentry/nextjs";
import {
  AxiosError,
  AxiosHeaders,
  CanceledError,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import { api, authClient, browserNavigation, handleApiResponseError } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";

type Handler = (config: InternalAxiosRequestConfig) => Promise<AxiosResponse>;

function respond(config: InternalAxiosRequestConfig, status: number, data: unknown = {}) {
  const response: AxiosResponse = {
    data,
    status,
    statusText: String(status),
    headers: {},
    config,
  };
  if (status >= 400) {
    return Promise.reject(
      new AxiosError(`HTTP ${status}`, "ERR_BAD_REQUEST", config, null, response),
    );
  }
  return Promise.resolve(response);
}

const user = { id: "u1", email: "a@b.com", role: "OWNER" as const, tenant_id: "t1" };

describe("lib/api", () => {
  let apiHandler: Handler;
  let refreshHandler: jest.Mock;
  let assignSpy: jest.SpyInstance;

  beforeEach(() => {
    jest.clearAllMocks();
    api.defaults.adapter = (config) => apiHandler(config);
    refreshHandler = jest.fn((config: InternalAxiosRequestConfig) => respond(config, 200));
    authClient.defaults.adapter = (config) => refreshHandler(config);
    assignSpy = jest.spyOn(browserNavigation, "assign").mockImplementation(() => undefined);
    useAuthStore.setState({ user });
  });

  afterEach(() => assignSpy.mockRestore());

  it("manda cookies (withCredentials) y ningún header Authorization", async () => {
    let seen: InternalAxiosRequestConfig | undefined;
    apiHandler = (config) => {
      seen = config;
      return respond(config, 200);
    };
    await api.get("/dummy-resources");
    expect(seen?.withCredentials).toBe(true);
    expect(seen?.headers.Authorization).toBeUndefined();
    expect(seen?.headers["X-Trace-Id"]).toEqual(expect.any(String));
    expect(Sentry.addBreadcrumb).toHaveBeenCalled();
  });

  it("respeta un X-Trace-Id que ya venga puesto", async () => {
    let seen: InternalAxiosRequestConfig | undefined;
    apiHandler = (config) => {
      seen = config;
      return respond(config, 200);
    };
    await api.get("/x", { headers: { "X-Trace-Id": "fijo" } });
    expect(seen?.headers["X-Trace-Id"]).toBe("fijo");
  });

  it("N respuestas 401 simultáneas disparan UN solo refresh (single-flight)", async () => {
    const calls = new Map<string, number>();
    apiHandler = (config) => {
      const n = (calls.get(config.url!) ?? 0) + 1;
      calls.set(config.url!, n);
      return respond(config, n === 1 ? 401 : 200, { url: config.url });
    };
    // El refresh tarda: las 401 que llegan mientras está pendiente se cuelgan de él.
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    refreshHandler.mockImplementation(async (config: InternalAxiosRequestConfig) => {
      await gate;
      return respond(config, 200);
    });

    const pending = Promise.all(["/a", "/b", "/c", "/d", "/e"].map((u) => api.get(u)));
    await new Promise((r) => setTimeout(r, 0));
    release();
    const results = await pending;

    expect(refreshHandler).toHaveBeenCalledTimes(1);
    expect(results.map((r) => r.data.url)).toEqual(["/a", "/b", "/c", "/d", "/e"]);
    expect(assignSpy).not.toHaveBeenCalled();
  });

  it("libera la promesa al terminar: un vencimiento posterior refresca de nuevo", async () => {
    let fail = true;
    apiHandler = (config) => {
      const status = fail ? 401 : 200;
      fail = !fail;
      return respond(config, status);
    };
    await api.get("/uno");
    await api.get("/dos");
    expect(refreshHandler).toHaveBeenCalledTimes(2);
  });

  it("si el refresh falla, limpia el usuario y vuelve al login con next y expired", async () => {
    window.history.pushState({}, "", "/dashboard?x=1");
    apiHandler = (config) => respond(config, 401);
    refreshHandler.mockImplementation((config: InternalAxiosRequestConfig) => respond(config, 401));

    await expect(api.get("/dummy-resources")).rejects.toBeInstanceOf(AxiosError);
    expect(useAuthStore.getState().user).toBeNull();
    expect(assignSpy).toHaveBeenCalledWith("/login?next=%2Fdashboard%3Fx%3D1&expired=1");
  });

  it("si después del refresh sigue en 401, no entra en loop: expira la sesión", async () => {
    apiHandler = (config) => respond(config, 401);
    await expect(api.get("/dummy-resources")).rejects.toBeInstanceOf(AxiosError);
    expect(refreshHandler).toHaveBeenCalledTimes(1);
    expect(assignSpy).toHaveBeenCalledTimes(1);
  });

  it("un 401 del login es credencial inválida, no sesión vencida: no refresca", async () => {
    apiHandler = (config) => respond(config, 401);
    await expect(api.post("/auth/login", {})).rejects.toBeInstanceOf(AxiosError);
    expect(refreshHandler).not.toHaveBeenCalled();
    expect(assignSpy).not.toHaveBeenCalled();
    expect(useAuthStore.getState().user).toEqual(user);
  });

  it("un 403 u otro 4xx se rechaza tal cual", async () => {
    apiHandler = (config) => respond(config, 403);
    await expect(api.get("/x")).rejects.toMatchObject({ response: { status: 403 } });
    expect(refreshHandler).not.toHaveBeenCalled();
  });

  it("un 5xx deja breadcrumb y no fabrica un evento propio", async () => {
    apiHandler = (config) => respond(config, 503);
    await expect(api.get("/x")).rejects.toMatchObject({ response: { status: 503 } });
    expect(Sentry.captureException).not.toHaveBeenCalled();
    expect(Sentry.addBreadcrumb).toHaveBeenCalledWith(expect.objectContaining({ level: "error" }));
  });

  it("un error de red se reporta a Sentry (el backend nunca lo vio)", async () => {
    apiHandler = (config) => Promise.reject(new AxiosError("Network Error", "ERR_NETWORK", config));
    await expect(api.get("/x")).rejects.toBeInstanceOf(AxiosError);
    expect(Sentry.captureException).toHaveBeenCalledTimes(1);
  });

  it("una request cancelada no es un error: solo breadcrumb", async () => {
    const config = {
      url: "/x",
      method: "get",
      headers: new AxiosHeaders(),
    } as InternalAxiosRequestConfig;
    const canceled = new CanceledError(undefined, undefined, config);
    await expect(handleApiResponseError(canceled)).rejects.toBe(canceled);
    expect(Sentry.captureException).not.toHaveBeenCalled();
    expect(Sentry.addBreadcrumb).toHaveBeenCalled();
  });
});
