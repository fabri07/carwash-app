import { api } from "@/lib/api";
import { crud, type CrudApi } from "@/services/config.service";
import type { PageParams } from "@/services/pagination";
import type {
  PaginatedResponse,
  Service,
  ServiceCategory,
  ServiceCategoryPatch,
  ServiceCategoryPayload,
  ServicePatch,
  ServicePayload,
  ServicePricesPayload,
  Template,
  TemplateApplyPayload,
  TemplateApplyResult,
  TemplateKindCode,
} from "@/types/api";

/**
 * Servicios, categorías y plantillas (FASE-4-CONTRATO §4 y §11.5, PR 4.3).
 * Lectura: cualquier usuario. Escritura: `CATALOGO_EDITAR`; precios, `PRECIOS_EDITAR`;
 * crear y anular un servicio piden los dos. Plantillas: solo el dueño.
 */

export const serviceCategoriesApi = crud<
  ServiceCategory,
  ServiceCategoryPayload,
  ServiceCategoryPatch
>("/service-categories");

export interface ServiceListParams extends PageParams {
  /** Búsqueda tolerante (sin tildes, sinónimos, errores de tipeo). Vacío = sin filtro. */
  q?: string;
  category_id?: string;
}

type ServicesApi = Omit<CrudApi<Service, ServicePayload, ServicePatch>, "list"> & {
  list: (params: ServiceListParams) => Promise<PaginatedResponse<Service>>;
  /** Los precios de TODOS los tamaños vivos de una vez (A8); opcionalmente cambia la modalidad. */
  replacePrices: (id: string, data: ServicePricesPayload) => Promise<Service>;
};

const base = crud<Service, ServicePayload, ServicePatch>("/services");

export const servicesApi: ServicesApi = {
  ...base,
  list(params) {
    // Un `q` vacío o un filtro sin elegir no viajan: el backend los trataría como "todo", pero ensucian la URL.
    const clean: ServiceListParams = { limit: params.limit, offset: params.offset };
    if (params.q?.trim()) clean.q = params.q.trim();
    if (params.category_id) clean.category_id = params.category_id;
    return base.list(clean);
  },
  async replacePrices(id, data) {
    const res = await api.put<Service>(`/services/${id}/prices`, data);
    return res.data;
  },
};

/** Términos de la plantilla con `already_added` por término. */
export async function getTemplate(kind: TemplateKindCode): Promise<Template> {
  const res = await api.get<Template>(`/templates/${kind}`);
  return res.data;
}

/** Idempotente por `glossary_code`: reaplicar devuelve `skipped`, no un 409; por eso sin `Idempotency-Key`. */
export async function applyTemplate(data: TemplateApplyPayload): Promise<TemplateApplyResult> {
  const res = await api.post<TemplateApplyResult>("/templates/apply", data);
  return res.data;
}
