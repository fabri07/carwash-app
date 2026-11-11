"use client";

import { useState, type ReactNode } from "react";
import { useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { Eye, EyeOff, Pencil, Plus } from "lucide-react";

import { SmartTable, type SmartColumn } from "@/components/data/SmartTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { vehicleKeys } from "@/features/vehiculos/queries";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { capitalize, useCatalogEditor, useStepBackOnEmptyPage } from "@/hooks/useCatalogEditor";
import { configError } from "@/lib/errors";
import type { PaginatedResponse } from "@/types/api";
import { toast } from "@/stores/toastStore";

/** Lo común a marcas, modelos y colores. */
export interface CatalogRow {
  id: string;
  name: string;
  is_custom: boolean;
  hidden_at?: string | null;
}

export interface TableParams {
  limit: number;
  offset: number;
  q: string;
  include_hidden: boolean;
  /** Filtro externo de los modelos: la marca elegida. */
  make_id?: string;
}

const PAGE_SIZE = 25;
const SEARCH_DELAY_MS = 300;

interface VehicleCatalogTableProps<R extends CatalogRow, F> {
  /** "Marcas": nombre accesible de la tabla. */
  title: string;
  /** "marca": para botones y mensajes. */
  singular: string;
  feminine?: boolean;
  searchPlaceholder: string;
  emptyMessage: string;
  exportFilename: string;
  /** El hook de la query; recibe los filtros de la tabla (búsqueda, ocultas, página). */
  useRows: (params: TableParams) => UseQueryResult<PaginatedResponse<R>>;
  /**
   * Filtros que vienen de afuera de la tabla (la marca de los modelos). Un `include_hidden: true`
   * acá pisa el interruptor (los modelos de una marca oculta se ven siempre).
   */
  scope?: Partial<Pick<TableParams, "make_id" | "include_hidden">>;
  /** Columnas además del nombre y el estado. */
  extraColumns?: SmartColumn<R>[];
  /** Filtros además de la búsqueda (p. ej. la marca de los modelos). */
  toolbarExtra?: ReactNode;
  /** `false` deshabilita "Nuevo" (p. ej. modelos sin marca elegida). */
  canCreate?: boolean;
  create: (values: F, idempotencyKey: string) => Promise<unknown>;
  update: (item: R, values: F) => Promise<unknown>;
  setHidden: (item: R, hidden: boolean) => Promise<unknown>;
  renderForm: (props: {
    item?: R;
    onSubmit: (values: F) => Promise<void>;
    onCancel: () => void;
  }) => ReactNode;
}

/**
 * Administración de una parte del catálogo de vehículos (FASE-4-CONTRATO §4, PR 4.4): tabla
 * paginada del servidor con búsqueda, "mostrar ocultas", alta, renombre y ocultar/mostrar. No hay
 * baja: ocultar saca la fila de los selectores y se puede deshacer.
 */
export function VehicleCatalogTable<R extends CatalogRow, F>({
  title,
  singular,
  feminine = false,
  searchPlaceholder,
  emptyMessage,
  exportFilename,
  useRows,
  extraColumns = [],
  toolbarExtra,
  canCreate = true,
  scope,
  create,
  update,
  setHidden,
  renderForm,
}: VehicleCatalogTableProps<R, F>) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const q = useDebouncedValue(search.trim(), SEARCH_DELAY_MS);
  const [showHidden, setShowHidden] = useState(false);
  // La página vale para los filtros con que se eligió: si cambian, vuelve a la primera.
  const key = `${q}|${showHidden}|${scope?.make_id ?? ""}|${scope?.include_hidden ?? ""}`;
  const [paging, setPaging] = useState({ key, offset: 0 });
  const offset = paging.key === key ? paging.offset : 0;
  const setOffset = (next: number) => setPaging({ key, offset: next });

  const query = useRows({ limit: PAGE_SIZE, offset, q, include_hidden: showHidden, ...scope });
  // Ocultar el último ítem de la última página (sin "mostrar ocultas") la deja vacía: retrocede.
  useStepBackOnEmptyPage(query.data, offset, PAGE_SIZE, setOffset);

  const capital = capitalize(singular);
  const refresh = () => queryClient.invalidateQueries({ queryKey: vehicleKeys.all });
  const { dialog, setDialog, close, openCreate, submitFor } = useCatalogEditor<R, F>({
    singular,
    feminine,
    create,
    update,
    refresh,
  });

  const columns: SmartColumn<R>[] = [
    {
      key: "name",
      header: "Nombre",
      hideable: false,
      className: "font-medium",
      cell: (r) => (
        <div className="flex flex-wrap items-center gap-2">
          <span>{r.name}</span>
          {r.is_custom && <Badge variant="outline">{feminine ? "Propia" : "Propio"}</Badge>}
          {r.hidden_at && <Badge variant="secondary">{feminine ? "Oculta" : "Oculto"}</Badge>}
        </div>
      ),
      csvValue: (r) => r.name,
    },
    ...extraColumns,
    {
      key: "origin",
      header: "Origen",
      defaultVisible: false,
      cell: (r) => (r.is_custom ? "Agregado por el negocio" : "Base del mercado"),
      csvValue: (r) => (r.is_custom ? "propio" : "base"),
    },
  ];

  async function toggle(item: R) {
    const hide = !item.hidden_at;
    try {
      await setHidden(item, hide);
    } catch (e) {
      toast.error(configError(e).message);
      return;
    }
    await refresh();
    toast.success(
      hide
        ? `${capital} ${feminine ? "oculta" : "oculto"}: ya no aparece al cargar vehículos.`
        : `${capital} visible de nuevo.`,
    );
  }

  const filtering = q !== "";
  return (
    <>
      <SmartTable
        label={title}
        columns={columns}
        page={query.data}
        isLoading={query.isLoading}
        error={query.isError ? configError(query.error).message : null}
        getRowId={(r) => r.id}
        onPageChange={setOffset}
        emptyMessage={filtering ? `Ningún resultado para "${q}".` : emptyMessage}
        exportFilename={exportFilename}
        toolbarActions={
          <>
            {toolbarExtra}
            <Input
              type="search"
              aria-label={`Buscar en ${title.toLowerCase()}`}
              placeholder={searchPlaceholder}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full sm:w-60"
            />
            <div className="flex min-h-touch items-center gap-2">
              <Switch
                id={`ocultas-${exportFilename}`}
                checked={showHidden}
                onCheckedChange={setShowHidden}
              />
              <Label htmlFor={`ocultas-${exportFilename}`}>
                {feminine ? "Mostrar ocultas" : "Mostrar ocultos"}
              </Label>
            </div>
            <Button type="button" disabled={!canCreate} onClick={openCreate}>
              <Plus aria-hidden="true" />
              {`${feminine ? "Nueva" : "Nuevo"} ${singular}`}
            </Button>
          </>
        }
        renderActions={(r) => (
          <>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Editar ${singular} ${r.name}`}
              onClick={() => setDialog({ kind: "edit", item: r })}
            >
              <Pencil />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`${r.hidden_at ? "Mostrar" : "Ocultar"} ${singular} ${r.name}`}
              onClick={() => toggle(r)}
            >
              {r.hidden_at ? <Eye /> : <EyeOff />}
            </Button>
          </>
        )}
      />

      <Dialog
        open={dialog?.kind === "create" || dialog?.kind === "edit"}
        onOpenChange={(open) => !open && close()}
      >
        <DialogContent>
          {(dialog?.kind === "create" || dialog?.kind === "edit") && (
            <>
              <DialogHeader>
                <DialogTitle>
                  {dialog.kind === "edit"
                    ? `Editar ${singular} ${dialog.item.name}`
                    : `${feminine ? "Nueva" : "Nuevo"} ${singular}`}
                </DialogTitle>
                <DialogDescription>
                  Rige para este negocio solamente; los vehículos ya cargados no cambian.
                </DialogDescription>
              </DialogHeader>
              {renderForm({
                item: dialog.kind === "edit" ? dialog.item : undefined,
                onSubmit: submitFor(dialog),
                onCancel: close,
              })}
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
