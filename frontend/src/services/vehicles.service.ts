import { api } from "@/lib/api";
import { crud, type CrudApi } from "@/services/config.service";
import type { PageParams } from "@/services/pagination";
import type {
  BodyTypeSize,
  BodyTypeSizesPayload,
  PaginatedResponse,
  VehicleColor,
  VehicleColorPatch,
  VehicleColorPayload,
  VehicleMake,
  VehicleMakePatch,
  VehicleMakePayload,
  VehicleModel,
  VehicleModelPatch,
  VehicleModelPayload,
} from "@/types/api";

/**
 * Catálogo de vehículos del negocio (FASE-4-CONTRATO §3.4 y §4, PR 4.4). Cada negocio arranca
 * con una copia de la base del mercado argentino y la edita, oculta o completa.
 *
 * Lectura: cualquier usuario. Sin `include_hidden` no vienen las filas ocultas (es lo que ven
 * los selectores); la pantalla de administración las pide. Escritura (`POST`, `PATCH`, `PUT`):
 * `CATALOGO_EDITAR`. No hay baja: se oculta con `PATCH { hidden: true }`.
 */

export interface VehicleListParams extends PageParams {
  /** Búsqueda tolerante (sin tildes, prefijos, errores de tipeo). Vacío = sin filtro. */
  q?: string;
  include_hidden?: boolean;
}

export interface VehicleModelListParams extends VehicleListParams {
  /** Sin marca, busca en todas: "toyota hilux" encuentra la Hilux. */
  make_id?: string;
}

/** Saca de la URL lo que no filtra: un `q` vacío, `include_hidden=false`, la marca sin elegir. */
function clean(params: VehicleModelListParams): VehicleModelListParams {
  const out: VehicleModelListParams = { limit: params.limit, offset: params.offset };
  if (params.q?.trim()) out.q = params.q.trim();
  if (params.include_hidden) out.include_hidden = true;
  if (params.make_id) out.make_id = params.make_id;
  return out;
}

/**
 * El `crud` de la configuración sin `remove`: el catálogo de vehículos no tiene baja (se oculta
 * con `PATCH { hidden: true }`).
 */
export type VehicleCatalogApi<R, C, U, P extends VehicleListParams> = Omit<
  CrudApi<R, C, U, P>,
  "remove"
>;

function catalogApi<R, C, U, P extends VehicleListParams>(
  path: string,
): VehicleCatalogApi<R, C, U, P> {
  const { list, create, update } = crud<R, C, U, P>(path, clean);
  return { list, create, update };
}

export const vehicleMakesApi = catalogApi<
  VehicleMake,
  VehicleMakePayload,
  VehicleMakePatch,
  VehicleListParams
>("/vehicle-makes");

export const vehicleModelsApi = catalogApi<
  VehicleModel,
  VehicleModelPayload,
  VehicleModelPatch,
  VehicleModelListParams
>("/vehicle-models");

export const vehicleColorsApi = catalogApi<
  VehicleColor,
  VehicleColorPayload,
  VehicleColorPatch,
  VehicleListParams
>("/vehicle-colors");

/** Las 9 carrocerías, cada una con el tamaño que sugiere o `null`. */
export async function getBodyTypeSizes(): Promise<BodyTypeSize[]> {
  const res = await api.get<PaginatedResponse<BodyTypeSize>>("/body-type-sizes", {
    params: { limit: 50, offset: 0 },
  });
  return res.data.items;
}

/** Reemplaza el mapa entero: una carrocería ausente o con `null` deja de sugerir tamaño. */
export async function putBodyTypeSizes(data: BodyTypeSizesPayload): Promise<BodyTypeSize[]> {
  const res = await api.put<PaginatedResponse<BodyTypeSize>>("/body-type-sizes", data);
  return res.data.items;
}
