"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";

import { lookupSchema, type LookupValues } from "./schema";

/** Código del turno + el WhatsApp con el que se reservó (D5-8). */
export function BookingLookupForm({
  onSubmit,
}: {
  onSubmit: (values: LookupValues) => Promise<void>;
}) {
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<LookupValues>({
    resolver: zodResolver(lookupSchema),
    defaultValues: { code: "", phone: "" },
  });

  async function submit(values: LookupValues) {
    setFormError(null);
    try {
      await onSubmit(values);
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Ocurrió un error inesperado.");
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(submit)} noValidate className="space-y-4">
        <FormField
          control={form.control}
          name="code"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Código del turno</FormLabel>
              <FormControl>
                <Input
                  placeholder="TUR-XXXXXX"
                  autoCapitalize="characters"
                  autoCorrect="off"
                  spellCheck={false}
                  {...field}
                />
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
              <FormLabel>WhatsApp con el que reservaste</FormLabel>
              <FormControl>
                <Input type="tel" inputMode="tel" autoComplete="tel" {...field} />
              </FormControl>
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
          {form.formState.isSubmitting ? "Buscando…" : "Ver mi turno"}
        </Button>
      </form>
    </Form>
  );
}
