"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useCan } from "@/hooks/usePermission";
import { ReasonDialog } from "@/features/turnos/ReasonDialog";
import { formatARS } from "@/lib/money";
import { cn } from "@/lib/utils";
import { operacionErrorMessage } from "./errors";
import { Guard } from "./Guard";
import { JobTimeline } from "./JobTimeline";
import { applyOptimistic, type OptimisticAction } from "./optimistic";
import { PayPanel } from "./PayPanel";
import { QueueBanner } from "./QueueBanner";
import { useBusinessTimeZone, useJob, usePaymentMethods } from "./queries";
import { JOB_STATUS } from "./status";
import { formatStamp, tapTime } from "./time";
import { BIG_ACTION, TOUCH_TARGET } from "./touch";
import type { Job } from "./types";
import { QUEUED_NOTICE, useActionRunner } from "./useActionRunner";
import { KINDS } from "./useOperacionSubmit";

/** Cada cuánto se revisa si ya se cumplió la tolerancia (aparece "Cancelar por demora"). */
const TICK_MS = 15_000;

function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

/**
 * `/operacion/{id}`: la pantalla operativa de un job (FASE-6-CONTRATO §4). Botones grandes, un
 * toque por paso: INICIAR, FINALIZAR, RETIRAR; COBRAR son dos (abrir y confirmar). Cada toque manda
 * su `Idempotency-Key` y la hora del toque (`occurred_at`) por la cola offline. Ver la pantalla pide
 * `AGENDA_VER`; los botones aparecen según `JOBS_OPERAR` y `COBROS_*`: el servidor decide igual.
 */
export function JobPageClient({ id }: { id: string }) {
  return (
    <Guard permission="AGENDA_VER" title="la operación">
      <JobScreen id={id} />
    </Guard>
  );
}

function JobScreen({ id }: { id: string }) {
  const can = useCan();
  const timeZone = useBusinessTimeZone();
  const jobQuery = useJob(id);
  const methods = usePaymentMethods();
  const { exec, busy, notice, setNotice } = useActionRunner();
  const now = useNow(TICK_MS);
  const [local, setLocal] = useState<Job | null>(null);
  const [paying, setPaying] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [voiding, setVoiding] = useState<string | null>(null);

  // Lo que dice el servidor reemplaza al estado optimista apenas se vuelve a leer el job.
  useEffect(() => setLocal(null), [jobQuery.dataUpdatedAt]);

  if (jobQuery.isLoading) return <p className="text-sm text-muted-foreground">Cargando…</p>;
  if (jobQuery.isError || !jobQuery.data) {
    return (
      <p role="alert" className="text-sm text-destructive">
        No se encontró el trabajo. Puede que no exista o que no tengas acceso.{" "}
        <Link href="/agenda" className={cn(TOUCH_TARGET, "inline-flex items-center underline")}>
          Volver a la agenda
        </Link>
      </p>
    );
  }
  const job = local ?? jobQuery.data;
  const status = JOB_STATUS[job.status];
  const canOperate = can("JOBS_OPERAR");
  const canCharge = can("COBROS_REGISTRAR");
  const canVoid = can("COBROS_ANULAR");
  const delayDue =
    job.status === "PRESENTE" &&
    job.cancel_delay_from !== null &&
    now >= Date.parse(job.cancel_delay_from);

  /** Manda una acción del job. Con la cola offline el estado se adelanta en pantalla. */
  async function act(
    kind: (typeof KINDS)[keyof typeof KINDS],
    action: Parameters<typeof operacionErrorMessage>[1],
    dedupe: string,
    body: Record<string, unknown>,
    optimistic: OptimisticAction,
  ) {
    // `occurred_at` lo pone la cola al tocar; acá solo va lo necesario para reenviar.
    const out = await exec(kind, action, { jobId: id, body }, `${dedupe}:${id}`);
    if (out?.status === "queued") setLocal(applyOptimistic(job, optimistic));
    return out;
  }

  const start = () => {
    const at = tapTime();
    void act(KINDS.start, "start", "iniciar", {}, { kind: "start", at });
  };
  const finish = () => {
    const at = tapTime();
    void act(KINDS.finish, "finish", "finalizar", {}, { kind: "finish", at });
  };
  const pickUp = () => {
    const at = tapTime();
    void act(KINDS.pickUp, "pickUp", "retirar", {}, { kind: "pickUp", at });
  };
  const pay = async (methodId: string, amountCents: number | undefined) => {
    const at = tapTime();
    const out = await act(
      KINDS.pay,
      "pay",
      "cobrar",
      { payment_method_id: methodId, ...(amountCents ? { amount_cents: amountCents } : {}) },
      { kind: "pay", at, amountCents: amountCents ?? job.balance_due_cents },
    );
    if (out && out.status !== "error") setPaying(false);
  };

  return (
    <section aria-labelledby="job-title" className="space-y-5">
      <div className="space-y-1">
        <Link
          href="/agenda"
          className={cn(TOUCH_TARGET, "inline-flex items-center text-sm underline")}
        >
          ← Agenda
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 id="job-title" className="text-2xl font-semibold">
            {job.customer?.name ?? "Sin nombre"}
            {job.vehicle_plate ? ` · ${job.vehicle_plate}` : ""}
          </h1>
          <Badge className={status.tone}>{status.label}</Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          {job.services.map((s) => s.name).join(" + ") || "Sin servicio"}
        </p>
        <p className="text-sm text-muted-foreground">
          Llegó {formatStamp(job.arrived_at, timeZone)}
          {job.scheduled_at
            ? ` · Turno ${formatStamp(job.scheduled_at, timeZone)}`
            : " · Sin turno"}
        </p>
        <p className="text-lg font-semibold">
          Total {formatARS(job.total_cents)}
          {job.balance_due_cents > 0 ? (
            <span className="ml-2 text-destructive">Debe {formatARS(job.balance_due_cents)}</span>
          ) : (
            <span className="ml-2 text-green-700">Pagado</span>
          )}
        </p>
      </div>

      <QueueBanner />

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
      {local && !notice && (
        <p role="status" className="rounded-md border border-sky-300 p-3 text-sm">
          {QUEUED_NOTICE}
        </p>
      )}

      {canOperate && (
        <div className="space-y-3">
          {job.status === "PRESENTE" && (
            <Button className={BIG_ACTION} disabled={busy} onClick={start}>
              INICIAR
            </Button>
          )}
          {job.status === "EN_PROCESO" && (
            <Button className={BIG_ACTION} disabled={busy} onClick={finish}>
              FINALIZAR
            </Button>
          )}
          {delayDue && (
            <Button
              variant="destructive"
              className="min-h-14 w-full text-lg font-bold"
              disabled={busy}
              onClick={() => setCancelling(true)}
            >
              Cancelar por demora
            </Button>
          )}
        </div>
      )}

      {job.status === "FINALIZADO" && canCharge && job.balance_due_cents > 0 && (
        <div className="space-y-3">
          {!paying ? (
            <Button
              className={BIG_ACTION}
              disabled={busy || !methods.data}
              onClick={() => setPaying(true)}
            >
              COBRAR {formatARS(job.balance_due_cents)}
            </Button>
          ) : (
            <PayPanel
              balanceCents={job.balance_due_cents}
              methods={methods.data?.items ?? []}
              busy={busy}
              onConfirm={(methodId, amount) => void pay(methodId, amount)}
              onCancel={() => setPaying(false)}
            />
          )}
        </div>
      )}

      {canOperate && (job.status === "FINALIZADO" || job.status === "COBRADO") && (
        <Button
          variant={job.status === "COBRADO" ? "default" : "outline"}
          className={job.status === "COBRADO" ? BIG_ACTION : "min-h-14 w-full text-lg font-bold"}
          disabled={busy}
          onClick={pickUp}
        >
          RETIRAR
        </Button>
      )}

      <section aria-labelledby="payments-title" className="space-y-2">
        <h2 id="payments-title" className="text-lg font-semibold">
          Cobros
        </h2>
        {job.payments.length === 0 && (
          <p className="text-sm text-muted-foreground">Todavía no hay cobros.</p>
        )}
        <ul className="space-y-2">
          {job.payments.map((p) => (
            <li key={p.id} className="flex items-center justify-between gap-2 text-sm">
              <span className={cn(p.voided && "text-muted-foreground line-through")}>
                {p.kind === "SEÑA" ? "Seña" : p.kind === "DEVOLUCION" ? "Devolución" : "Cobro"}{" "}
                {formatARS(p.amount_cents)} · {formatStamp(p.occurred_at, timeZone)}
              </span>
              {p.voided ? (
                <span className="text-xs text-muted-foreground">Anulado</span>
              ) : (
                canVoid &&
                p.kind === "SALDO" && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => setVoiding(p.id)}
                  >
                    Anular cobro
                  </Button>
                )
              )}
            </li>
          ))}
        </ul>
      </section>

      {canOperate && (job.status === "PRESENTE" || job.status === "EN_PROCESO") && (
        <Link
          href={`/operacion/${id}/recepcion`}
          className={cn(TOUCH_TARGET, "inline-flex items-center text-sm underline")}
        >
          Recepción completa (opcional)
        </Link>
      )}

      <JobTimeline jobId={id} timeZone={timeZone} />

      <ConfirmDialog
        open={cancelling}
        onOpenChange={setCancelling}
        title="Cancelar por demora"
        description="El cliente no llegó a tiempo. Si había seña, se retiene según la política del negocio. Esto no se puede deshacer desde acá."
        confirmLabel="Sí, cancelar por demora"
        variant="destructive"
        onConfirm={async () => {
          const at = tapTime();
          const out = await act(
            KINDS.cancelDelay,
            "cancelDelay",
            "cancel-delay",
            {},
            { kind: "cancelDelay", at },
          );
          if (out?.status === "error")
            throw new Error(operacionErrorMessage(out.error, "cancelDelay"));
        }}
      />
      <ReasonDialog
        open={voiding !== null}
        onOpenChange={(open) => !open && setVoiding(null)}
        title="Anular cobro"
        description="El cobro queda anulado y el saldo vuelve a deber. Escribí el motivo."
        label="Motivo"
        confirmLabel="Anular cobro"
        variant="destructive"
        onSubmit={async (reason) => {
          const out = await exec(
            KINDS.void,
            "void",
            { jobId: id, paymentId: voiding ?? undefined, body: { reason } },
            `anular:${id}`,
          );
          if (out?.status === "error") throw new Error(operacionErrorMessage(out.error, "void"));
          setNotice(null);
        }}
      />
    </section>
  );
}
