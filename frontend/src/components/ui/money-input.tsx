"use client";

import * as React from "react";

import { Input } from "@/components/ui/input";
import { formatAmount, parseARS } from "@/lib/money";
import { cn } from "@/lib/utils";

/**
 * Campo de importe en pesos que trabaja en **centavos enteros** (nunca float).
 *
 * El usuario escribe en formato argentino (`20.000`, `1.500,50`); el campo
 * emite `onChange(centavos)` o `onChange(null)` si lo escrito no se puede leer
 * sin adivinar (ver `parseARS`). `null` no es `0`: el schema del formulario lo
 * rechaza con su mensaje, en vez de guardar cero pesos en silencio como hacía
 * `parseMoney_` en el legacy.
 *
 * Mientras se escribe, el texto se respeta tal cual; al salir del campo, si es
 * válido, se reescribe normalizado (`20000` → `20.000,00`). Se integra con
 * react-hook-form vía `FormField` (`value`, `onChange`, `onBlur`, `ref`).
 */
export interface MoneyInputProps extends Omit<
  React.ComponentProps<"input">,
  "value" | "onChange" | "type" | "defaultValue"
> {
  value: number | null | undefined;
  onChange: (cents: number | null) => void;
}

export const MoneyInput = React.forwardRef<HTMLInputElement, MoneyInputProps>(
  ({ value, onChange, onBlur, className, ...props }, ref) => {
    const [text, setText] = React.useState(() => (value == null ? "" : formatAmount(value)));

    // Si el valor cambia desde afuera (reset del formulario, dato del
    // servidor) y no coincide con lo escrito, manda el de afuera.
    React.useEffect(() => {
      setText((current) => {
        const parsed = parseARS(current);
        if (value == null) return parsed === null ? current : "";
        return parsed === value ? current : formatAmount(value);
      });
    }, [value]);

    return (
      <div className="relative">
        <span
          aria-hidden="true"
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
        >
          $
        </span>
        <Input
          ref={ref}
          type="text"
          inputMode="decimal"
          autoComplete="off"
          className={cn("pl-7 text-right tabular-nums", className)}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            onChange(parseARS(e.target.value));
          }}
          onBlur={(e) => {
            const parsed = parseARS(text);
            if (parsed !== null) setText(formatAmount(parsed));
            onBlur?.(e);
          }}
          {...props}
        />
      </div>
    );
  },
);
MoneyInput.displayName = "MoneyInput";
