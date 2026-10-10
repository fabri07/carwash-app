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
import { changePasswordSchema, type ChangePasswordInput } from "@/validation/auth";

interface ChangePasswordFormProps {
  /** Si rechaza con `FormSubmitError`, el mensaje va al campo que corresponde. */
  onSubmit: (values: ChangePasswordInput) => Promise<void>;
}

/** ADR-0010: react-hook-form + zodResolver. La confirmación no viaja al backend. */
export function ChangePasswordForm({ onSubmit }: ChangePasswordFormProps) {
  const form = useForm<ChangePasswordInput>({
    resolver: zodResolver(changePasswordSchema),
    defaultValues: { current_password: "", new_password: "", confirm_password: "" },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);

  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <FormField
          control={form.control}
          name="current_password"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Contraseña actual</FormLabel>
              <FormControl>
                <Input type="password" autoComplete="current-password" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="new_password"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Contraseña nueva</FormLabel>
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
              <FormLabel>Repetí la contraseña nueva</FormLabel>
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
        <Button type="submit" className="w-full" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? "Guardando…" : "Cambiar contraseña"}
        </Button>
      </form>
    </Form>
  );
}
