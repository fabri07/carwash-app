"use client";

import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { isIsoDate, resolvePeriod, todayInZone, type PeriodValue } from "@/lib/period";

/**
 * Filtro de período: hoy, esta semana, este mes o un rango. Portado de Véktor
 * (`components/ui/PeriodFilter.tsx`) y achicado: la navegación jerárquica
 * año → mes → semana → día se cambió por un rango de dos fechas, que cubre lo
 * mismo con menos toques en el celular.
 *
 * Controlado: el valor vive en quien lo usa (y de ahí a la query del backend).
 * Las fechas se interpretan en la zona del negocio (`lib/period.ts`).
 */
interface PeriodFilterProps {
  value: PeriodValue;
  onChange: (value: PeriodValue) => void;
  /** "Ahora" inyectable para tests. */
  now?: Date;
}

const PRESETS: { kind: "today" | "week" | "month"; label: string }[] = [
  { kind: "today", label: "Hoy" },
  { kind: "week", label: "Esta semana" },
  { kind: "month", label: "Este mes" },
];

export function PeriodFilter({ value, onChange, now }: PeriodFilterProps) {
  const id = useId();
  const today = todayInZone(now);
  const initial = value.kind === "range" ? value : { from: today, to: today };
  const [from, setFrom] = useState(initial.from);
  const [to, setTo] = useState(initial.to);
  const [showRange, setShowRange] = useState(value.kind === "range");

  function applyRange(nextFrom: string, nextTo: string) {
    setFrom(nextFrom);
    setTo(nextTo);
    // Un campo de fecha a medio escribir no dispara una búsqueda.
    if (isIsoDate(nextFrom) && isIsoDate(nextTo)) {
      const { from: f, to: t } = resolvePeriod({ kind: "range", from: nextFrom, to: nextTo }, now);
      onChange({ kind: "range", from: f, to: t });
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div role="group" aria-label="Período" className="flex flex-wrap items-center gap-2">
        {PRESETS.map((p) => (
          <Button
            key={p.kind}
            type="button"
            size="sm"
            variant={value.kind === p.kind ? "default" : "outline"}
            aria-pressed={value.kind === p.kind}
            onClick={() => {
              setShowRange(false);
              onChange({ kind: p.kind });
            }}
          >
            {p.label}
          </Button>
        ))}
        <Button
          type="button"
          size="sm"
          variant={value.kind === "range" ? "default" : "outline"}
          aria-pressed={value.kind === "range"}
          onClick={() => {
            setShowRange(true);
            applyRange(from, to);
          }}
        >
          Rango
        </Button>
      </div>

      {showRange && (
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label htmlFor={`${id}-from`}>Desde</Label>
            <Input
              id={`${id}-from`}
              type="date"
              value={from}
              onChange={(e) => applyRange(e.target.value, to)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor={`${id}-to`}>Hasta</Label>
            <Input
              id={`${id}-to`}
              type="date"
              value={to}
              onChange={(e) => applyRange(from, e.target.value)}
            />
          </div>
        </div>
      )}
    </div>
  );
}
