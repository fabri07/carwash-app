"use client";

import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { DollarSign, Pencil, Plus, Trash2 } from "lucide-react";

import { SmartTable, type SmartColumn } from "@/components/data/SmartTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import {
  depositLabel,
  durationSummary,
  MODE_LABELS,
  priceSummary,
} from "@/features/catalogo/format";
import {
  catalogKeys,
  useAllCategories,
  useAllVehicleSizes,
  useServices,
} from "@/features/catalogo/queries";
import { ServiceEditForm } from "@/features/catalogo/ServiceEditForm";
import { ServiceForm } from "@/features/catalogo/ServiceForm";
import { ServicePricesForm } from "@/features/catalogo/ServicePricesForm";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useCan } from "@/hooks/usePermission";
import { apiErrorCode, FormSubmitError, serviceError } from "@/lib/errors";
import { useSettings } from "@/features/configuracion/queries";
import { bpsToPercent } from "@/lib/percent";
import { servicesApi } from "@/services/catalog.service";
import { toast } from "@/stores/toastStore";
import type { Service } from "@/types/api";
import {
  toDepositBps,
  toPriceItems,
  type ServiceCreateInput,
  type ServiceEditInput,
  type ServicePricesInput,
} from "@/validation/services";

type DialogState =
  | { kind: "create"; idempotencyKey: string }
  | { kind: "edit"; item: Service }
  | { kind: "prices"; item: Service }
  | { kind: "delete"; item: Service }
  | null;

const PAGE_SIZE = 25;
const SEARCH_DELAY_MS = 300;

/**
 * Servicios del negocio: tabla paginada del servidor con búsqueda (`?q=`, con debounce) y filtro por
 * categoría. Editar nombre y notas pide `CATALOGO_EDITAR`; los precios, `PRECIOS_EDITAR`; crear y
 * anular piden los dos (FASE-4-CONTRATO §2.3). Ocultar botones no es autorizar: el backend responde 403.
 */
export function ServicesTab() {
  const can = useCan();
  const canCatalog = can("CATALOGO_EDITAR");
  const canPrices = can("PRECIOS_EDITAR");
  const queryClient = useQueryClient();

  const [search, setSearch] = useState("");
  const q = useDebouncedValue(search.trim(), SEARCH_DELAY_MS);
  const [categoryId, setCategoryId] = useState("");
  // La página vale solo para la búsqueda y categoría con que se eligió: si cambian (el `q` ya con
  // debounce, no al tipear) se vuelve a la primera sin pedir antes la página vieja con el filtro nuevo.
  const filterKey = `${q}|${categoryId}`;
  const [paging, setPaging] = useState({ key: filterKey, offset: 0 });
  const offset = paging.key === filterKey ? paging.offset : 0;
  const setOffset = (next: number) => setPaging({ key: filterKey, offset: next });
  const [dialog, setDialog] = useState<DialogState>(null);

  const params = { limit: PAGE_SIZE, offset, q, category_id: categoryId };
  const query = useServices(params);
  const categoriesQuery = useAllCategories();
  const sizesQuery = useAllVehicleSizes();
  // Para avisar en la tabla si la seña está apagada (la lectura no pide permiso).
  const depositEnabled = useSettings().data?.deposit_enabled;
  const categories = useMemo(() => categoriesQuery.data?.items ?? [], [categoriesQuery.data]);
  const sizes = useMemo(() => sizesQuery.data?.items ?? [], [sizesQuery.data]);
  const categoryName = useMemo(() => new Map(categories.map((c) => [c.id, c.name])), [categories]);

  // Quitar el último ítem de la última página la deja vacía: retrocede a la última que tenga datos.
  const loaded = query.data;
  useEffect(() => {
    if (loaded && loaded.items.length === 0 && loaded.total > 0 && offset > 0) {
      setOffset(Math.floor((loaded.total - 1) / PAGE_SIZE) * PAGE_SIZE);
    }
  }, [loaded, offset]);

  const close = () => setDialog(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: catalogKeys.services });

  const columns: SmartColumn<Service>[] = [
    {
      key: "name",
      header: "Nombre",
      hideable: false,
      className: "font-medium",
      cell: (s) => (
        <div className="flex flex-wrap items-center gap-2">
          <span>{s.name}</span>
          {s.missing_price_size_ids.length > 0 && (
            <Badge variant="destructive">Faltan precios</Badge>
          )}
        </div>
      ),
      csvValue: (s) => s.name,
    },
    {
      key: "category",
      header: "Categoría",
      cell: (s) => (s.category_id ? (categoryName.get(s.category_id) ?? "—") : "Sin categoría"),
      csvValue: (s) => (s.category_id ? (categoryName.get(s.category_id) ?? "") : ""),
    },
    {
      key: "pricing_mode",
      header: "Modalidad",
      cell: (s) => MODE_LABELS[s.pricing_mode],
      csvValue: (s) => MODE_LABELS[s.pricing_mode],
    },
    {
      key: "prices",
      header: "Precios",
      className: "tabular-nums",
      cell: (s) => priceSummary(s),
      csvValue: (s) => priceSummary(s),
    },
    {
      key: "duration",
      header: "Duración",
      cell: (s) => durationSummary(s),
      csvValue: (s) => durationSummary(s),
    },
    {
      key: "deposit",
      header: "Seña",
      cell: (s) => depositLabel(s, depositEnabled),
      // Número en el CSV (como la comisión): vacío = usa la seña general.
      csvValue: (s) => (s.deposit_bps == null ? "" : bpsToPercent(s.deposit_bps)),
    },
    { key: "notes", header: "Notas", defaultVisible: false },
  ];

  async function afterSave(message: string) {
    await refresh();
    close();
    toast.success(message);
  }

  async function create(values: ServiceCreateInput, idempotencyKey: string) {
    try {
      await servicesApi.create(
        {
          name: values.name,
          pricing_mode: values.pricing_mode,
          notes: values.notes === "" ? null : values.notes,
          category_id: values.category_id === "" ? null : values.category_id,
          deposit_bps: toDepositBps(values.deposit_percent),
          prices: toPriceItems(values.prices),
        },
        idempotencyKey,
      );
    } catch (e) {
      if (apiErrorCode(e) !== "DUPLICATE_IDEMPOTENT") {
        throw new FormSubmitError(serviceError(e, "prices"));
      }
      await refresh();
      close();
      toast.info("Ese servicio ya se había creado. Revisá en la lista cómo quedó.");
      return;
    }
    await afterSave("Servicio creado.");
  }

  async function edit(item: Service, values: ServiceEditInput) {
    try {
      // `null` borra las notas o la categoría; el nombre es lo único que no puede quedar vacío.
      await servicesApi.update(item.id, {
        name: values.name,
        notes: values.notes === "" ? null : values.notes,
        category_id: values.category_id === "" ? null : values.category_id,
      });
    } catch (e) {
      throw new FormSubmitError(serviceError(e));
    }
    await afterSave("Servicio actualizado.");
  }

  async function savePrices(item: Service, values: ServicePricesInput) {
    const deposit = toDepositBps(values.deposit_percent);
    try {
      await servicesApi.replacePrices(item.id, {
        pricing_mode: values.pricing_mode,
        // Solo si cambió: ausente no la toca en el servidor, así un formulario abierto con datos
        // viejos no pisa la seña que otro acaba de fijar. Vacía (`null`) vuelve a la general.
        ...(deposit !== item.deposit_bps && { deposit_bps: deposit }),
        prices: toPriceItems(values.prices),
      });
    } catch (e) {
      throw new FormSubmitError(serviceError(e, "prices"));
    }
    await afterSave("Precios actualizados.");
  }

  async function remove(item: Service) {
    try {
      await servicesApi.remove(item.id);
    } catch (e) {
      // 409 IN_USE: el motivo queda en el diálogo.
      throw new Error(serviceError(e).message);
    }
    await refresh();
    toast.success("Servicio anulado.");
  }

  const noSizes = sizesQuery.isSuccess && sizes.length === 0;
  const filtering = q !== "" || categoryId !== "";

  return (
    <>
      <SmartTable
        label="Servicios"
        columns={columns}
        page={query.data}
        isLoading={query.isLoading}
        error={query.isError ? serviceError(query.error).message : null}
        getRowId={(s) => s.id}
        onPageChange={setOffset}
        emptyMessage={
          filtering
            ? "Ningún servicio coincide con la búsqueda."
            : "Todavía no cargaste servicios. Creá el primero o, si sos el dueño, partí de una plantilla."
        }
        exportFilename="servicios"
        toolbarActions={
          <>
            <Input
              type="search"
              aria-label="Buscar servicios"
              placeholder="Buscar: lavado, cerámico, rayones…"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
              }}
              className="w-full sm:w-72"
            />
            <NativeSelect
              aria-label="Filtrar por categoría"
              value={categoryId}
              onChange={(e) => {
                setCategoryId(e.target.value);
              }}
              className="sm:w-52"
            >
              <option value="">Todas las categorías</option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </NativeSelect>
            {canCatalog && canPrices && (
              <Button
                type="button"
                onClick={() => setDialog({ kind: "create", idempotencyKey: crypto.randomUUID() })}
              >
                <Plus aria-hidden="true" />
                Nuevo servicio
              </Button>
            )}
          </>
        }
        renderActions={(s) => (
          <>
            {canCatalog && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`Editar servicio ${s.name}`}
                onClick={() => setDialog({ kind: "edit", item: s })}
              >
                <Pencil />
              </Button>
            )}
            {canPrices && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`Precios de ${s.name}`}
                onClick={() => setDialog({ kind: "prices", item: s })}
              >
                <DollarSign />
              </Button>
            )}
            {canCatalog && canPrices && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`Anular servicio ${s.name}`}
                onClick={() => setDialog({ kind: "delete", item: s })}
              >
                <Trash2 />
              </Button>
            )}
          </>
        )}
      />

      <Dialog
        open={dialog !== null && dialog.kind !== "delete"}
        onOpenChange={(open) => !open && close()}
      >
        <DialogContent className="max-w-2xl">
          {dialog?.kind === "create" && (
            <>
              <DialogHeader>
                <DialogTitle>Nuevo servicio</DialogTitle>
                <DialogDescription>
                  Cada servicio lleva un precio y una duración por tamaño de vehículo.
                </DialogDescription>
              </DialogHeader>
              {noSizes ? (
                <p role="alert" className="text-sm text-muted-foreground">
                  Antes de crear servicios cargá al menos un tamaño de vehículo en Negocio →
                  Tamaños.
                </p>
              ) : sizesQuery.isSuccess && categoriesQuery.isSuccess ? (
                <ServiceForm
                  sizes={sizes}
                  categories={categories}
                  onSubmit={(v) => create(v, dialog.idempotencyKey)}
                  onCancel={close}
                />
              ) : (
                <p className="text-sm text-muted-foreground">Cargando…</p>
              )}
            </>
          )}
          {dialog?.kind === "edit" && (
            <>
              <DialogHeader>
                <DialogTitle>{`Editar servicio ${dialog.item.name}`}</DialogTitle>
                <DialogDescription>
                  Nombre, categoría y notas. Los precios se cambian aparte.
                </DialogDescription>
              </DialogHeader>
              {categoriesQuery.isSuccess ? (
                <ServiceEditForm
                  service={dialog.item}
                  categories={categories}
                  onSubmit={(v) => edit(dialog.item, v)}
                  onCancel={close}
                />
              ) : (
                <p className="text-sm text-muted-foreground">Cargando…</p>
              )}
            </>
          )}
          {dialog?.kind === "prices" && (
            <>
              <DialogHeader>
                <DialogTitle>{`Precios de ${dialog.item.name}`}</DialogTitle>
                <DialogDescription>
                  Un precio y una duración para cada tamaño de vehículo. Los cambios rigen para los
                  turnos nuevos.
                </DialogDescription>
              </DialogHeader>
              {sizesQuery.isSuccess ? (
                <ServicePricesForm
                  service={dialog.item}
                  sizes={sizes}
                  onSubmit={(v) => savePrices(dialog.item, v)}
                  onCancel={close}
                />
              ) : (
                <p className="text-sm text-muted-foreground">Cargando…</p>
              )}
            </>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={dialog?.kind === "delete"}
        onOpenChange={(open) => !open && close()}
        title={dialog?.kind === "delete" ? `¿Anular el servicio ${dialog.item.name}?` : ""}
        description="Se anula también sus precios. No se puede anular si hay turnos, cotizaciones o trabajos sin cerrar con este servicio."
        confirmLabel="Anular"
        variant="destructive"
        onConfirm={() => (dialog?.kind === "delete" ? remove(dialog.item) : undefined)}
      />
    </>
  );
}
