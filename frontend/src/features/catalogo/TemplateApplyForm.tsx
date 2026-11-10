"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Button } from "@/components/ui/button";
import { Form } from "@/components/ui/form";
import { PriceFields } from "@/features/catalogo/PriceFields";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import type { TemplateTerm, VehicleSize } from "@/types/api";
import { templateApplySchema, type TemplateApplyInput } from "@/validation/services";

interface TemplateApplyFormProps {
  terms: TemplateTerm[];
  sizes: VehicleSize[];
  /** Lo ya cargado por término (clave `glossary_code`): al volver a esta pantalla no se pierde. */
  drafts: Record<string, TemplateApplyInput["items"][number]>;
  onSubmit: (values: TemplateApplyInput) => Promise<void>;
  /** Devuelve lo cargado hasta ahora para que quien lo llama lo conserve. */
  onBack: (items: TemplateApplyInput["items"]) => void;
}

/**
 * Precio y duración de CADA tamaño para cada servicio elegido de la plantilla: no se aplica sin
 * precio completo (A8). Las plantillas traen nombres, no precios.
 */
export function TemplateApplyForm({
  terms,
  sizes,
  drafts,
  onSubmit,
  onBack,
}: TemplateApplyFormProps) {
  const form = useForm<TemplateApplyInput>({
    resolver: zodResolver(templateApplySchema),
    defaultValues: {
      items: terms.map(
        (t) =>
          drafts[t.glossary_code] ?? {
            glossary_code: t.glossary_code,
            name: t.name,
            pricing_mode: "PRECIO_FIJO" as const,
            prices: sizes.map((s) => ({
              vehicle_size_id: s.id,
              price_cents: undefined,
              duration_min: "",
            })),
          },
      ),
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);
  const count = terms.length;
  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-6">
        {terms.map((term, j) => (
          <section
            key={term.glossary_code}
            aria-label={`Precios de ${term.name}`}
            className="space-y-3 rounded-xl border bg-card p-4"
          >
            <h3 className="font-semibold">{term.name}</h3>
            <PriceFields sizes={sizes} basePath={`items.${j}`} />
          </section>
        ))}
        {formError && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {formError}
          </p>
        )}
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button type="button" variant="outline" onClick={() => onBack(form.getValues().items)}>
            Volver a la selección
          </Button>
          <Button type="submit" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting
              ? "Creando…"
              : `Crear ${count} ${count === 1 ? "servicio" : "servicios"}`}
          </Button>
        </div>
      </form>
    </Form>
  );
}
