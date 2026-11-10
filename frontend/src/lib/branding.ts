"use client";

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";

import { useSettings } from "@/features/configuracion/queries";
import { FONT_VARS } from "@/lib/fonts";
import { getBrandingCatalog } from "@/services/branding.service";
import type { BrandCatalog, BrandPalette } from "@/types/api";

/**
 * Marca del negocio en la app (FASE-4-CONTRATO §3.5, PR 4.5): paleta y tipografía del tenant
 * como variables CSS sobre `<html>`, con el tema de `globals.css` como respaldo.
 *
 * Se escriben en `<html>` (y no en un `<div>` del layout) para que también las vean los
 * diálogos, menús y toasts, que se montan en un portal fuera del layout. El tema usa triplets
 * HSL (`hsl(var(--primary))`, shadcn): el backend manda cada color también en hex y acá se
 * convierte. La paleta `default` y la tipografía `default` no escriben nada: queda el tema.
 */

export const BRANDING_KEYS = {
  catalog: ["branding", "catalog"] as const,
};

/** Pila del sistema: la de `default` y el respaldo de toda fuente propia mientras carga. */
export const SYSTEM_FONT_STACK =
  'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

/** Las variables que esto puede escribir: se borran todas antes de aplicar otra marca. */
export const BRAND_VARS = [
  "--background",
  "--primary",
  "--primary-foreground",
  "--ring",
  "--brand-secondary",
  "--font-brand",
] as const;

export type BrandVars = Partial<Record<(typeof BRAND_VARS)[number], string>>;

/** `#0a7db2` → `"199 89% 37%"` (el formato de las variables del tema). `null` si no es hex. */
export function hexToHsl(hex: string): string | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m?.[1]) return null;
  const n = parseInt(m[1], 16);
  const r = ((n >> 16) & 255) / 255;
  const g = ((n >> 8) & 255) / 255;
  const b = (n & 255) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  const d = max - min;
  let h = 0;
  let s = 0;
  if (d !== 0) {
    s = d / (1 - Math.abs(2 * l - 1));
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h = (h * 60 + 360) % 360;
  }
  return `${Math.round(h)} ${Math.round(s * 100)}% ${Math.round(l * 100)}%`;
}

/** Valor de `font-family` para un código del catálogo. Desconocido o `default`: el sistema. */
export function fontStack(code: string | null | undefined): string {
  const variable = code ? FONT_VARS[code] : undefined;
  return variable ? `var(${variable}), ${SYSTEM_FONT_STACK}` : SYSTEM_FONT_STACK;
}

/** Las variables a escribir para una paleta y una tipografía. Vacío = todo el tema. */
export function brandingVariables(
  palette: BrandPalette | null | undefined,
  fontCode: string | null | undefined,
  defaultPalette = "default",
): BrandVars {
  const vars: BrandVars = {};
  if (palette && palette.code !== defaultPalette) {
    const background = hexToHsl(palette.background.hex);
    const primary = hexToHsl(palette.primary.hex);
    const onPrimary = hexToHsl(palette.on_primary.hex);
    // Un hex roto deja el tema entero: nunca media paleta.
    if (background && primary && onPrimary) {
      vars["--background"] = background;
      vars["--primary"] = primary;
      vars["--ring"] = primary;
      vars["--primary-foreground"] = onPrimary;
      vars["--brand-secondary"] = palette.secondary.oklch;
    }
  }
  if (fontCode && FONT_VARS[fontCode]) vars["--font-brand"] = fontStack(fontCode);
  return vars;
}

/** Borra la marca anterior y escribe la nueva (inline, gana sobre `:root`). */
export function applyBranding(root: HTMLElement, vars: BrandVars): void {
  for (const name of BRAND_VARS) root.style.removeProperty(name);
  for (const [name, value] of Object.entries(vars)) root.style.setProperty(name, value);
}

/** Paleta del catálogo por código; desconocida → `undefined` (queda el tema). */
export function findPalette(catalog: BrandCatalog | undefined, code: string | undefined) {
  return catalog?.palettes.find((p) => p.code === code);
}

export function useBrandingCatalog(enabled = true) {
  return useQuery({
    queryKey: BRANDING_KEYS.catalog,
    queryFn: getBrandingCatalog,
    // Vive en el código del backend: cambia solo con un deploy.
    staleTime: Infinity,
    enabled,
  });
}

/**
 * Aplica la marca del negocio mientras el layout protegido está montado; al desmontarse (salir
 * de la sesión) vuelve al tema. `enabled = false` mientras no hay sesión utilizable.
 */
export function useTenantBranding(enabled: boolean): void {
  // La misma consulta que la pantalla de Configuración: guardar allá repinta al instante.
  const settings = useSettings(enabled);
  const catalog = useBrandingCatalog(enabled);
  const palette = findPalette(catalog.data, settings.data?.palette);
  const font = settings.data?.font;
  const defaultPalette = catalog.data?.default_palette;

  useEffect(() => {
    const root = document.documentElement;
    applyBranding(root, brandingVariables(palette, font, defaultPalette));
    return () => applyBranding(root, {});
  }, [palette, font, defaultPalette]);
}

// ── Logo: validación en el cliente (el servidor vuelve a validar todo, A11) ──────────────────

export const MAX_LOGO_BYTES = 2 * 1024 * 1024;
export const MIN_LOGO_SIDE = 200;
export const MAX_LOGO_SIDE = 4096;
export const ACCEPTED_LOGO_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;

/**
 * Mensaje si el archivo no sirve por tipo o peso; `null` si pasa. Un tipo vacío (algunos
 * navegadores y apps de archivos no lo informan) no se rechaza: decide el servidor, que mira el
 * contenido.
 */
export function logoFileError(file: File): string | null {
  if (file.type !== "" && !(ACCEPTED_LOGO_TYPES as readonly string[]).includes(file.type)) {
    return "El logo tiene que ser una imagen PNG, JPG o WebP.";
  }
  if (file.size > MAX_LOGO_BYTES) return "El logo puede pesar hasta 2 MB.";
  return null;
}

/** Tamaño en píxeles, si el navegador puede decodificarlo; `null` si no (lo valida el servidor). */
export async function readImageSize(file: File): Promise<{ width: number; height: number } | null> {
  if (typeof createImageBitmap !== "function") return null;
  try {
    const bitmap = await createImageBitmap(file);
    const size = { width: bitmap.width, height: bitmap.height };
    bitmap.close();
    return size;
  } catch {
    return null;
  }
}

/** Validación completa en el cliente: tipo, peso y lado mínimo. */
export async function validateLogo(file: File): Promise<string | null> {
  const basic = logoFileError(file);
  if (basic) return basic;
  const size = await readImageSize(file);
  if (size && Math.min(size.width, size.height) < MIN_LOGO_SIDE) {
    return `El logo tiene que medir al menos ${MIN_LOGO_SIDE}×${MIN_LOGO_SIDE} píxeles.`;
  }
  if (size && Math.max(size.width, size.height) > MAX_LOGO_SIDE) {
    return `El logo puede medir hasta ${MAX_LOGO_SIDE} píxeles de lado.`;
  }
  return null;
}
