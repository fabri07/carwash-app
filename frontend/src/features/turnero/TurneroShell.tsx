"use client";

import { useEffect } from "react";
import Link from "next/link";
import { CalendarSearch, MapPin, Store } from "lucide-react";

import { applyBranding, brandingVariables } from "@/lib/branding";
import type { TurneroBusiness } from "@/types/api";

import { useTurneroBusiness } from "./queries";

/**
 * Marco del turnero público (PR 5.2): la marca del negocio (paleta, tipografía y logo de su
 * configuración, PR 4.5) sobre `<html>`, como en la app con sesión, y una cabecera con nombre y
 * dirección. Mobile-first: una sola columna angosta. Un slug inexistente o a medio configurar
 * muestra lo mismo (Z8).
 */
export function useBusinessBranding(business: TurneroBusiness | undefined): void {
  const palette = business?.palette;
  const font = business?.font.code;
  useEffect(() => {
    if (!palette) return;
    const root = document.documentElement;
    applyBranding(root, brandingVariables(palette, font));
    return () => applyBranding(root, {});
  }, [palette, font]);
}

function NotFound() {
  return (
    <div className="flex flex-col items-center gap-3 py-16 text-center">
      <Store className="h-10 w-10 text-muted-foreground" aria-hidden="true" />
      <h1 className="text-lg font-semibold">No encontramos este negocio</h1>
      <p className="text-sm text-muted-foreground">
        Revisá el link que te pasaron. Si sigue sin andar, consultale al negocio.
      </p>
    </div>
  );
}

export function TurneroShell({ slug, children }: { slug: string; children: React.ReactNode }) {
  const business = useTurneroBusiness(slug);
  useBusinessBranding(business.data);

  let body: React.ReactNode = children;
  if (business.isPending) {
    body = (
      <div role="status" aria-label="Cargando" className="flex justify-center py-16">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-muted border-t-primary" />
      </div>
    );
  } else if (business.isError) {
    body = <NotFound />;
  }

  const data = business.data;
  return (
    <div
      className="min-h-screen bg-background text-foreground"
      style={{ fontFamily: "var(--font-brand, inherit)" }}
    >
      <div className="mx-auto flex w-full max-w-md flex-col gap-6 px-4 pb-16 pt-6">
        {data && (
          <header className="flex items-center gap-3">
            {data.logo_url ? (
              // El logo vive en R2 (dominio público del negocio): `<img>` y no `next/image`
              // para no exigir el dominio en `next.config`.
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={data.logo_url}
                alt=""
                className="h-14 w-14 rounded-full border object-cover"
              />
            ) : (
              <div
                aria-hidden="true"
                className="flex h-14 w-14 items-center justify-center rounded-full bg-primary text-xl font-semibold text-primary-foreground"
              >
                {data.name.slice(0, 1).toUpperCase()}
              </div>
            )}
            <div className="min-w-0 flex-1">
              <h1 className="truncate text-xl font-semibold">{data.name}</h1>
              {data.address && (
                <p className="flex items-center gap-1 text-sm text-muted-foreground">
                  <MapPin className="h-4 w-4 shrink-0" aria-hidden="true" />
                  <span className="truncate">{data.address}</span>
                </p>
              )}
            </div>
            <Link
              href={`/${slug}/mi-turno`}
              className="inline-flex min-h-touch items-center gap-1 rounded-md px-2 text-sm font-medium text-primary underline-offset-4 hover:underline"
            >
              <CalendarSearch className="h-4 w-4" aria-hidden="true" />
              Mi turno
            </Link>
          </header>
        )}
        <main>{body}</main>
      </div>
    </div>
  );
}
