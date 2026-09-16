"use client";

import { useEffect } from "react";

import { registrarSW } from "@/lib/sw-register";

/** Registra (o, con el interruptor apagado, desregistra) el SW al montar. */
export function ServiceWorkerRegistrar() {
  useEffect(() => {
    registrarSW().catch(() => {
      // Un SW que no registra no rompe la app: la app funciona sin él.
    });
  }, []);
  return null;
}
