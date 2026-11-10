import { api } from "@/lib/api";
import type { BrandCatalog, BrandSuggestion, LogoUpload } from "@/types/api";

/**
 * Personalización (FASE-4-CONTRATO §4, PR 4.5). El catálogo lo lee cualquier usuario; el logo y
 * la sugerencia, solo el dueño. La paleta y la tipografía se guardan con `PATCH /settings`.
 *
 * El `Content-Type: multipart/form-data` es obligatorio: `api` manda JSON por defecto y, con ese
 * header, axios convierte el `FormData` en JSON. Con este, el navegador pone el `boundary`.
 */
const MULTIPART = { headers: { "Content-Type": "multipart/form-data" } };

function form(file: File): FormData {
  const data = new FormData();
  data.append("file", file);
  return data;
}

export async function getBrandingCatalog(): Promise<BrandCatalog> {
  const res = await api.get<BrandCatalog>("/branding/catalog");
  return res.data;
}

/** Sube y guarda el logo; devuelve su URL y la paleta sugerida. 503 si no hay almacenamiento. */
export async function uploadLogo(file: File): Promise<LogoUpload> {
  const res = await api.post<LogoUpload>("/branding/logo", form(file), MULTIPART);
  return res.data;
}

/** Solo sugiere la paleta: no guarda nada. */
export async function suggestPalette(file: File): Promise<BrandSuggestion> {
  const res = await api.post<BrandSuggestion>("/branding/suggest-palette", form(file), MULTIPART);
  return res.data;
}

export async function deleteLogo(): Promise<void> {
  await api.delete("/branding/logo");
}
