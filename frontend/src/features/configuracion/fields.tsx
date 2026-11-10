"use client";

import type { Control, FieldPath, FieldValues } from "react-hook-form";

import {
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MoneyInput } from "@/components/ui/money-input";
import { NativeSelect } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

interface BaseProps<T extends FieldValues> {
  control: Control<T>;
  name: FieldPath<T>;
  label: string;
  hint?: string;
  disabled?: boolean;
}

/** Campo de texto de los formularios de configuración. */
export function TextField<T extends FieldValues>({
  control,
  name,
  label,
  hint,
  disabled,
  multiline = false,
  ...input
}: BaseProps<T> & {
  multiline?: boolean;
  type?: string;
  inputMode?: "text" | "numeric" | "decimal" | "tel" | "url";
  placeholder?: string;
  autoComplete?: string;
}) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel>{label}</FormLabel>
          <FormControl>
            {multiline ? (
              <Textarea disabled={disabled} {...field} />
            ) : (
              <Input disabled={disabled} autoComplete="off" {...input} {...field} />
            )}
          </FormControl>
          {hint && <FormDescription>{hint}</FormDescription>}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

/** Importe en pesos: el valor del formulario son centavos enteros (`number`), nunca texto. */
export function MoneyField<T extends FieldValues>({
  control,
  name,
  label,
  hint,
  disabled,
}: BaseProps<T>) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel>{label}</FormLabel>
          <FormControl>
            <MoneyInput
              name={field.name}
              ref={field.ref}
              disabled={disabled}
              value={field.value as number | null | undefined}
              onChange={field.onChange}
              onBlur={field.onBlur}
            />
          </FormControl>
          {hint && <FormDescription>{hint}</FormDescription>}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

/** Lista desplegable nativa (en el celular abre el selector del sistema). */
export function SelectField<T extends FieldValues>({
  control,
  name,
  label,
  hint,
  disabled,
  options,
}: BaseProps<T> & { options: { value: string; label: string }[] }) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <FormLabel>{label}</FormLabel>
          <FormControl>
            <NativeSelect disabled={disabled} {...field}>
              {options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </NativeSelect>
          </FormControl>
          {hint && <FormDescription>{hint}</FormDescription>}
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

/** Interruptor con su etiqueta. */
export function SwitchField<T extends FieldValues>({
  control,
  name,
  label,
  hint,
  disabled,
}: BaseProps<T>) {
  return (
    <FormField
      control={control}
      name={name}
      render={({ field }) => (
        <FormItem>
          <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
            <div className="space-y-0.5">
              <FormLabel>{label}</FormLabel>
              {hint && <FormDescription>{hint}</FormDescription>}
            </div>
            <FormControl>
              <Switch
                checked={Boolean(field.value)}
                onCheckedChange={field.onChange}
                disabled={disabled}
              />
            </FormControl>
          </div>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

/** Pie de los formularios en diálogo: error general, cancelar y guardar. */
export function DialogFormFooter({
  formError,
  submitting,
  submitLabel,
  onCancel,
}: {
  formError: string | null;
  submitting: boolean;
  submitLabel: string;
  onCancel: () => void;
}) {
  return (
    <>
      {formError && (
        <p role="alert" className="text-sm font-medium text-destructive">
          {formError}
        </p>
      )}
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancelar
        </Button>
        <Button type="submit" disabled={submitting}>
          {submitting ? "Guardando…" : submitLabel}
        </Button>
      </div>
    </>
  );
}
