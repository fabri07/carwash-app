"use client";

import { useEffect, useState } from "react";

/** Reloj de pantalla: `Date.now()` que se refresca cada `intervalMs` (cancelar por demora, "hoy"). */
export function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
