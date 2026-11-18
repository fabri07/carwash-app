"use client";

import { useEffect } from "react";
import axios from "axios";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useCan } from "@/hooks/usePermission";
import { localTime } from "@/lib/agenda";
import { isIsoDate, todayInZone } from "@/lib/period";
import { formatARS } from "@/lib/money";
import { cn } from "@/lib/utils";
import { Guard } from "./Guard";
import { useBusinessTimeZone, useDay } from "./queries";
import { entryStatus } from "./status";
import { BIG_ACTION, TOUCH_TARGET } from "./touch";
import type { Job } from "./types";
import { ARRIVAL_QUEUED_NOTICE, useActionRunner } from "./useActionRunner";
import { KINDS } from "./useOperacionSubmit";

/**
 * `/operacion/turno/{bookingId}?date=`: un turno que todavía no tiene job. Muestra el turno (se
 * lee del mismo pedido de la agenda del día, que ya está en caché) y LLEGÓ, un toque: `POST /jobs`
 * con `booking_id` y la hora del toque. Con el job creado, sigue en su pantalla operativa.
 */
export function BookingArrivalClient({ bookingId }: { bookingId: string }) {
  return (
    <Guard permission="AGENDA_VER" title="la operación">
      <Arrival bookingId={bookingId} />
    </Guard>
  );
}

function Arrival({ bookingId }: { bookingId: string }) {
  const router = useRouter();
  const can = useCan();
  const timeZone = useBusinessTimeZone();
  const param = useSearchParams().get("date");
  const date = param && isIsoDate(param) ? param : todayInZone(new Date(), timeZone);
  const day = useDay(date);
  const { exec, busy, notice, setNotice } = useActionRunner();
  const entry = day.data?.entries.find((e) => e.booking_id === bookingId);
  const jobId = entry?.job_id ?? null;

  // Un turno que ya tiene job se opera en la pantalla del job.
  useEffect(() => {
    if (jobId) router.replace(`/operacion/${jobId}`);
  }, [jobId, router]);

  if (!day.data) {
    // Sin datos: cargando, sin red (consulta pausada o error de red) o un 404 real.
    const httpStatus = axios.isAxiosError(day.error) ? day.error.response?.status : undefined;
    if (httpStatus !== 404 && day.isFetching && day.fetchStatus !== "paused") {
      return <p className="text-sm text-muted-foreground">Cargando…</p>;
    }
    return (
      <div className="space-y-3">
        <p role="alert" className="text-sm text-destructive">
          {httpStatus === 404
            ? "No se encontró la agenda de ese día."
            : "Sin conexión: no se pudo cargar el turno."}{" "}
          <Link href="/agenda" className={cn(TOUCH_TARGET, "inline-flex items-center underline")}>
            Volver a la agenda
          </Link>
        </p>
        {httpStatus !== 404 && (
          <Button variant="outline" onClick={() => void day.refetch()}>
            Reintentar
          </Button>
        )}
      </div>
    );
  }
  if (!entry) {
    return (
      <p role="alert" className="text-sm text-destructive">
        No se encontró el turno en la agenda de ese día. Puede que ya se haya recibido o cancelado.{" "}
        <Link href="/agenda" className={cn(TOUCH_TARGET, "inline-flex items-center underline")}>
          Volver a la agenda
        </Link>
      </p>
    );
  }
  if (jobId) return null;
  const status = entryStatus(entry);

  async function arrived() {
    const out = await exec(
      KINDS.receive,
      "receive",
      { body: { booking_id: bookingId } },
      `llego:${bookingId}`,
    );
    if (out?.status === "queued") setNotice({ tone: "info", text: ARRIVAL_QUEUED_NOTICE });
    if (out?.status === "ok") {
      const job = out.data as Job | undefined;
      router.replace(job ? `/operacion/${job.id}` : "/agenda");
    }
  }

  return (
    <section aria-labelledby="arrival-title" className="space-y-5">
      <Link
        href="/agenda"
        className={cn(TOUCH_TARGET, "inline-flex items-center text-sm underline")}
      >
        ← Agenda
      </Link>
      <div className="space-y-1">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 id="arrival-title" className="text-2xl font-semibold">
            {entry.customer?.name ?? "Sin nombre"}
            {entry.vehicle_plate ? ` · ${entry.vehicle_plate}` : ""}
          </h1>
          <Badge className={status.tone}>{status.label}</Badge>
        </div>
        <p className="text-sm text-muted-foreground">{entry.services.join(" + ")}</p>
        <p className="text-sm text-muted-foreground">
          Turno {localTime(entry.start_at, timeZone)}–{localTime(entry.end_at, timeZone)}
          {entry.code ? ` · ${entry.code}` : ""}
          {entry.total_cents !== null ? ` · ${formatARS(entry.total_cents)}` : ""}
        </p>
      </div>
      {notice && (
        <p
          role={notice.tone === "error" ? "alert" : "status"}
          className={cn(
            "rounded-md border p-3 text-sm",
            notice.tone === "error" ? "border-destructive text-destructive" : "border-sky-300",
          )}
        >
          {notice.text}
        </p>
      )}
      {can("JOBS_OPERAR") && (
        <Button className={BIG_ACTION} disabled={busy} onClick={() => void arrived()}>
          LLEGÓ
        </Button>
      )}
    </section>
  );
}
