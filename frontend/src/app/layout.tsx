import type { Metadata, Viewport } from "next";

import "@/styles/globals.css";
import { Providers } from "./providers";

const appUrl =
  process.env.NEXT_PUBLIC_APP_URL ??
  (process.env.VERCEL_PROJECT_PRODUCTION_URL
    ? `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}`
    : process.env.VERCEL_URL
      ? `https://${process.env.VERCEL_URL}`
      : "http://localhost:3000");

const THEME_COLOR = "#0b6fa4";

export const metadata: Metadata = {
  metadataBase: new URL(appUrl),
  title: "carwash.app",
  description: "Gestión para lavaderos y centros de detailing.",
  applicationName: "carwash.app",
  // ADR-0011: instalable. Next emite <link rel="manifest">.
  manifest: "/manifest.webmanifest",
  icons: {
    icon: [
      { url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    // iOS ignora el manifiesto para el ícono de inicio.
    apple: [{ url: "/icons/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
  },
  // Meta `apple-mobile-web-app-*`: pantalla completa al abrir desde el ícono.
  appleWebApp: {
    capable: true,
    title: "carwash",
    statusBarStyle: "black-translucent",
  },
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Con `black-translucent` el contenido pasa bajo el notch; `viewport-fit`
  // habilita los `env(safe-area-inset-*)` que lo corren de ahí.
  viewportFit: "cover",
  themeColor: THEME_COLOR,
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es-AR">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
