"use client";

import { useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";

import { SmartTable, type SmartColumn } from "@/components/data/SmartTable";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { configKeys, useCatalogPage, type CatalogName } from "@/features/configuracion/queries";
import { apiErrorCode, configError, FormSubmitError } from "@/lib/errors";
import type { PageParams } from "@/services/team.service";
import { toast } from "@/stores/toastStore";
import type { PaginatedResponse } from "@/types/api";

const PAGE_SIZE = 25;

/** Textos de cada catálogo. `name(row)` es como se nombra una fila en títulos y botones. */
export interface CatalogCopy<T> {
  /** Nombre accesible de la tabla. */
  tableLabel: string;
  newLabel: string;
  newTitle: string;
  editTitle: (row: T) => string;
  dialogDescription: string;
  emptyMessage: string;
  exportFilename: string;
  /** "el puesto Box 1": va en "Editar …" y "Borrar …". */
  rowName: (row: T) => string;
  deleteTitle: (row: T) => string;
  deleteDescription: string;
  created: string;
  deleted: (row: T) => string;
  replayed: string;
}

export interface CatalogService<T, Payload, Patch> {
  list: (params: PageParams) => Promise<PaginatedResponse<T>>;
  create: (data: Payload, idempotencyKey?: string) => Promise<T>;
  update: (id: string, data: Patch) => Promise<T>;
  remove: (id: string) => Promise<void>;
}

interface CatalogTabProps<T extends { id: string }, Payload extends Patch, Patch> {
  catalog: CatalogName;
  service: CatalogService<T, Payload, Patch>;
  columns: SmartColumn<T>[];
  copy: CatalogCopy<T>;
  /** El formulario de alta (`row` vacío) o edición. */
  renderForm: (props: {
    row?: T;
    onSubmit: (payload: Payload) => Promise<void>;
    onCancel: () => void;
  }) => ReactNode;
  /** `"hours"` para las franjas: un 422 es la franja que cierra antes de abrir. */
  errorScope?: "hours";
  /** Orden dentro de la página (el backend ya ordena; esto lo asegura en pantalla). */
  sortItems?: (a: T, b: T) => number;
}

type DialogState<T> =
  | { kind: "create"; idempotencyKey: string }
  | { kind: "edit"; row: T }
  | { kind: "delete"; row: T }
  | null;

/**
 * Pestaña de un catálogo de operación: tabla paginada por el servidor, alta y
 * edición en un diálogo, borrado con confirmación. Mismo patrón que
 * `equipo/ProfilesTab.tsx`; los cuatro catálogos solo cambian columnas,
 * formulario y textos.
 */
export function CatalogTab<T extends { id: string }, Payload extends Patch, Patch>({
  catalog,
  service,
  columns,
  copy,
  renderForm,
  errorScope,
  sortItems,
}: CatalogTabProps<T, Payload, Patch>) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState({ limit: PAGE_SIZE, offset: 0 });
  const [dialog, setDialog] = useState<DialogState<T>>(null);
  const query = useCatalogPage(catalog, page, service.list);

  const close = () => setDialog(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: configKeys.catalog(catalog) });
  const data =
    query.data && sortItems
      ? { ...query.data, items: [...query.data.items].sort(sortItems) }
      : query.data;

  async function save(values: Payload, current?: T, idempotencyKey?: string) {
    let replay = false;
    try {
      if (current) await service.update(current.id, values);
      else await service.create(values, idempotencyKey);
    } catch (e) {
      if (current || apiErrorCode(e) !== "DUPLICATE_IDEMPOTENT") {
        throw new FormSubmitError(configError(e, errorScope));
      }
      replay = true;
    }
    await refresh();
    close();
    if (replay) toast.info(copy.replayed);
    else toast.success(current ? "Cambios guardados." : copy.created);
  }

  async function remove(row: T) {
    try {
      await service.remove(row.id);
    } catch (e) {
      // 409 RESOURCE_IN_USE: el mensaje queda en el diálogo.
      throw new Error(configError(e).message);
    }
    // Si era la única fila de una página que no es la primera, volver a la anterior: si no,
    // la tabla queda en una página vacía y parece que no hay nada cargado.
    if (data && data.items.length === 1 && page.offset > 0) {
      setPage((p) => ({ ...p, offset: Math.max(0, p.offset - p.limit) }));
    }
    await refresh();
    toast.success(copy.deleted(row));
  }

  const formOpen = dialog?.kind === "create" || dialog?.kind === "edit";

  return (
    <>
      <SmartTable
        label={copy.tableLabel}
        columns={columns}
        page={data}
        isLoading={query.isLoading}
        error={query.isError ? configError(query.error).message : null}
        getRowId={(row) => row.id}
        onPageChange={(offset) => setPage((p) => ({ ...p, offset }))}
        emptyMessage={copy.emptyMessage}
        exportFilename={copy.exportFilename}
        toolbarActions={
          <Button
            type="button"
            onClick={() => setDialog({ kind: "create", idempotencyKey: crypto.randomUUID() })}
          >
            <Plus aria-hidden="true" />
            {copy.newLabel}
          </Button>
        }
        renderActions={(row) => (
          <>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Editar ${copy.rowName(row)}`}
              onClick={() => setDialog({ kind: "edit", row })}
            >
              <Pencil />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Borrar ${copy.rowName(row)}`}
              onClick={() => setDialog({ kind: "delete", row })}
            >
              <Trash2 />
            </Button>
          </>
        )}
      />

      <Dialog open={formOpen} onOpenChange={(open) => !open && close()}>
        <DialogContent>
          {(dialog?.kind === "create" || dialog?.kind === "edit") && (
            <>
              <DialogHeader>
                <DialogTitle>
                  {dialog.kind === "edit" ? copy.editTitle(dialog.row) : copy.newTitle}
                </DialogTitle>
                <DialogDescription>{copy.dialogDescription}</DialogDescription>
              </DialogHeader>
              {renderForm({
                row: dialog.kind === "edit" ? dialog.row : undefined,
                onSubmit: (v) =>
                  dialog.kind === "edit"
                    ? save(v, dialog.row)
                    : save(v, undefined, dialog.idempotencyKey),
                onCancel: close,
              })}
            </>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={dialog?.kind === "delete"}
        onOpenChange={(open) => !open && close()}
        title={dialog?.kind === "delete" ? copy.deleteTitle(dialog.row) : ""}
        description={copy.deleteDescription}
        confirmLabel="Borrar"
        variant="destructive"
        onConfirm={() => (dialog?.kind === "delete" ? remove(dialog.row) : undefined)}
      />
    </>
  );
}
