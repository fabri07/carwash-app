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
import { ProfileSelect } from "@/features/equipo/ProfileSelect";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import type { PermissionProfile } from "@/types/api";
import { staffCreateSchema, type StaffCreateInput } from "@/validation/team";

interface StaffCreateFormProps {
  profiles: PermissionProfile[];
  /** Si rechaza con `FormSubmitError` (p. ej. usuario tomado), el error va a su campo. */
  onSubmit: (values: StaffCreateInput) => Promise<void>;
  onCancel: () => void;
}

/** Alta de empleado (D4-3): usuario y clave inicial los elige el dueño; el email es opcional. */
export function StaffCreateForm({ profiles, onSubmit, onCancel }: StaffCreateFormProps) {
  const form = useForm<StaffCreateInput>({
    resolver: zodResolver(staffCreateSchema),
    defaultValues: { username: "", password: "", permission_profile_id: "", email: "" },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);

  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <FormField
          control={form.control}
          name="username"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Usuario</FormLabel>
              <FormControl>
                <Input
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  {...field}
                />
              </FormControl>
              <FormDescription>
                Con esto entra a la app. Letras sin tilde, números, punto o guion.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="password"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Clave inicial</FormLabel>
              <FormControl>
                <Input type="password" autoComplete="new-password" {...field} />
              </FormControl>
              <FormDescription>
                Mínimo 8 caracteres. La va a tener que cambiar la primera vez que entre.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="permission_profile_id"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Perfil</FormLabel>
              <ProfileSelect
                profiles={profiles}
                value={field.value}
                onChange={field.onChange}
                onBlur={field.onBlur}
              />
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Email (opcional)</FormLabel>
              <FormControl>
                <Input type="email" autoComplete="off" inputMode="email" {...field} />
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
            {form.formState.isSubmitting ? "Creando…" : "Crear empleado"}
          </Button>
        </div>
      </form>
    </Form>
  );
}
