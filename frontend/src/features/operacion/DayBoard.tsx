"use client";

import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { localTime } from "@/lib/agenda";
import { formatARS } from "@/lib/money";
import { cn } from "@/lib/utils";
import { entryStatus, owes } from "./status";
import { TOUCH_TARGET } from "./touch";
import type { DayEntry, DayResource } from "./types";

/** Dónde se abre un renglón: el job ya existente, o la pantalla del turno (que registra la llegada). */
export function entryHref(entry: DayEntry, date: string): string {
  return entry.job_id
    ? `/operacion/${entry.job_id}`
    : `/operacion/turno/${entry.booking_id}?date=${date}`;
}

function EntryCard({ entry, date, timeZone }: { entry: DayEntry; date: string; timeZone: string }) {
  const status = entryStatus(entry);
  return (
    <Link
      href={entryHref(entry, date)}
      className={cn(
        TOUCH_TARGET,
        "block min-h-16 overflow-hidden rounded-lg border bg-card p-3 shadow-sm active:bg-accent",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-base font-semibold tabular-nums">
          {localTime(entry.start_at, timeZone)}–{localTime(entry.end_at, timeZone)}
        </span>
        <Badge className={status.tone}>{status.label}</Badge>
      </div>
      <p className="mt-1 break-words text-sm">{entry.services.join(" + ") || "Sin servicio"}</p>
      <p className="break-words text-sm text-muted-foreground">
        {entry.customer?.name ?? "Sin nombre"}
        {entry.vehicle_plate ? ` · ${entry.vehicle_plate}` : ""}
      </p>
      {owes(entry) && (
        <p className="mt-1 text-sm font-bold text-destructive">
          Debe {formatARS(entry.balance_due_cents ?? 0)}
        </p>
      )}
      {entry.entry_mode === "RETROSPECTIVA" && (
        <p className="mt-1 text-xs text-muted-foreground">Carga manual</p>
      )}
    </Link>
  );
}

/** Los renglones de una columna agrupados por hora de inicio (en la zona del negocio). */
function groupByHour(entries: DayEntry[], timeZone: string): [string, DayEntry[]][] {
  const sorted = [...entries].sort((a, b) => a.start_at.localeCompare(b.start_at));
  const groups = new Map<string, DayEntry[]>();
  for (const e of sorted) {
    const hour = `${localTime(e.start_at, timeZone).slice(0, 2)}:00`;
    groups.set(hour, [...(groups.get(hour) ?? []), e]);
  }
  return [...groups.entries()];
}

/**
 * Agenda del día en columnas por puesto (D6-1): una columna por puesto y los renglones como
 * bloques por hora. En el celular cada columna ocupa casi todo el ancho y se desliza de lado; los
 * renglones sin puesto van en su propia columna "Sin puesto".
 */
export function DayBoard({
  resources,
  entries,
  date,
  timeZone,
}: {
  resources: DayResource[];
  entries: DayEntry[];
  date: string;
  timeZone: string;
}) {
  const columns: { id: string; name: string; entries: DayEntry[] }[] = resources.map((r) => ({
    id: r.id,
    name: r.name,
    entries: entries.filter((e) => e.resource_id === r.id),
  }));
  const loose = entries.filter(
    (e) => e.resource_id === null || !resources.some((r) => r.id === e.resource_id),
  );
  if (loose.length > 0) columns.push({ id: "none", name: "Sin puesto", entries: loose });

  return (
    <div
      className="-mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 pb-2 sm:mx-0 sm:px-0"
      data-testid="day-board"
    >
      {columns.map((col) => (
        <section
          key={col.id}
          aria-label={`Puesto ${col.name}`}
          className="w-[88%] shrink-0 snap-start space-y-3 sm:w-72"
        >
          <h2 className="sticky top-0 rounded-md bg-muted px-3 py-2 text-sm font-semibold">
            {col.name}
          </h2>
          {col.entries.length === 0 && (
            <p className="px-1 text-sm text-muted-foreground">Sin turnos ni trabajos.</p>
          )}
          {groupByHour(col.entries, timeZone).map(([hour, group]) => (
            <div key={hour} className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground">{hour}</p>
              {group.map((e) => (
                <EntryCard key={`${e.kind}-${e.id}`} entry={e} date={date} timeZone={timeZone} />
              ))}
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
