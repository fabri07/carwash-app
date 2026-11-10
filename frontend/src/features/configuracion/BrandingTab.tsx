"use client";

import { useId, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ImageIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { BrandingForm, PaletteSwatches } from "@/features/configuracion/BrandingForm";
import { configKeys } from "@/features/configuracion/queries";
import { SettingsState, useSave } from "@/features/configuracion/SettingsTabs";
import { findPalette, useBrandingCatalog, validateLogo } from "@/lib/branding";
import { apiErrorCode, brandingError } from "@/lib/errors";
import { deleteLogo, suggestPalette, uploadLogo } from "@/services/branding.service";
import { toast } from "@/stores/toastStore";
import type { BrandCatalog, BusinessSettings } from "@/types/api";

interface Suggestion {
  palette: string;
  /** El logo no se guardó (sin almacenamiento): la sugerencia sale igual. */
  logoSaved: boolean;
}

function LogoSection({
  settings,
  onSuggestion,
}: {
  settings: BusinessSettings;
  onSuggestion: (s: Suggestion) => void;
}) {
  const queryClient = useQueryClient();
  const inputId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  async function handleFile(file: File | undefined) {
    if (!file) return;
    setError(null);
    const invalid = await validateLogo(file);
    if (invalid) {
      setError(invalid);
      return;
    }
    setBusy(true);
    try {
      const res = await uploadLogo(file);
      onSuggestion({ palette: res.suggestion.palette, logoSaved: true });
      toast.success("Logo guardado.");
    } catch (e) {
      setError(brandingError(e));
      // Sin almacenamiento el logo no se guarda, pero la paleta se puede sugerir igual.
      if (apiErrorCode(e) === "STORAGE_UNAVAILABLE") {
        try {
          const s = await suggestPalette(file);
          onSuggestion({ palette: s.palette, logoSaved: false });
        } catch {
          // Ya hay un mensaje en pantalla; la sugerencia es un extra.
        }
      }
    } finally {
      setBusy(false);
      await queryClient.invalidateQueries({ queryKey: configKeys.settings });
    }
  }

  async function handleDelete() {
    try {
      await deleteLogo();
    } catch (e) {
      throw new Error(brandingError(e));
    }
    await queryClient.invalidateQueries({ queryKey: configKeys.settings });
    toast.success("Logo quitado.");
  }

  const hasLogo = Boolean(settings.logo_key);
  return (
    <section aria-labelledby={`${inputId}-title`} className="space-y-3">
      <h2 id={`${inputId}-title`} className="text-sm font-medium">
        Logo
      </h2>
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex h-24 w-24 items-center justify-center overflow-hidden rounded-lg border bg-white">
          {settings.logo_url ? (
            // eslint-disable-next-line @next/next/no-img-element -- bucket público de R2, sin optimizar
            <img
              src={settings.logo_url}
              alt={`Logo de ${settings.name}`}
              className="h-full w-full object-contain"
            />
          ) : (
            <ImageIcon className="h-8 w-8 text-muted-foreground" aria-label="Sin logo" />
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <input
            id={inputId}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="sr-only"
            disabled={busy}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = ""; // permite volver a elegir el mismo archivo
              void handleFile(file);
            }}
          />
          <Button asChild variant="outline" aria-disabled={busy}>
            <label htmlFor={inputId} className="cursor-pointer">
              {busy ? "Subiendo…" : hasLogo ? "Cambiar logo" : "Subir logo"}
            </label>
          </Button>
          {hasLogo && (
            <Button variant="ghost" onClick={() => setConfirmOpen(true)} disabled={busy}>
              Quitar logo
            </Button>
          )}
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        PNG, JPG o WebP, de 200 a 4096 píxeles de lado y hasta 2 MB. Te sugerimos una paleta según
        sus colores.
      </p>
      {error && (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error}
        </p>
      )}
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="¿Quitar el logo?"
        description="Tu negocio se va a mostrar sin logo hasta que subas otro."
        confirmLabel="Quitar logo"
        variant="destructive"
        onConfirm={handleDelete}
      />
    </section>
  );
}

function SuggestionBanner({
  catalog,
  settings,
  suggestion,
  onApply,
}: {
  catalog: BrandCatalog;
  settings: BusinessSettings;
  suggestion: Suggestion;
  onApply: (palette: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const palette = findPalette(catalog, suggestion.palette);
  if (!palette) return null;
  const applied = settings.palette === palette.code;
  return (
    <div role="status" className="flex flex-wrap items-center gap-3 rounded-lg border bg-card p-3">
      <PaletteSwatches palette={palette} />
      <p className="flex-1 text-sm">
        {applied
          ? `Estás usando la paleta ${palette.name}, la que mejor combina con tu logo.`
          : `Según tu logo, te sugerimos la paleta ${palette.name}.`}
        {!suggestion.logoSaved && " (El logo no se guardó.)"}
      </p>
      {!applied && (
        <Button
          size="sm"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await onApply(palette.code);
            } catch (e) {
              toast.error(e instanceof Error ? e.message : "No se pudo aplicar la paleta.");
            } finally {
              setBusy(false);
            }
          }}
        >
          Aplicar paleta
        </Button>
      )}
    </div>
  );
}

/** Pestaña Personalización: solo el dueño (PR 4.5). */
export function BrandingTab() {
  const save = useSave();
  const catalog = useBrandingCatalog();
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);

  if (catalog.isLoading) return <p className="text-sm text-muted-foreground">Cargando…</p>;
  if (catalog.isError || !catalog.data) {
    return (
      <p role="alert" className="text-sm font-medium text-destructive">
        No se pudieron cargar las paletas. Probá de nuevo en unos minutos.
      </p>
    );
  }
  const data = catalog.data;
  return (
    <SettingsState>
      {(settings) => (
        <div className="space-y-6">
          <LogoSection settings={settings} onSuggestion={setSuggestion} />
          {suggestion && (
            <SuggestionBanner
              catalog={data}
              settings={settings}
              suggestion={suggestion}
              onApply={(palette) => save({ palette }, "Paleta aplicada.")}
            />
          )}
          <BrandingForm
            // Re-arma el formulario con lo guardado (también al aplicar la sugerencia).
            key={`${settings.palette}-${settings.font}`}
            catalog={data}
            settings={settings}
            onSubmit={(v) =>
              save({ palette: v.palette, font: v.font }, "Personalización guardada.")
            }
          />
        </div>
      )}
    </SettingsState>
  );
}
