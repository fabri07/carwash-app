import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs/config";

/**
 * Origen de la API. La CSP necesita conocerlo en tiempo de build para poder
 * permitir el `connect-src` — si queda en `'self'`, el fetch a
 * `api.carwashdetailapp.com` lo bloquea el navegador y el síntoma es un error de red
 * indistinguible de "el backend está caído".
 */
const API_ORIGIN = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * CSP de la aplicación. Vive acá y no en `vercel.json` (que es del agente
 * `deploy`): el manifiesto de portado asigna las cabeceras de aplicación al
 * frontend, para que no haya dos archivos mandando la misma cabecera.
 *
 * `script-src` incluye `'unsafe-inline'` a propósito y con deuda anotada: Next
 * inyecta scripts inline de bootstrap y el camino correcto (nonce por request)
 * obliga a que toda página pase por el middleware y deja de poder cachearse
 * estáticamente. Se difiere a la fase que tenga rutas públicas cacheadas.
 */
const CSP = [
  "default-src 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self' ${API_ORIGIN} https://*.sentry.io`,
  "manifest-src 'self'",
  "worker-src 'self'",
].join("; ");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  env: {
    // Vercel corre preview Y producción con NODE_ENV=production — VERCEL_ENV
    // ("production" | "preview" | "development") es lo que realmente distingue
    // un error de un PR de prueba de uno de un cliente real.
    NEXT_PUBLIC_SENTRY_ENVIRONMENT: process.env.VERCEL_ENV ?? "development",
  },
  async headers() {
    return [
      {
        // El service worker NUNCA se cachea: si `/sw.js` queda en la caché del
        // navegador, el interruptor de apagado (ADR-0011 §6) deja de funcionar
        // y el usuario afectado no puede arreglarlo solo.
        source: "/sw.js",
        headers: [
          { key: "Cache-Control", value: "no-store, no-cache, must-revalidate" },
          { key: "Service-Worker-Allowed", value: "/" },
        ],
      },
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: CSP },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,
  release: { name: process.env.VERCEL_GIT_COMMIT_SHA },
  silent: !process.env.CI,
  widenClientFileUpload: true,
  // Sin DSN ni token (build local / CI sin secretos) el plugin no sube nada;
  // que no sea un error es deliberado: el build tiene que correr en una máquina
  // sin credenciales de Sentry.
  webpack: { treeshake: { removeDebugLogging: true } },
});
