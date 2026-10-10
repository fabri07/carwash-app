"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import { resetPasswordSchema, type ResetPasswordInput } from "@/validation/team";

interface ResetPasswordFormProps {
  onSubmit: (values: ResetPasswordInput) => Promise<void>;
  onCancel: () => void;
}

/** Clave nueva para un empleado que se la olvidó. Él la cambia al entrar (§2.3). */
export function ResetPasswordForm({ onSubmit, onCancel }: ResetPasswordFormProps) {
  const form = useForm<ResetPasswordInput>({
    resolver: zodResolver(resetPasswordSchema),
    defaultValues: { password: "", confirm_password: "" },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);

  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <FormField
          control={form.control}
          name="password"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Clave nueva</FormLabel>
              <FormControl>
                <Input type="password" autoComplete="new-password" {...field} />
              </FormControl>
              <FormDescription>Mínimo 8 caracteres.</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="confirm_password"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Repetí la clave</FormLabel>
              <FormControl>
                <Input type="password" autoComplete="new-password" {...field} />
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
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button type="button" variant="outline" onClick={onCancel}>
            Cancelar
          </Button>
          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? "Guardando…" : "Guardar clave"}
          </Button>
        </div>
      </form>
    </Form>
  );
}
