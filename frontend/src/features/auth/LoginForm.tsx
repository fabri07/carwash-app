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
import { loginSchema, type LoginInput } from "@/validation/auth";

interface LoginFormProps {
  /** Si rechaza, el mensaje del error se muestra arriba del botón. */
  onSubmit: (values: LoginInput) => Promise<void>;
}

/**
 * ADR-0010: react-hook-form + zodResolver. Nada de `safeParse` a mano: el
 * estado de errores, `touched` e `isSubmitting` lo lleva RHF.
 */
export function LoginForm({ onSubmit }: LoginFormProps) {
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<LoginInput>({
    resolver: zodResolver(loginSchema),
    defaultValues: { identifier: "", password: "" },
  });

  async function submit(values: LoginInput) {
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
          name="identifier"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Email o usuario</FormLabel>
              <FormControl>
                {/* `type="text"`: un usuario de empleado no es un email. `username`
                    es el autocompletado correcto para los dos casos. */}
                <Input
                  type="text"
                  autoComplete="username"
                  autoCapitalize="none"
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
          name="password"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Contraseña</FormLabel>
              <FormControl>
                <Input type="password" autoComplete="current-password" {...field} />
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
          {form.formState.isSubmitting ? "Ingresando…" : "Ingresar"}
        </Button>
      </form>
    </Form>
  );
}
