import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/** Estado compartido del mock de `next/navigation` (se configura por test). */
export const nav = {
  pathname: "/dashboard",
  search: new URLSearchParams(),
  router: {
    replace: jest.fn(),
    push: jest.fn(),
    refresh: jest.fn(),
    back: jest.fn(),
    prefetch: jest.fn(),
  },
};

export function resetNav() {
  nav.pathname = "/dashboard";
  nav.search = new URLSearchParams();
  for (const fn of Object.values(nav.router)) fn.mockReset();
}

export function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    qc,
    ...render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>),
  };
}
