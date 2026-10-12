"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

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
import { Textarea } from "@/components/ui/textarea";
import { FormActions } from "@/features/configuracion/FormActions";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import type { SettingsPatch, TenantSettings } from "@/types/api";
import { businessSchema, WELCOME_TEXT_MAX, type BusinessInput } from "@/validation/configuracion";

type BusinessFields = Pick<
  TenantSettings,
  "slug" | "address" | "whatsapp_e164" | "instagram_url" | "welcome_text" | "cancellation_policy"
>;

interface BusinessFormProps {
  settings?: BusinessFields;
  /** Recibe solo los campos de esta sección, listos para `PATCH /settings`. */
  onSubmit: (payload: SettingsPatch) => Promise<void>;
  onCancel?: () => void;
  submitLabel?: string;
}

/** Vacío es "sin dato": se manda `null` para que el backend lo borre. */
const orNull = (v: string) => (v === "" ? null : v);

export function toBusinessPatch(v: BusinessInput): SettingsPatch {
  return {
    slug: v.slug,
    address: orNull(v.address),
    whatsapp_e164: orNull(v.whatsapp),
    instagram_url: orNull(v.instagram_url),
    welcome_text: orNull(v.welcome_text),
    cancellation_policy: orNull(v.cancellation_policy),
  };
}

/**
 * Datos del negocio: dirección de la página, contacto y textos para los
 * clientes. Suelto para reusarlo en "Mi negocio" y en el wizard de alta (§6).
 */
export function BusinessForm({
  settings,
  onSubmit,
  onCancel,
  submitLabel = "Guardar",
}: BusinessFormProps) {
  const form = useForm<BusinessInput>({
    resolver: zodResolver(businessSchema),
    defaultValues: {
      slug: settings?.slug ?? "",
      address: settings?.address ?? "",
      whatsapp: settings?.whatsapp_e164 ?? "",
      instagram_url: settings?.instagram_url ?? "",
      welcome_text: settings?.welcome_text ?? "",
      cancellation_policy: settings?.cancellation_policy ?? "",
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, (v) => onSubmit(toBusinessPatch(v)));
  const welcomeLength = form.watch("welcome_text").length;

  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <FormField
          control={form.control}
          name="slug"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Dirección de tu página</FormLabel>
              <FormControl>
                <Input autoComplete="off" autoCapitalize="none" spellCheck={false} {...field} />
              </FormControl>
              <FormDescription>
                Va en el link que les pasás a tus clientes para sacar turno. Solo minúsculas,
                números y guiones, por ejemplo <span className="font-mono">lavadero-del-sur</span>.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="address"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Dirección del local (opcional)</FormLabel>
              <FormControl>
                <Input autoComplete="street-address" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="whatsapp"
          render={({ field }) => (
            <FormItem>
              <FormLabel>WhatsApp (opcional)</FormLabel>
              <FormControl>
                <Input type="tel" inputMode="tel" autoComplete="tel" {...field} />
              </FormControl>
              <FormDescription>Con código de área, por ejemplo 11 2345-6789.</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="instagram_url"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Instagram (opcional)</FormLabel>
              <FormControl>
                <Input
                  type="url"
                  inputMode="url"
                  autoCapitalize="none"
                  placeholder="https://instagram.com/tu-lavadero"
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="welcome_text"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Texto de bienvenida (opcional)</FormLabel>
              <FormControl>
                <Textarea rows={3} {...field} />
              </FormControl>
              <FormDescription aria-live="polite">
                Lo ven tus clientes al sacar turno. {welcomeLength}/{WELCOME_TEXT_MAX} caracteres.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="cancellation_policy"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Política de cancelación (opcional)</FormLabel>
              <FormControl>
                <Textarea rows={4} {...field} />
              </FormControl>
              <FormDescription>
                Qué pasa si un cliente cancela tarde o no viene. Se le muestra antes de reservar.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormActions
          formError={formError}
          isSubmitting={form.formState.isSubmitting}
          submitLabel={submitLabel}
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
