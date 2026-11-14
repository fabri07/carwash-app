"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { FormSubmitError } from "@/lib/errors";

import { bookingDetailsSchema, type BookingDetailsValues } from "./schema";

interface BookingDetailsFormProps {
  /** Texto de la política del negocio; sin texto se acepta igual (se guarda sin versión). */
  policy: string | null;
  submitLabel: string;
  /** Un `FormSubmitError` con `field` va debajo de ese campo; cualquier otro, arriba del botón. */
  onSubmit: (values: BookingDetailsValues) => Promise<void>;
}

/** Datos del cliente: nombre, WhatsApp, patente opcional (D5-3) y la política (§4.2 paso 8). */
export function BookingDetailsForm({ policy, submitLabel, onSubmit }: BookingDetailsFormProps) {
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<BookingDetailsValues>({
    resolver: zodResolver(bookingDetailsSchema),
    defaultValues: { name: "", phone: "", plate: "", acceptPolicy: false },
  });

  async function submit(values: BookingDetailsValues) {
    setFormError(null);
    try {
      await onSubmit(values);
    } catch (e) {
      if (e instanceof FormSubmitError && e.field) {
        form.setError(e.field as keyof BookingDetailsValues, { message: e.message });
        return;
      }
      setFormError(e instanceof Error ? e.message : "Ocurrió un error inesperado.");
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(submit)} noValidate className="space-y-4">
        <FormField
          control={form.control}
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Nombre</FormLabel>
              <FormControl>
                <Input autoComplete="name" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="phone"
          render={({ field }) => (
            <FormItem>
              <FormLabel>WhatsApp</FormLabel>
              <FormControl>
                <Input
                  type="tel"
                  inputMode="tel"
                  autoComplete="tel"
                  placeholder="351 000 0000"
                  {...field}
                />
              </FormControl>
              <p className="text-xs text-muted-foreground">
                Con código de área. Lo vas a necesitar, con el código del turno, para verlo o
                cancelarlo.
              </p>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="plate"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Patente (opcional)</FormLabel>
              <FormControl>
                <Input autoCapitalize="characters" autoCorrect="off" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        {policy && (
          <div className="rounded-md border bg-muted/40 p-3 text-sm">
            <p className="mb-1 font-medium">Política de cancelación</p>
            <p className="whitespace-pre-line text-muted-foreground">{policy}</p>
          </div>
        )}
        <FormField
          control={form.control}
          name="acceptPolicy"
          render={({ field }) => (
            <FormItem>
              <label className="flex min-h-touch items-center gap-3 text-sm">
                <FormControl>
                  <Checkbox
                    checked={field.value}
                    onCheckedChange={(v) => field.onChange(v === true)}
                  />
                </FormControl>
                Acepto la política de cancelación
              </label>
              <FormMessage />
            </FormItem>
          )}
        />
        {formError && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {formError}
          </p>
        )}
        <Button type="submit" className="w-full" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? "Reservando…" : submitLabel}
        </Button>
      </form>
    </Form>
  );
}
