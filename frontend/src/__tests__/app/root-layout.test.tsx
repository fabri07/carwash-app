import { render, screen } from "@testing-library/react";

import RootLayout, { metadata, viewport } from "@/app/layout";
import { Providers } from "@/app/providers";

jest.mock("@/lib/sw-register", () => ({ registrarSW: jest.fn(async () => "unregistered") }));

describe("layout raíz (ADR-0011)", () => {
  it("declara manifest, íconos de iOS y viewport-fit=cover", () => {
    expect(metadata.manifest).toBe("/manifest.webmanifest");
    expect(metadata.appleWebApp).toMatchObject({ capable: true });
    expect(viewport.viewportFit).toBe("cover");
    expect(viewport.themeColor).toMatch(/^#/);
  });

  it("envuelve a los hijos con los providers", async () => {
    jest.spyOn(console, "error").mockImplementation(() => undefined); // <html> dentro de <div>
    render(
      <RootLayout>
        <p>app</p>
      </RootLayout>,
    );
    expect(await screen.findByText("app")).toBeInTheDocument();
  });

  it("Providers renderiza tras hidratar el store", async () => {
    render(
      <Providers>
        <p>hijo</p>
      </Providers>,
    );
    expect(await screen.findByText("hijo")).toBeInTheDocument();
  });
});
