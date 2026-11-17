"use client";

import { useCallback, useState } from "react";

/**
 * Clave de deduplicación estable por formulario: un UUID al montar, que se renueva SOLO tras un
 * guardado confirmado. Un reintento después de un error (o de un envío que quedó en la cola)
 * reutiliza la misma clave y, con ella, la misma Idempotency-Key: no crea un segundo trabajo.
 */
export function useStableKey(prefix: string) {
  const [id, setId] = useState(() => crypto.randomUUID());
  const renew = useCallback(() => setId(crypto.randomUUID()), []);
  return { key: `${prefix}:${id}`, renew };
}

/** Hash corto (djb2) de un texto: distingue una corrección distinta de un doble toque idéntico. */
export function shortHash(text: string): string {
  let h = 5381;
  for (let i = 0; i < text.length; i++) h = ((h << 5) + h + text.charCodeAt(i)) >>> 0;
  return h.toString(36);
}
