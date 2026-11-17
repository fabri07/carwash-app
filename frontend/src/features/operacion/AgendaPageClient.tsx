"use client";

import { useState } from "react";
import axios from "axios";
import Link from "next/link";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useCan } from "@/hooks/usePermission";
import { addDays, isIsoDate, todayInZone } from "@/lib/period";
import { cn } from "@/lib/utils";
import { DayBoard } from "./DayBoard";
import { Guard } from "./Guard";
import { QueueBanner } from "./QueueBanner";
import { useBusinessTimeZone, useDay } from "./queries";
import { useNow } from "./useNow";
import { TOUCH_TARGET } from "./touch";

/** "lunes 23/11" de un `YYYY-MM-DD` (el día calendario no tiene zona: se arma en UTC). */
function dayLabel(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`);
  const weekday = new Intl.DateTimeFormat("es-AR", { timeZone: "UTC", weekday: "long" }).format(d);
  return `${weekday} ${iso.slice(8, 10)}/${iso.slice(5, 7)}`;
}

/**
 * `/agenda`: la agenda del día en columnas por puesto (FASE-6-CONTRATO D6-1). Verla pide
 * `AGENDA_VER`. "Hoy" y "ayer" son del negocio (su zona horaria), no del navegador. Los accesos a
 * cargar cosas aparecen según el permiso: el servidor decide igual.
 */
export function AgendaPageClient({ initialDate }: { initialDate?: string }) {
  return (
    <Guard permission="AGENDA_VER" title="la agenda">
      <Agenda initialDate={initialDate} />
    </Guard>
  );
}

function Agenda({ initialDate }: { initialDate?: string }) {
  const can = useCan();
  const timeZone = useBusinessTimeZone();
  // "Hoy" se recalcula con el reloj: al cruzar la medianoche la agenda cambia de día sola.
  const now = useNow(30_000);
  const today = todayInZone(new Date(now), timeZone);
  const [picked, setPicked] = useState<string | null>(
    initialDate && isIsoDate(initialDate) ? initialDate : null,
  );
  const date = picked ?? today;
  const day = useDay(date);
  const yesterday = addDays(today, -1);

  return (
    <section aria-labelledby="agenda-title" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 id="agenda-title" className="text-2xl font-semibold">
          Agenda
        </h1>
        <div className="flex flex-wrap gap-2">
          {can("JOBS_OPERAR") && (
            <Button asChild>
              <Link href="/operacion/nuevo">Ingreso sin turno</Link>
            </Button>
          )}
          {can("TURNOS_GESTIONAR") && (
            <Button asChild variant="outline">
              <Link href="/operacion/turno-a-mano">Turno a mano</Link>
            </Button>
          )}
          {can("JOBS_OPERAR") && (
            <Button asChild variant="outline">
              <Link href="/operacion/trabajo-hecho">Cargar trabajo hecho</Link>
            </Button>
          )}
        </div>
      </div>

      <QueueBanner />

      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Elegir día">
        <Button
          variant="outline"
          size="icon"
          aria-label="Día anterior"
          onClick={() => setPicked(addDays(date, -1))}
        >
          <ChevronLeft />
        </Button>
        <Button
          variant={date === yesterday ? "default" : "outline"}
          onClick={() => setPicked(yesterday)}
        >
          Ayer
        </Button>
        <Button variant={date === today ? "default" : "outline"} onClick={() => setPicked(today)}>
          Hoy
        </Button>
        <Button
          variant="outline"
          size="icon"
          aria-label="Día siguiente"
          onClick={() => setPicked(addDays(date, 1))}
        >
          <ChevronRight />
        </Button>
        <Input
          type="date"
          aria-label="Ir a un día"
          value={date}
          onChange={(e) => isIsoDate(e.target.value) && setPicked(e.target.value)}
          className={cn(TOUCH_TARGET, "w-auto")}
        />
        <p className="w-full text-sm font-medium capitalize text-muted-foreground sm:w-auto">
          {dayLabel(date)}
        </p>
      </div>

      {day.isLoading && <p className="text-sm text-muted-foreground">Cargando la agenda…</p>}
      {day.isError && (
        <p role="alert" className="text-sm text-destructive">
          {axios.isAxiosError(day.error) && day.error.response?.status === 403
            ? "No tenés permiso para ver la agenda. Pedile el acceso al dueño del negocio."
            : "No se pudo cargar la agenda del día. Revisá la conexión y probá de nuevo."}
        </p>
      )}
      {day.data?.truncated && (
        <p role="status" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm">
          Hay más turnos y trabajos de los que entran en la pantalla: se muestran los primeros.
          Elegí otro día o revisá la bandeja de Turnos.
        </p>
      )}
      {day.data &&
        (day.data.entries.length === 0 && day.data.resources.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No hay puestos configurados. Cargalos en Negocio.
          </p>
        ) : (
          <>
            {day.data.entries.length === 0 && (
              <p className="text-sm text-muted-foreground">No hay turnos ni trabajos este día.</p>
            )}
            <DayBoard
              resources={day.data.resources}
              entries={day.data.entries}
              date={date}
              timeZone={day.data.timezone}
            />
          </>
        ))}
    </section>
  );
}
