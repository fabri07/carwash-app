import { Inter, Manrope, Outfit, Plus_Jakarta_Sans } from "next/font/google";

/**
 * Tipografías elegibles por el negocio (FASE-4-CONTRATO §3.5, PR 4.5). Los códigos son los de
 * `backend/app/domain/branding.py::FONTS`; un meta-test del backend verifica que estén todos.
 *
 * `next/font` las sirve desde nuestro dominio (no hay pedido a Google en el navegador) y
 * `preload: false` hace que el navegador baje solo la que el negocio eligió: el `@font-face` de
 * las demás queda declarado pero sin usar. `default` es la pila del sistema y no descarga nada.
 *
 * Cada una expone una variable CSS (`--font-inter`…) que se declara en `<html>` (layout raíz);
 * `lib/branding.ts` apunta `--font-brand` a la elegida.
 */
// `next/font` exige literales en cada llamada (no admite un objeto compartido con spread).
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  preload: false,
  variable: "--font-inter",
});
const manrope = Manrope({
  subsets: ["latin"],
  display: "swap",
  preload: false,
  variable: "--font-manrope",
});
const plusJakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  display: "swap",
  preload: false,
  variable: "--font-plus-jakarta",
});
const outfit = Outfit({
  subsets: ["latin"],
  display: "swap",
  preload: false,
  variable: "--font-outfit",
});

/** Clases que declaran las variables de las cuatro fuentes. Van en `<html>`. */
export const FONT_VARIABLES = [inter, manrope, plusJakarta, outfit]
  .map((f) => f.variable)
  .join(" ");

/** Código del catálogo → variable CSS de la familia. `default` no tiene: es la del sistema. */
export const FONT_VARS: Record<string, string> = {
  inter: "--font-inter",
  manrope: "--font-manrope",
  plus_jakarta: "--font-plus-jakarta",
  outfit: "--font-outfit",
};
