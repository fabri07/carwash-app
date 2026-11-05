"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Button } from "@/components/ui/button";
import { Form } from "@/components/ui/form";
import { TextField } from "@/features/configuracion/fields";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import type { BusinessSettings } from "@/types/api";
import { businessSchema, type BusinessInput } from "@/validation/settings";

interface BusinessFormProps {
  settings: BusinessSettings;
  onSubmit: (values: BusinessInput) => Promise<void>;
}

/** Datos del negocio: la dirección pública, cómo contactarlo y lo que ve el cliente al reservar. */
export function BusinessForm({ settings, onSubmit }: BusinessFormProps) {
  const form = useForm<BusinessInput>({
    resolver: zodResolver(businessSchema),
    defaultValues: {
      slug: settings.slug,
      address: settings.address ?? "",
      whatsapp_e164: settings.whatsapp_e164 ?? "",
      instagram_url: settings.instagram_url ?? "",
      welcome_text: settings.welcome_text ?? "",
      cancellation_policy: settings.cancellation_policy ?? "",
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);
  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="max-w-2xl space-y-4">
        <TextField
          control={form.control}
          name="slug"
          label="Dirección de tu página"
          hint="Es la parte final del link que compartís con tus clientes. Solo letras sin tilde, números y guiones."
        />
        <TextField control={form.control} name="address" label="Dirección del local" />
        <TextField
          control={form.control}
          name="whatsapp_e164"
          label="WhatsApp"
          inputMode="tel"
          placeholder="+5493515551234"
          hint="Con código de país, sin espacios."
        />
        <TextField
          control={form.control}
          name="instagram_url"
          label="Instagram"
          inputMode="url"
          placeholder="https://instagram.com/tu-negocio"
        />
        <TextField
          control={form.control}
          name="welcome_text"
          label="Mensaje de bienvenida"
          multiline
          hint="Hasta 280 caracteres. Lo ve el cliente al entrar a reservar."
        />
        <TextField
          control={form.control}
          name="cancellation_policy"
          label="Política de cancelación"
          multiline
        />
        {formError && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {formError}
          </p>
        )}
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? "Guardando…" : "Guardar cambios"}
        </Button>
      </form>
    </Form>
  );
}
