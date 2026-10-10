"use client";

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
import { useFormSubmit } from "@/hooks/useFormSubmit";
import { PERMISSION_GROUPS, PERMISSION_LABELS, sortPermissions } from "@/lib/permissions";
import type { PermissionProfile } from "@/types/api";
import { profileSchema, type ProfileInput } from "@/validation/team";

interface ProfileFormProps {
  /** Sin perfil: alta. Con perfil: edición. */
  profile?: PermissionProfile;
  onSubmit: (values: ProfileInput) => Promise<void>;
  onCancel: () => void;
}

/**
 * Perfil de permisos: nombre y permisos agrupados por área. Los permisos
 * salen del enum del contrato (`lib/permissions.ts`); no hay permiso para
 * "dar permisos" (Y5), así que un perfil nunca habilita a editar el equipo.
 */
export function ProfileForm({ profile, onSubmit, onCancel }: ProfileFormProps) {
  const form = useForm<ProfileInput>({
    resolver: zodResolver(profileSchema),
    defaultValues: { name: profile?.name ?? "", permissions: profile?.permissions ?? [] },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);

  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <FormField
          control={form.control}
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Nombre del perfil</FormLabel>
              <FormControl>
                <Input autoComplete="off" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="permissions"
          render={({ field }) => (
            <div className="space-y-3">
              {PERMISSION_GROUPS.map((group) => (
                <fieldset key={group.area} className="rounded-lg border p-3">
                  <legend className="px-1 text-sm font-semibold">{group.area}</legend>
                  {group.permissions.map((p) => (
                    <label key={p} className="flex min-h-touch items-center gap-3 text-sm">
                      <Checkbox
                        checked={field.value.includes(p)}
                        onCheckedChange={(checked) =>
                          field.onChange(
                            checked === true
                              ? sortPermissions([...field.value, p])
                              : field.value.filter((x) => x !== p),
                          )
                        }
                      />
                      {PERMISSION_LABELS[p]}
                    </label>
                  ))}
                </fieldset>
              ))}
            </div>
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
            {form.formState.isSubmitting
              ? "Guardando…"
              : profile
                ? "Guardar cambios"
                : "Crear perfil"}
          </Button>
        </div>
      </form>
    </Form>
  );
}
