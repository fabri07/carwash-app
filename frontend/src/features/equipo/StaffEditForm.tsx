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
import type { PermissionProfile, StaffMember } from "@/types/api";
import { staffEditSchema, type StaffEditInput } from "@/validation/team";

interface StaffEditFormProps {
  staff: StaffMember;
  profiles: PermissionProfile[];
  onSubmit: (values: StaffEditInput) => Promise<void>;
  onCancel: () => void;
}

/** Edición de un empleado. La clave no se edita acá: tiene su propio "resetear". */
export function StaffEditForm({ staff, profiles, onSubmit, onCancel }: StaffEditFormProps) {
  const form = useForm<StaffEditInput>({
    resolver: zodResolver(staffEditSchema),
    defaultValues: {
      username: staff.username ?? "",
      permission_profile_id: staff.permission_profile_id ?? "",
      email: staff.email ?? "",
    },
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
              <FormDescription>El cambio de perfil rige desde su próxima acción.</FormDescription>
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
              <FormDescription>Dejalo vacío para quitarlo.</FormDescription>
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
            {form.formState.isSubmitting ? "Guardando…" : "Guardar cambios"}
          </Button>
        </div>
      </form>
    </Form>
  );
}
