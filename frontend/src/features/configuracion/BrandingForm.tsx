"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Check } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Form } from "@/components/ui/form";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import { fontStack } from "@/lib/branding";
import { cn } from "@/lib/utils";
import type { BrandCatalog, BrandPalette, BusinessSettings } from "@/types/api";
import { brandingSchema, type BrandingInput } from "@/validation/settings";

interface BrandingFormProps {
  catalog: BrandCatalog;
  settings: BusinessSettings;
  onSubmit: (values: BrandingInput) => Promise<void>;
}

/** Tres círculos: fondo, primario y secundario de la paleta. */
export function PaletteSwatches({ palette }: { palette: BrandPalette }) {
  return (
    <span className="flex" aria-hidden>
      {[palette.background, palette.primary, palette.secondary].map((color, i) => (
        <span
          key={i}
          className="-ml-1 h-6 w-6 rounded-full border border-black/10 first:ml-0"
          style={{ background: color.hex }}
        />
      ))}
    </span>
  );
}

/**
 * Paleta y tipografía del negocio (PR 4.5). Se elige una paleta entera, nunca un color suelto:
 * así el texto sobre los botones siempre se lee. La vista previa muestra cómo queda antes de
 * guardar; al guardar se repinta toda la app.
 */
export function BrandingForm({ catalog, settings, onSubmit }: BrandingFormProps) {
  const form = useForm<BrandingInput>({
    resolver: zodResolver(brandingSchema),
    defaultValues: { palette: settings.palette, font: settings.font },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);
  const paletteCode = form.watch("palette");
  const fontCode = form.watch("font");
  const palette =
    catalog.palettes.find((p) => p.code === paletteCode) ??
    catalog.palettes.find((p) => p.code === catalog.default_palette);

  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="max-w-3xl space-y-6">
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Paleta de colores</legend>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {catalog.palettes.map((p) => (
              <label
                key={p.code}
                className={cn(
                  "flex min-h-touch cursor-pointer items-center gap-3 rounded-lg border bg-card p-3",
                  "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring",
                  paletteCode === p.code && "border-primary ring-1 ring-primary",
                )}
              >
                <input
                  type="radio"
                  value={p.code}
                  className="sr-only"
                  {...form.register("palette")}
                />
                <PaletteSwatches palette={p} />
                <span className="flex-1 text-sm">{p.name}</span>
                {paletteCode === p.code && <Check className="h-4 w-4 text-primary" aria-hidden />}
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Tipografía</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {catalog.fonts.map((f) => (
              <label
                key={f.code}
                className={cn(
                  "flex min-h-touch cursor-pointer flex-col gap-1 rounded-lg border bg-card p-3",
                  "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring",
                  fontCode === f.code && "border-primary ring-1 ring-primary",
                )}
              >
                <input type="radio" value={f.code} className="sr-only" {...form.register("font")} />
                <span className="text-base font-semibold" style={{ fontFamily: fontStack(f.code) }}>
                  {f.name}
                </span>
                <span className="text-xs text-muted-foreground">{f.description}</span>
              </label>
            ))}
          </div>
        </fieldset>

        {palette && (
          <div
            data-testid="branding-preview"
            aria-label="Vista previa"
            className="space-y-3 rounded-lg border p-4"
            style={{ background: palette.background.hex, fontFamily: fontStack(fontCode) }}
          >
            <p className="text-lg font-semibold text-foreground">Lavado completo · $ 25.000</p>
            <p className="text-sm text-muted-foreground">
              Así ven tu marca tus clientes y tu equipo.
            </p>
            <span className="flex flex-wrap items-center gap-2">
              <span
                className="inline-flex min-h-touch items-center rounded-md px-4 text-sm font-medium"
                style={{ background: palette.primary.hex, color: palette.on_primary.hex }}
              >
                Reservar turno
              </span>
              <span
                className="h-3 w-12 rounded-full"
                style={{ background: palette.secondary.hex }}
                aria-hidden
              />
            </span>
          </div>
        )}

        {formError && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {formError}
          </p>
        )}
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? "Guardando…" : "Guardar personalización"}
        </Button>
      </form>
    </Form>
  );
}
