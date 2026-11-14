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
import { Textarea } from "@/components/ui/textarea";

import { cancelSchema, type CancelValues } from "./schema";

/** Cancelar el turno propio, con un motivo opcional (≤ 160). */
export function CancelBookingForm({
  onSubmit,
}: {
  onSubmit: (values: CancelValues) => Promise<void>;
}) {
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<CancelValues>({
    resolver: zodResolver(cancelSchema),
    defaultValues: { reason: "" },
  });

  async function submit(values: CancelValues) {
    setFormError(null);
    try {
      await onSubmit(values);
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Ocurrió un error inesperado.");
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(submit)} noValidate className="space-y-3">
        <FormField
          control={form.control}
          name="reason"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Motivo (opcional)</FormLabel>
              <FormControl>
                <Textarea rows={2} maxLength={160} {...field} />
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
        <Button
          type="submit"
          variant="destructive"
          className="w-full"
          disabled={form.formState.isSubmitting}
        >
          {form.formState.isSubmitting ? "Cancelando…" : "Cancelar el turno"}
        </Button>
      </form>
    </Form>
  );
}
