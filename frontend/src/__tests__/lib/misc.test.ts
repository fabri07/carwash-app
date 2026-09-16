import type { ErrorEvent, EventHint } from "@sentry/nextjs";
import {
  AxiosError,
  AxiosHeaders,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";

import { apiErrorCode, authErrorMessage, safeNextPath } from "@/lib/errors";
import { queryClient } from "@/lib/queryClient";
import { PATENTE_VALUE_RE, REDACTED, redactValue, scrubSentryEvent } from "@/lib/sentryScrub";
import { cn } from "@/lib/utils";

const config = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
function httpError(status: number, data: unknown = {}) {
  const response = { status, data, statusText: "", headers: {}, config } as AxiosResponse;
  return new AxiosError("x", "ERR_BAD_REQUEST", config, null, response);
}

describe("sentryScrub (patente es el PII fuerte)", () => {
  it("reconoce patentes Mercosur y viejas", () => {
    for (const p of ["AB123CD", "ab 123 cd", "ABC123", "abc-123"]) {
      expect(PATENTE_VALUE_RE.test(p)).toBe(true);
    }
    expect(PATENTE_VALUE_RE.test("dummy-resources")).toBe(false);
  });

  it("redacta por clave y por valor", () => {
    expect(redactValue("patente", "x")).toBe(REDACTED);
    expect(redactValue("Cookie", "x")).toBe(REDACTED);
    expect(redactValue("nota", "auto AB123CD")).toBe(REDACTED);
    expect(redactValue("nota", "dni 30.123.456")).toBe(REDACTED);
    expect(redactValue("nota", "hola")).toBe("hola");
    expect(redactValue("n", 5)).toBe(5);
  });

  it("limpia header, query, body, url, extra y breadcrumbs (A13)", () => {
    const event = {
      request: {
        headers: { "x-patente": "AB123CD", accept: "json" },
        query_string: "q=AB123CD",
        data: '{"patente":"AB123CD"}',
        url: "https://api.carwash.app/v1/x?p=AB123CD",
      },
      extra: { vehiculo: "AB123CD", ok: "sí" },
      breadcrumbs: [{ message: "GET /x?AB123CD", data: { token: "t" } }, { message: "ok" }],
    } as unknown as ErrorEvent;
    const out = scrubSentryEvent(event, {} as EventHint)!;
    expect(out.request!.headers).toEqual({ "x-patente": REDACTED, accept: "json" });
    expect(out.request!.query_string).toBe(REDACTED);
    expect(out.request!.data).toBe(REDACTED);
    expect(out.request!.url).toBe(REDACTED);
    expect(out.extra).toEqual({ vehiculo: REDACTED, ok: "sí" });
    expect(out.breadcrumbs![0]).toEqual({ message: REDACTED, data: { token: REDACTED } });
    expect(out.breadcrumbs![1]!.message).toBe("ok");
    expect(JSON.stringify(out)).not.toMatch(/AB123CD/);
  });

  it("adjunta una copia saneada del config de axios sin mutar el original", () => {
    const original = {
      isAxiosError: true,
      config: { headers: { Cookie: "s", a: "b" }, data: "{}" },
    };
    const out = scrubSentryEvent({} as ErrorEvent, { originalException: original } as EventHint)!;
    expect(out.contexts!.axios_request).toEqual({
      headers: { Cookie: REDACTED, a: "b" },
      data: REDACTED,
    });
    expect(original.config.headers.Cookie).toBe("s");
  });

  it("un evento sin nada que limpiar pasa intacto", () => {
    const out = scrubSentryEvent(
      { request: { query_string: "a=1" } } as ErrorEvent,
      {
        originalException: { isAxiosError: true, config: {} },
      } as EventHint,
    )!;
    expect(out.request!.query_string).toBe("a=1");
    expect(out.contexts).toBeUndefined();
  });
});

describe("errors", () => {
  it("prioriza el código del contrato", () => {
    const e = httpError(401, { detail: { code: "INVALID_CREDENTIALS", message: "x" } });
    expect(apiErrorCode(e)).toBe("INVALID_CREDENTIALS");
    expect(authErrorMessage(e, "login")).toMatch(/incorrectos/);
    expect(
      authErrorMessage(
        httpError(409, { detail: { code: "EMAIL_TAKEN", message: "" } }),
        "register",
      ),
    ).toMatch(/Ya existe/);
    expect(apiErrorCode(new Error("x"))).toBeUndefined();
  });

  it("cae al estado si no vino código", () => {
    expect(authErrorMessage(httpError(401), "login")).toMatch(/incorrectos/);
    expect(authErrorMessage(httpError(409), "register")).toMatch(/Ya existe/);
    expect(authErrorMessage(httpError(422), "register")).toMatch(/Revisá/);
    expect(authErrorMessage(httpError(429), "login")).toMatch(/Demasiados/);
    expect(authErrorMessage(httpError(500), "login")).toMatch(/servidor/);
    expect(authErrorMessage(new AxiosError("net", "ERR_NETWORK", config), "login")).toMatch(
      /conexión/,
    );
    expect(authErrorMessage(new Error("x"), "login")).toMatch(/inesperado/);
  });

  it("safeNextPath deja pasar rutas internas (normalizadas)", () => {
    expect(safeNextPath("/dashboard?x=1")).toBe("/dashboard?x=1");
    expect(safeNextPath("/dashboard/sub#a")).toBe("/dashboard/sub#a");
    expect(safeNextPath("/dashboard/../dashboard")).toBe("/dashboard");
  });

  it("safeNextPath bloquea open redirects, incluidos los que normaliza new URL (M5)", () => {
    const malos = [
      null,
      undefined,
      "",
      "dashboard",
      "https://evil.example",
      "//evil.example",
      "/\\evil.example",
      "/\\/evil.example",
      "\\\\evil.example",
      // `new URL` descarta tab/LF/CR y deja `//evil.example`.
      "/\t/evil.example",
      "/\n/evil.example",
      "/\r/evil.example",
      "\t//evil.example",
      "/\u0000/evil.example",
      "/\u007f/evil.example",
      // Variantes codificadas (llegan así si la URL se codificó dos veces).
      "/%09/evil.example",
      "/%0a/evil.example",
      "/%0D/evil.example",
      "/%2Fevil.example",
      "/%5Cevil.example",
      "%2F%2Fevil.example",
      // Codificación inválida.
      "/%E0%A4%A",
    ];
    for (const bad of malos) {
      expect(safeNextPath(bad)).toBe("/dashboard");
    }
  });

  it("para cualquier candidato, lo que devuelve resuelve al mismo origen", () => {
    for (const c of ["/\t/evil.example", "/ok", "/%09/x", "/a b"]) {
      const out = safeNextPath(c);
      expect(new URL(out, window.location.origin).origin).toBe(window.location.origin);
    }
  });
});

describe("utils y queryClient", () => {
  it("cn combina y resuelve conflictos de tailwind", () => {
    expect(cn("h-8", false && "x", "h-11")).toBe("h-11");
  });

  it("queryClient no reintenta mutaciones", () => {
    expect(queryClient.getDefaultOptions().mutations?.retry).toBe(0);
  });
});
