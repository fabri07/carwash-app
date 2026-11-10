"use client";

import { useState, type ComponentType } from "react";
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
import { configKeys, useCatalogPage } from "@/features/configuracion/queries";
import { capitalize, useCatalogEditor, useStepBackOnEmptyPage } from "@/hooks/useCatalogEditor";
import { configError } from "@/lib/errors";
import type { CrudApi } from "@/services/config.service";
import { toast } from "@/stores/toastStore";

/** Props que recibe el formulario de cada recurso. */
export interface CatalogFormProps<R, F> {
  item?: R;
  onSubmit: (values: F) => Promise<void>;
  onCancel: () => void;
}

interface CatalogTabProps<R extends { id: string }, F, C, U> {
  /** Clave de la query y base del CSV, p. ej. `"resources"`. */
  resource: string;
  /** "Puestos": nombre accesible de la tabla. */
  title: string;
  /** "puesto": para los mensajes ("Puesto creado."). */
  singular: string;
  /** Género del sustantivo, para "creado" / "creada". */
  feminine?: boolean;
  api: CrudApi<R, C, U>;
  columns: SmartColumn<R>[];
  emptyMessage: string;
  exportFilename: string;
  itemName: (item: R) => string;
  toCreate: (values: F) => C;
  toPatch: (values: F, item: R) => U;
  Form: ComponentType<CatalogFormProps<R, F>>;
  deleteHint: string;
  /** Otras cachés que dependen de este recurso (p. ej. vehículos, de los tamaños). */
  alsoInvalidates?: readonly (readonly unknown[])[];
}

const PAGE_SIZE = 25;

/**
 * Pestaña de un recurso del catálogo (tamaños, puestos, franjas, medios de
 * pago): tabla paginada del servidor + alta/edición en diálogo + baja con
 * confirmación. Lo que cambia entre recursos (columnas, formulario y cómo se arma
 * el payload) lo pasa cada pestaña.
 */
export function CatalogTab<R extends { id: string }, F, C, U>({
  resource,
  title,
  singular,
  feminine = false,
  api,
  columns,
  emptyMessage,
  exportFilename,
  itemName,
  toCreate,
  toPatch,
  Form,
  deleteHint,
  alsoInvalidates = [],
}: CatalogTabProps<R, F, C, U>) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState({ limit: PAGE_SIZE, offset: 0 });
  const query = useCatalogPage(resource, api, page);
  useStepBackOnEmptyPage(query.data, page.offset, page.limit, (offset) =>
    setPage((p) => ({ ...p, offset })),
  );

  const capital = capitalize(singular);
  const refresh = () =>
    Promise.all(
      [configKeys.resource(resource), ...alsoInvalidates].map((queryKey) =>
        queryClient.invalidateQueries({ queryKey }),
      ),
    );
  const { dialog, setDialog, close, openCreate, submitFor } = useCatalogEditor<R, F>({
    singular,
    feminine,
    create: (values, idempotencyKey) => api.create(toCreate(values), idempotencyKey),
    update: (item, values) => api.update(item.id, toPatch(values, item)),
    refresh,
  });

  async function remove(item: R) {
    try {
      await api.remove(item.id);
    } catch (e) {
      // 409 IN_USE: el mensaje queda en el diálogo.
      throw new Error(configError(e).message);
    }
    await refresh();
    toast.success(`${capital} ${feminine ? "quitada" : "quitado"}.`);
  }

  return (
    <>
      <SmartTable
        label={title}
        columns={columns}
        page={query.data}
        isLoading={query.isLoading}
        error={query.isError ? configError(query.error).message : null}
        getRowId={(r) => r.id}
        onPageChange={(offset) => setPage((p) => ({ ...p, offset }))}
        emptyMessage={emptyMessage}
        exportFilename={exportFilename}
        toolbarActions={
          <Button type="button" onClick={openCreate}>
            <Plus aria-hidden="true" />
            {`${feminine ? "Nueva" : "Nuevo"} ${singular}`}
          </Button>
        }
        renderActions={(r) => (
          <>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Editar ${singular} ${itemName(r)}`}
              onClick={() => setDialog({ kind: "edit", item: r })}
            >
              <Pencil />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Quitar ${singular} ${itemName(r)}`}
              onClick={() => setDialog({ kind: "delete", item: r })}
            >
              <Trash2 />
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
                    ? `Editar ${singular} ${itemName(dialog.item)}`
                    : `${feminine ? "Nueva" : "Nuevo"} ${singular}`}
                </DialogTitle>
                <DialogDescription>
                  Los cambios rigen desde la próxima vez que se use.
                </DialogDescription>
              </DialogHeader>
              <Form
                item={dialog.kind === "edit" ? dialog.item : undefined}
                onSubmit={submitFor(dialog)}
                onCancel={close}
              />
            </>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={dialog?.kind === "delete"}
        onOpenChange={(open) => !open && close()}
        title={dialog?.kind === "delete" ? `¿Quitar ${singular} ${itemName(dialog.item)}?` : ""}
        description={deleteHint}
        confirmLabel="Quitar"
        variant="destructive"
        onConfirm={() => (dialog?.kind === "delete" ? remove(dialog.item) : undefined)}
      />
    </>
  );
}
