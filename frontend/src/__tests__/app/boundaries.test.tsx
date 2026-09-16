import { fireEvent, render, screen } from "@testing-library/react";
import * as Sentry from "@sentry/nextjs";

import ErrorPage from "@/app/error";
import GlobalError from "@/app/global-error";
import GlobalLoading from "@/app/loading";

beforeEach(() => jest.clearAllMocks());

// Archivo aparte de pages.test.tsx: en el mismo archivo, después de abrir un
// Sheet de Radix, cualquier test siguiente colgaba el worker de jsdom.
describe("boundaries", () => {
  it("error.tsx reporta a Sentry y permite reintentar", async () => {
    const reset = jest.fn();
    const error = new Error("boom");
    render(<ErrorPage error={error} reset={reset} />);
    expect(Sentry.captureException).toHaveBeenCalledWith(error);
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(reset).toHaveBeenCalled();
    expect(screen.getByRole("link", { name: /volver al inicio/i })).toHaveAttribute(
      "href",
      "/dashboard",
    );
  });

  it("global-error.tsx reporta a Sentry", async () => {
    const reset = jest.fn();
    const error = new Error("layout roto");
    // Renderiza su propio <html>: se monta en un documento aparte para no anidar.
    jest.spyOn(console, "error").mockImplementation(() => undefined);
    render(<GlobalError error={error} reset={reset} />);
    expect(Sentry.captureException).toHaveBeenCalledWith(error);
    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(reset).toHaveBeenCalled();
  });

  it("loading.tsx anuncia la carga", () => {
    render(<GlobalLoading />);
    expect(screen.getByRole("status", { name: "Cargando" })).toBeInTheDocument();
  });
});
