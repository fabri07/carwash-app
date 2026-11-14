"use client";

import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { MoneyInput } from "@/components/ui/money-input";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { formatARS } from "@/lib/money";
import { DEPOSIT_STATUS_LABELS } from "@/features/turnos/queries";
import { REASON_MAX } from "@/features/turnos/ReasonDialog";
import type {
  DepositCase,
  DepositPayload,
  InboxBooking,
  PaymentMethod,
  ResolvePayload,
} from "@/types/api";

interface ShellProps {
  title: string;
  description: string;
  confirmLabel: string;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: () => void;
  children: React.ReactNode;
}

function Shell({
  title,
  description,
  confirmLabel,
  busy,
  error,
  onClose,
  onSubmit,
  children,
}: ShellProps) {
  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onSubmit();
          }}
          className="space-y-4"
        >
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          {children}
          {error && (
            <p role="alert" className="text-sm font-medium text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
              Volver
            </Button>
            <Button type="submit" disabled={busy}>
              {confirmLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Corre `fn` con el diálogo ocupado; un `Error` queda en el diálogo y no se cierra. */
function useSubmit(onClose: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ocurrió un error inesperado.");
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, setError, run };
}

interface DepositDialogProps {
  booking: InboxBooking;
  methods: PaymentMethod[];
  onClose: () => void;
  onSubmit: (payload: DepositPayload) => Promise<void>;
}

/** Confirmar la seña: medio de pago e importe (por defecto, la seña pedida). */
export function DepositDialog({ booking, methods, onClose, onSubmit }: DepositDialogProps) {
  const ids = { method: useId(), amount: useId() };
  const income = methods.filter((m) => m.for_income);
  const [methodId, setMethodId] = useState(income[0]?.id ?? "");
  const [amount, setAmount] = useState<number | null>(booking.deposit_required_cents || null);
  const { busy, error, setError, run } = useSubmit(onClose);
  const late = booking.status === "VENCIDO";

  return (
    <Shell
      title={`Confirmar la seña de ${booking.code}`}
      description={
        late
          ? "La retención venció: si el horario sigue libre, el turno se confirma igual."
          : `Seña pedida: ${formatARS(booking.deposit_required_cents)}.`
      }
      confirmLabel="Confirmar seña"
      busy={busy}
      error={error}
      onClose={onClose}
      onSubmit={() => {
        if (!methodId) return setError("Elegí el medio de pago.");
        if (amount === null || amount <= 0) return setError("Escribí el importe que llegó.");
        void run(() => onSubmit({ payment_method_id: methodId, amount_cents: amount }));
      }}
    >
      <div className="space-y-2">
        <Label htmlFor={ids.method}>Medio de pago</Label>
        <NativeSelect
          id={ids.method}
          value={methodId}
          onChange={(e) => setMethodId(e.target.value)}
        >
          {income.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </NativeSelect>
      </div>
      <div className="space-y-2">
        <Label htmlFor={ids.amount}>Importe</Label>
        <MoneyInput id={ids.amount} value={amount} onChange={setAmount} />
      </div>
    </Shell>
  );
}

type Resolution = ResolvePayload["status"];

interface ResolveDialogProps {
  item: DepositCase;
  methods: PaymentMethod[];
  onClose: () => void;
  onSubmit: (payload: ResolvePayload) => Promise<void>;
}

/**
 * Cerrar el caso de la seña de una cancelación (D-005): devolverla (sale de caja), reprogramarla
 * o, solo si el cliente canceló después de la hora del turno, retenerla.
 */
export function ResolveDialog({ item, methods, onClose, onSubmit }: ResolveDialogProps) {
  const ids = { status: useId(), reason: useId(), method: useId(), amount: useId() };
  const { cancellation } = item;
  const options: Resolution[] =
    cancellation.deposit_status === "EN_REVISION"
      ? ["DEVUELTA", "REPROGRAMADA", "RETENIDA"]
      : ["DEVUELTA", "REPROGRAMADA"];
  const [status, setStatus] = useState<Resolution>(options[0]!);
  const [reason, setReason] = useState("");
  const [methodId, setMethodId] = useState(methods[0]?.id ?? "");
  const [amount, setAmount] = useState<number | null>(cancellation.deposit_paid_cents);
  const { busy, error, setError, run } = useSubmit(onClose);

  return (
    <Shell
      title={`Resolver la seña de ${item.booking.code}`}
      description={`${DEPOSIT_STATUS_LABELS[cancellation.deposit_status]} · cobrado ${formatARS(cancellation.deposit_paid_cents)}.`}
      confirmLabel="Resolver"
      busy={busy}
      error={error}
      onClose={onClose}
      onSubmit={() => {
        const cleaned = reason.trim();
        if (!cleaned) return setError("Escribí el motivo.");
        if (status === "DEVUELTA") {
          if (!methodId) return setError("Elegí por dónde sale la devolución.");
          if (amount === null || amount <= 0) return setError("Escribí el importe devuelto.");
          void run(() =>
            onSubmit({
              status,
              reason: cleaned,
              refund: { payment_method_id: methodId, amount_cents: amount },
            }),
          );
          return;
        }
        void run(() => onSubmit({ status, reason: cleaned }));
      }}
    >
      <div className="space-y-2">
        <Label htmlFor={ids.status}>Qué se hace con la seña</Label>
        <NativeSelect
          id={ids.status}
          value={status}
          onChange={(e) => setStatus(e.target.value as Resolution)}
        >
          {options.map((o) => (
            <option key={o} value={o}>
              {o === "DEVUELTA" ? "Devolverla" : o === "REPROGRAMADA" ? "Reprogramar" : "Retenerla"}
            </option>
          ))}
        </NativeSelect>
      </div>
      {status === "DEVUELTA" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor={ids.method}>Sale por</Label>
            <NativeSelect
              id={ids.method}
              value={methodId}
              onChange={(e) => setMethodId(e.target.value)}
            >
              {methods.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div className="space-y-2">
            <Label htmlFor={ids.amount}>Importe devuelto</Label>
            <MoneyInput id={ids.amount} value={amount} onChange={setAmount} />
          </div>
        </div>
      )}
      <div className="space-y-2">
        <Label htmlFor={ids.reason}>Motivo</Label>
        <Textarea
          id={ids.reason}
          value={reason}
          maxLength={REASON_MAX}
          onChange={(e) => setReason(e.target.value)}
        />
      </div>
    </Shell>
  );
}
