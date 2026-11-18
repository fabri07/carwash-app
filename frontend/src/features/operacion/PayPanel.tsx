"use client";

import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { MoneyInput } from "@/components/ui/money-input";
import { NativeSelect } from "@/components/ui/native-select";
import { formatARS } from "@/lib/money";
import { defaultPaymentMethod } from "./queries";
import { BIG_ACTION } from "./touch";
import { cn } from "@/lib/utils";
import type { PaymentMethod } from "@/types/api";

/**
 * Segundo toque de COBRAR (el primero lo abre): el medio de pago ya viene elegido (el efectivo, o el
 * primero que cobre, §12.8) y el importe es el saldo. CONFIRMAR registra el cobro con la hora de
 * ESTE toque. El importe y el medio se pueden cambiar antes (cobro parcial, otro medio), sin que el
 * camino normal los pida.
 */
export function PayPanel({
  balanceCents,
  methods,
  busy,
  onConfirm,
  onCancel,
}: {
  balanceCents: number;
  methods: PaymentMethod[];
  busy: boolean;
  /** `amountCents` es `undefined` cuando se cobra el saldo completo (el servidor lo calcula). */
  onConfirm: (methodId: string, amountCents: number | undefined) => void;
  onCancel: () => void;
}) {
  const ids = { method: useId(), amount: useId() };
  const incomes = methods.filter((m) => m.for_income);
  const [methodId, setMethodId] = useState(() => defaultPaymentMethod(methods)?.id ?? "");
  const [amount, setAmount] = useState<number | null>(balanceCents);

  if (incomes.length === 0) {
    return (
      <p role="alert" className="rounded-md border p-3 text-sm text-destructive">
        No hay un medio de pago que acepte cobros. Cargalo en Negocio.
      </p>
    );
  }
  const valid = amount !== null && amount > 0 && amount <= balanceCents;

  return (
    <div className="space-y-3 rounded-lg border-2 border-primary p-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor={ids.method}>Medio de pago</Label>
          <NativeSelect
            id={ids.method}
            value={methodId}
            onChange={(e) => setMethodId(e.target.value)}
          >
            {incomes.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="space-y-1">
          <Label htmlFor={ids.amount}>Importe</Label>
          <MoneyInput id={ids.amount} value={amount} onChange={setAmount} />
        </div>
      </div>
      {!valid && (
        <p role="alert" className="text-sm text-destructive">
          El importe tiene que ser mayor a cero y no superar el saldo ({formatARS(balanceCents)}).
        </p>
      )}
      <Button
        className={cn(BIG_ACTION)}
        disabled={busy || !valid || !methodId}
        onClick={() =>
          onConfirm(methodId, amount === balanceCents ? undefined : (amount ?? undefined))
        }
      >
        CONFIRMAR COBRO {valid ? formatARS(amount) : ""}
      </Button>
      <Button variant="outline" className="w-full" disabled={busy} onClick={onCancel}>
        Volver
      </Button>
    </div>
  );
}
