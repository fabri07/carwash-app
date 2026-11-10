"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { skipReasonLabel } from "@/features/catalogo/format";
import { catalogKeys, useAllVehicleSizes, useTemplate } from "@/features/catalogo/queries";
import { TemplateApplyForm } from "@/features/catalogo/TemplateApplyForm";
import { configKeys } from "@/features/configuracion/queries";
import { FormSubmitError, serviceError } from "@/lib/errors";
import { applyTemplate } from "@/services/catalog.service";
import { toast } from "@/stores/toastStore";
import type { TemplateApplyResult, TemplateKindCode, TemplateTerm } from "@/types/api";
import { toPriceItems, type TemplateApplyInput } from "@/validation/services";

const KINDS: { kind: TemplateKindCode; title: string; description: string }[] = [
  { kind: "lavadero", title: "Lavadero", description: "Lavado y limpieza interior." },
  {
    kind: "detailing",
    title: "Detailing",
    description: "Lavado, interior, exterior y corrección de pintura, y protección.",
  },
];

type Step = "pick" | "price" | "done";

/**
 * Plantillas de servicios (solo el dueño). Se elige un tipo de negocio, se tildan los servicios que
 * se ofrecen y se carga precio y duración para cada tamaño: la plantilla trae nombres, no precios.
 * Aplicar es idempotente: lo que ya existe se informa como salteado, sin tocar sus precios.
 */
export function TemplatesTab() {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<TemplateKindCode | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [step, setStep] = useState<Step>("pick");
  // Los términos del paso de precios se congelan al entrar: un refetch de la plantilla (por ejemplo,
  // otro término pasa a `already_added`) no puede cambiar el orden ni desmontar el formulario.
  const [snapshot, setSnapshot] = useState<TemplateTerm[]>([]);
  const [drafts, setDrafts] = useState<Record<string, TemplateApplyInput["items"][number]>>({});
  const [result, setResult] = useState<TemplateApplyResult | null>(null);

  const template = useTemplate(kind);
  const sizesQuery = useAllVehicleSizes();
  const sizes = sizesQuery.data?.items ?? [];
  const noSizes = sizesQuery.isSuccess && sizes.length === 0;

  const allTerms: TemplateTerm[] = (template.data?.categories ?? []).flatMap((c) => c.items);
  const chosen = allTerms.filter((t) => selected.has(t.glossary_code) && !t.already_added);

  function chooseKind(next: TemplateKindCode) {
    if (next === kind) return;
    setKind(next);
    setSelected(new Set());
  }

  function toggle(code: string, on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(code);
      else next.delete(code);
      return next;
    });
  }

  function toggleGroup(items: TemplateTerm[]) {
    const free = items.filter((t) => !t.already_added);
    const allOn = free.every((t) => selected.has(t.glossary_code));
    setSelected((prev) => {
      const next = new Set(prev);
      free.forEach((t) => (allOn ? next.delete(t.glossary_code) : next.add(t.glossary_code)));
      return next;
    });
  }

  async function apply(values: TemplateApplyInput) {
    if (!kind) return;
    let res: TemplateApplyResult;
    try {
      res = await applyTemplate({
        kind,
        items: values.items.map((i) => ({
          glossary_code: i.glossary_code,
          pricing_mode: i.pricing_mode,
          prices: toPriceItems(i.prices),
        })),
      });
    } catch (e) {
      throw new FormSubmitError(serviceError(e, "prices"));
    }
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: catalogKeys.services }),
      queryClient.invalidateQueries({ queryKey: configKeys.resource("service-categories") }),
      queryClient.invalidateQueries({ queryKey: catalogKeys.template(kind) }),
    ]);
    setResult(res);
    setSelected(new Set());
    setDrafts({});
    setStep("done");
    toast.success(
      res.created.length === 1
        ? "Se creó 1 servicio."
        : `Se crearon ${res.created.length} servicios.`,
    );
  }

  if (step === "done" && result) {
    const names = new Map(allTerms.map((t) => [t.glossary_code, t.name]));
    return (
      <section aria-label="Resultado" className="space-y-4 rounded-xl border bg-card p-4">
        <h2 className="text-lg font-semibold">Plantilla aplicada</h2>
        <p>
          {result.created.length === 1
            ? "Se creó 1 servicio."
            : `Se crearon ${result.created.length} servicios.`}
          {result.categories_created.length > 0 &&
            ` Categorías nuevas: ${result.categories_created.map((c) => c.name).join(", ")}.`}
        </p>
        {result.created.length > 0 && (
          <ul className="list-disc pl-5 text-sm">
            {result.created.map((s) => (
              <li key={s.id}>{s.name}</li>
            ))}
          </ul>
        )}
        {result.skipped.length > 0 && (
          <div>
            <h3 className="font-medium">Salteados ({result.skipped.length})</h3>
            <ul className="list-disc pl-5 text-sm text-muted-foreground">
              {result.skipped.map((s) => (
                <li key={s.glossary_code}>
                  {`${names.get(s.glossary_code) ?? s.glossary_code}: ${skipReasonLabel(s.reason)}`}
                </li>
              ))}
            </ul>
          </div>
        )}
        <Button
          type="button"
          onClick={() => {
            setResult(null);
            setStep("pick");
          }}
        >
          Elegir más servicios
        </Button>
      </section>
    );
  }

  if (step === "price" && snapshot.length > 0) {
    return (
      <section aria-label="Cargar precios" className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold">Precios de los servicios elegidos</h2>
          <p className="text-sm text-muted-foreground">
            Cargá precio y duración para cada tamaño de vehículo. Sin eso no se puede crear.
          </p>
        </div>
        <TemplateApplyForm
          terms={snapshot}
          sizes={sizes}
          drafts={drafts}
          onSubmit={apply}
          onBack={(items) => {
            setDrafts(Object.fromEntries(items.map((i) => [i.glossary_code, i])));
            setStep("pick");
          }}
        />
      </section>
    );
  }

  return (
    <section aria-label="Plantillas" className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">Partir de una plantilla</h2>
        <p className="text-sm text-muted-foreground">
          Elegí el tipo de negocio y tildá los servicios que ofrecés. Después les ponés precio.
        </p>
      </div>

      <div role="group" aria-label="Tipo de negocio" className="grid gap-2 sm:grid-cols-2">
        {KINDS.map((k) => (
          <button
            key={k.kind}
            type="button"
            aria-pressed={kind === k.kind}
            onClick={() => chooseKind(k.kind)}
            className="min-h-touch rounded-xl border p-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-pressed:border-primary aria-pressed:bg-accent"
          >
            <span className="block font-semibold">{k.title}</span>
            <span className="block text-sm text-muted-foreground">{k.description}</span>
          </button>
        ))}
      </div>

      {noSizes && (
        <p role="alert" className="text-sm text-destructive">
          Antes de aplicar una plantilla cargá al menos un tamaño de vehículo en Negocio → Tamaños.
        </p>
      )}

      {kind && template.isLoading && <p className="text-sm text-muted-foreground">Cargando…</p>}
      {kind && template.isError && (
        <p role="alert" className="text-sm text-destructive">
          {serviceError(template.error).message}
        </p>
      )}

      {template.data?.categories.map((group) => (
        <fieldset key={group.name} className="space-y-1 rounded-xl border bg-card p-3">
          <legend className="px-1 text-sm font-semibold">{group.name}</legend>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => toggleGroup(group.items)}
            disabled={group.items.every((t) => t.already_added)}
          >
            {`Elegir o quitar todos en ${group.name}`}
          </Button>
          <div className="grid gap-x-4 sm:grid-cols-2">
            {group.items.map((term) => (
              <label
                key={term.glossary_code}
                className="flex min-h-touch items-center gap-3 text-sm"
              >
                <Checkbox
                  checked={term.already_added || selected.has(term.glossary_code)}
                  disabled={term.already_added}
                  onCheckedChange={(on) => toggle(term.glossary_code, on === true)}
                />
                <span className="flex flex-wrap items-center gap-2">
                  {term.name}
                  {term.already_added && <Badge variant="outline">Ya lo tenés</Badge>}
                </span>
              </label>
            ))}
          </div>
        </fieldset>
      ))}

      {template.data && (
        <div className="flex justify-end">
          <Button
            type="button"
            disabled={chosen.length === 0 || noSizes || !sizesQuery.isSuccess}
            onClick={() => {
              setSnapshot(chosen);
              setStep("price");
            }}
          >
            {`Cargar precios (${chosen.length})`}
          </Button>
        </div>
      )}
    </section>
  );
}
