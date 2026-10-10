"use client";

import { useId, useState, type ReactNode } from "react";
import { ChevronLeft, ChevronRight, Download, SlidersHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { downloadCSV } from "@/lib/csv";

/**
 * Tabla de listados. Portada de Véktor (`components/ui/SmartTable.tsx`) con el
 * cambio que pide el contrato (Y13, A14): **la paginación es del servidor**.
 *
 * En Véktor la tabla recibía TODAS las filas y filtraba, buscaba y paginaba en
 * el cliente: con 10.000 lavados eso es bajar 10.000 lavados para mostrar 25.
 * Acá recibe una página (`PaginatedResponse`: `items`, `total`, `limit`,
 * `offset`) y avisa con `onPageChange(offset)` / `onLimitChange(limit)`; quien
 * la usa pide la página siguiente al backend. Por lo mismo no hay buscador
 * propio: buscar en una página sola mentiría ("sin resultados" cuando el dato
 * está en la página 3). Los filtros van afuera y viajan como query al backend.
 *
 * Se conservan: columnas ocultables, columna de acciones y exportación CSV.
 * "Exportar esta página" baja lo que se ve (columnas visibles, filas de la
 * página); "exportar todo lo filtrado" lo generará el backend (A14, F7).
 */
export interface SmartColumn<T> {
  key: string;
  header: string;
  /** Contenido de la celda. Sin `cell`, se muestra `row[key]`. */
  cell?: (row: T) => ReactNode;
  /** Valor para el CSV. Sin `csvValue`, `row[key]`. Obligatorio si `cell` dibuja algo que no es texto. */
  csvValue?: (row: T) => string | number | null | undefined;
  /** `false`: no aparece en el selector de columnas (siempre visible). */
  hideable?: boolean;
  /** `false`: arranca oculta. */
  defaultVisible?: boolean;
  /** `false`: no va al CSV (p. ej. una columna de íconos). */
  exportable?: boolean;
  className?: string;
}

/** La forma de `PaginatedResponse` del backend (ADR-0006). */
export interface TablePage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

interface SmartTableProps<T> {
  columns: SmartColumn<T>[];
  /** Página actual. `undefined` mientras carga la primera vez. */
  page: TablePage<T> | undefined;
  getRowId: (row: T) => string;
  onPageChange: (offset: number) => void;
  /** Sin esto no se ofrece elegir filas por página. */
  onLimitChange?: (limit: number) => void;
  isLoading?: boolean;
  /** Mensaje de error de la query, si falló. */
  error?: string | null;
  emptyMessage?: string;
  /** Nombre base del archivo, sin fecha ni extensión. */
  exportFilename?: string;
  renderActions?: (row: T) => ReactNode;
  /** Acciones a la izquierda de la barra (p. ej. "Nuevo empleado"). */
  toolbarActions?: ReactNode;
  /** Nombre accesible de la tabla. */
  label: string;
}

export const PAGE_SIZE_OPTIONS = [25, 50, 100] as const;

function rawValue<T>(row: T, key: string): unknown {
  return (row as Record<string, unknown>)[key];
}

function cellText(value: unknown): ReactNode {
  if (value == null || value === "") return <span className="text-muted-foreground">—</span>;
  return String(value);
}

export function SmartTable<T>({
  columns,
  page,
  getRowId,
  onPageChange,
  onLimitChange,
  isLoading = false,
  error = null,
  emptyMessage = "No hay nada para mostrar.",
  exportFilename = "carwash-export",
  renderActions,
  toolbarActions,
  label,
}: SmartTableProps<T>) {
  const pickerId = useId();
  const [pickerOpen, setPickerOpen] = useState(false);
  const [visibleKeys, setVisibleKeys] = useState<Set<string>>(
    () => new Set(columns.filter((c) => c.defaultVisible !== false).map((c) => c.key)),
  );

  const visibleColumns = columns.filter((c) => visibleKeys.has(c.key));
  const hideableColumns = columns.filter((c) => c.hideable !== false);
  const items = page?.items ?? [];
  const total = page?.total ?? 0;
  const limit = page?.limit ?? PAGE_SIZE_OPTIONS[0];
  const offset = page?.offset ?? 0;
  const rangeStart = items.length === 0 ? 0 : offset + 1;
  const rangeEnd = offset + items.length;
  const hasPrev = offset > 0;
  const hasNext = offset + items.length < total;

  function toggleColumn(key: string) {
    setVisibleKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        // Nunca cero columnas: una tabla vacía de columnas no se puede volver a configurar a ciegas.
        if (visibleColumns.length > 1) next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }

  function exportPage() {
    const exportColumns = visibleColumns.filter((c) => c.exportable !== false);
    downloadCSV(
      exportFilename,
      exportColumns.map((c) => c.header),
      items.map((row) =>
        exportColumns.map((c) => (c.csvValue ? c.csvValue(row) : rawValue(row, c.key))),
      ),
    );
  }

  const colSpan = visibleColumns.length + (renderActions ? 1 : 0);

  let body: ReactNode;
  if (error) {
    body = (
      <TableRow>
        <TableCell colSpan={colSpan} className="py-8 text-center text-destructive" role="alert">
          {error}
        </TableCell>
      </TableRow>
    );
  } else if (isLoading && !page) {
    body = (
      <TableRow>
        <TableCell colSpan={colSpan} className="py-8 text-center text-muted-foreground">
          Cargando…
        </TableCell>
      </TableRow>
    );
  } else if (items.length === 0) {
    body = (
      <TableRow>
        <TableCell colSpan={colSpan} className="py-8 text-center text-muted-foreground">
          {emptyMessage}
        </TableCell>
      </TableRow>
    );
  } else {
    body = items.map((row) => (
      <TableRow key={getRowId(row)}>
        {visibleColumns.map((c) => (
          <TableCell key={c.key} className={c.className}>
            {c.cell ? c.cell(row) : cellText(rawValue(row, c.key))}
          </TableCell>
        ))}
        {renderActions && (
          <TableCell className="sticky right-0 bg-background text-right">
            <div className="flex justify-end gap-1">{renderActions(row)}</div>
          </TableCell>
        )}
      </TableRow>
    ));
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2">{toolbarActions}</div>
        <div className="flex flex-wrap items-center gap-2">
          {hideableColumns.length > 1 && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-expanded={pickerOpen}
              aria-controls={pickerId}
              onClick={() => setPickerOpen((v) => !v)}
            >
              <SlidersHorizontal aria-hidden="true" />
              Columnas
            </Button>
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={exportPage}
            disabled={items.length === 0}
          >
            <Download aria-hidden="true" />
            Exportar esta página
          </Button>
        </div>
      </div>

      {pickerOpen && (
        <fieldset id={pickerId} className="rounded-lg border bg-card p-3">
          <legend className="px-1 text-xs font-semibold text-muted-foreground">
            Mostrar columnas
          </legend>
          <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2 md:grid-cols-3">
            {hideableColumns.map((c) => (
              <label key={c.key} className="flex min-h-touch items-center gap-3 text-sm">
                <Checkbox
                  checked={visibleKeys.has(c.key)}
                  onCheckedChange={() => toggleColumn(c.key)}
                />
                {c.header}
              </label>
            ))}
          </div>
        </fieldset>
      )}

      <div className="rounded-xl border bg-card">
        <Table aria-label={label} aria-busy={isLoading}>
          <TableHeader>
            <TableRow>
              {visibleColumns.map((c) => (
                <TableHead key={c.key} scope="col" className={c.className}>
                  {c.header}
                </TableHead>
              ))}
              {renderActions && (
                <TableHead scope="col" className="sticky right-0 bg-card text-right">
                  <span className="sr-only">Acciones</span>
                </TableHead>
              )}
            </TableRow>
          </TableHeader>
          <TableBody>{body}</TableBody>
        </Table>
      </div>

      {total > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
          <p aria-live="polite">
            Mostrando {rangeStart}–{rangeEnd} de {total}
          </p>
          <div className="flex items-center gap-2">
            {onLimitChange && (
              <label className="flex items-center gap-2">
                Filas por página
                <select
                  value={limit}
                  onChange={(e) => onLimitChange(Number(e.target.value))}
                  className="min-h-touch rounded-md border border-input bg-background px-2 text-base"
                >
                  {PAGE_SIZE_OPTIONS.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <Button
              type="button"
              variant="outline"
              size="icon"
              aria-label="Página anterior"
              disabled={!hasPrev}
              onClick={() => onPageChange(Math.max(0, offset - limit))}
            >
              <ChevronLeft />
            </Button>
            <Button
              type="button"
              variant="outline"
              size="icon"
              aria-label="Página siguiente"
              disabled={!hasNext}
              onClick={() => onPageChange(offset + limit)}
            >
              <ChevronRight />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
