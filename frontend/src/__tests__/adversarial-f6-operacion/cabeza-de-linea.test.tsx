/**
 * T3 (6.2) — Cabeza de línea de la cola: un 5xx en un job no debería frenar a los demás. Va en su
 * propio archivo porque el backoff del hook es estado de módulo y contaminaría a otras suites.
 */
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { KINDS, useOperacionSubmit } from "@/features/operacion/useOperacionSubmit";
import {
  apiError,
  createServer,
  goOffline,
  goOnline,
  job,
  resetUnits,
  type FakeServer,
} from "./harness";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
jest.mock("@/lib/api", () => ({
  API_URL: "http://localhost:8000",
  api: { get: jest.fn(), post: jest.fn() },
}));
jest.mock("@/services/catalog.service", () => ({ servicesApi: { list: jest.fn() } }));
jest.mock("@/services/config.service", () => ({
  getSettings: jest.fn(),
  resourcesApi: { list: jest.fn() },
  paymentMethodsApi: { list: jest.fn() },
  vehicleSizesApi: { list: jest.fn() },
}));

jest.setTimeout(30_000);

let server: FakeServer;
beforeEach(() => {
  resetUnits();
  server = createServer([job({ id: "j1" }), job({ id: "j2" })]);
});

function renderQueueHook() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return renderHook(() => useOperacionSubmit(), { wrapper });
}

describe("Dos jobs distintos: cabeza de línea", () => {
  // H5 DIFERIDO: es de la cola de 6.3 (FIFO por job con tope de 20 fallas transitorias, que ya desbloquea).
  // `it.failing` pasa mientras el hallazgo siga abierto y avisa cuando 6.3 lo arregle.
  it.failing(
    "HALLAZGO H5 (diferido a 6.3): un 5xx en un job no frena el envío del otro job (cola atascada por cabeza de línea)",
    async () => {
      goOffline(server);
      const { result } = renderQueueHook();
      await result.current.run(KINDS.start, { jobId: "j1", body: {} }, "iniciar:j1");
      await result.current.run(KINDS.start, { jobId: "j2", body: {} }, "iniciar:j2");
      server.failPost.push({ match: /\/jobs\/j1\/start/, error: () => apiError(500), times: -1 });
      goOnline(server);
      await act(async () => {
        await result.current.flush();
      });
      await new Promise((r) => setTimeout(r, 100));
      // j2 no tiene nada que ver con el 500 de j1: tendría que haberse sincronizado.
      expect(server.received.map((r) => r.url)).toContain("/jobs/j2/start");
    },
  );
});
