/**
 * Registro del service worker detrás del interruptor `NEXT_PUBLIC_SW_ENABLED`
 * (ADR-0011 §6).
 *
 * Apagado no es solo "no registrar": un SW ya instalado sigue vivo en el
 * navegador del usuario aunque el deploy nuevo no lo registre. Por eso el
 * camino de apagado desregistra todo lo que haya y borra sus cachés. Es el
 * camino de salida ante un SW mal desplegado, y está probado en
 * `src/__tests__/pwa/sw.test.ts`.
 */
export const SW_URL = "/sw.js";

export function isSwEnabled(): boolean {
  return process.env.NEXT_PUBLIC_SW_ENABLED === "true";
}

/**
 * Borra las cachés propias del SW. Se llama también en el logout: la caché de
 * navegaciones guarda HTML de páginas ya visitadas, que no debe quedar a mano
 * del siguiente usuario de un dispositivo compartido.
 */
export async function clearSwCaches(): Promise<void> {
  if (typeof caches === "undefined") return;
  const keys = await caches.keys();
  await Promise.all(keys.filter((k) => k.startsWith("carwash-")).map((k) => caches.delete(k)));
}

export async function unregisterAll(): Promise<void> {
  const registrations = await navigator.serviceWorker.getRegistrations();
  await Promise.all(registrations.map((r) => r.unregister()));
  await clearSwCaches();
}

export async function registrarSW(): Promise<"registered" | "unregistered" | "unsupported"> {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
    return "unsupported";
  }
  if (!isSwEnabled()) {
    await unregisterAll();
    return "unregistered";
  }
  await navigator.serviceWorker.register(SW_URL, { scope: "/" });
  return "registered";
}
