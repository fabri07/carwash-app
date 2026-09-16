"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

interface GlobalErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/**
 * Boundary raíz: cubre errores en el `layout.tsx` raíz mismo, fuera del alcance
 * de `error.tsx`. Renderiza su propio `<html><body>` (requisito de Next) y no
 * depende de `Providers` ni de Tailwind — si lo que rompió es el layout, no hay
 * garantía de que esos estilos estén.
 */
export default function GlobalError({ error, reset }: GlobalErrorProps) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="es-AR">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: "#f7f9fb",
          color: "#0f172a",
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
          padding: "24px",
        }}
      >
        <div style={{ maxWidth: 420, textAlign: "center" }}>
          <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 8 }}>Algo salió mal</h1>
          <p style={{ fontSize: 14, color: "#475569", marginBottom: 24 }}>
            Ocurrió un error inesperado al cargar carwash.app. Podés intentar de nuevo.
          </p>
          <button
            type="button"
            onClick={reset}
            style={{
              minHeight: 44,
              borderRadius: 8,
              backgroundColor: "#0b6fa4",
              color: "#fff",
              padding: "10px 20px",
              fontSize: 16,
              fontWeight: 500,
              border: "none",
              cursor: "pointer",
            }}
          >
            Reintentar
          </button>
        </div>
      </body>
    </html>
  );
}
