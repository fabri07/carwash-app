"use client";

import { useQueryClient } from "@tanstack/react-query";

import { BusinessForm } from "@/features/configuracion/BusinessForm";
import { DepositForm } from "@/features/configuracion/DepositForm";
import { configKeys, useSettings } from "@/features/configuracion/queries";
import { configError, FormSubmitError } from "@/lib/errors";
import { inputToBps } from "@/lib/percent";
import { patchSettings } from "@/services/config.service";
import { toast } from "@/stores/toastStore";
import type { BusinessSettingsPatch } from "@/types/api";
import type { BusinessInput, DepositInput } from "@/validation/settings";

/** Guarda y deja el resultado en la caché: el formulario se vuelve a armar con lo guardado. */
function useSave() {
  const queryClient = useQueryClient();
  return async (patch: BusinessSettingsPatch, message: string) => {
    try {
      const saved = await patchSettings(patch);
      queryClient.setQueryData(configKeys.settings, saved);
    } catch (e) {
      throw new FormSubmitError(configError(e));
    }
    toast.success(message);
  };
}

function SettingsState({
  children,
}: {
  children: (s: NonNullable<ReturnType<typeof useSettings>["data"]>) => React.ReactNode;
}) {
  const query = useSettings();
  if (query.isLoading) return <p className="text-sm text-muted-foreground">Cargando…</p>;
  if (query.isError || !query.data) {
    return (
      <p role="alert" className="text-sm font-medium text-destructive">
        {query.isError ? configError(query.error).message : "No se pudo cargar la configuración."}
      </p>
    );
  }
  return <>{children(query.data)}</>;
}

/** Pestaña Negocio: solo el dueño. */
export function BusinessTab() {
  const save = useSave();
  return (
    <SettingsState>
      {(settings) => (
        <BusinessForm
          // Re-arma el formulario con lo guardado (la URL o los textos normalizados por el backend).
          key={settings.slug + (settings.address ?? "") + (settings.welcome_text ?? "")}
          settings={settings}
          onSubmit={(v: BusinessInput) =>
            save(
              {
                // Sin cambios en el slug no se manda: no choca consigo mismo.
                ...(v.slug !== settings.slug ? { slug: v.slug } : {}),
                address: v.address,
                whatsapp_e164: v.whatsapp_e164,
                instagram_url: v.instagram_url,
                welcome_text: v.welcome_text,
                cancellation_policy: v.cancellation_policy,
              },
              "Datos del negocio guardados.",
            )
          }
        />
      )}
    </SettingsState>
  );
}

/** Pestaña Seña y reservas: solo el dueño. */
export function DepositTab() {
  const save = useSave();
  return (
    <SettingsState>
      {(settings) => (
        <DepositForm
          key={`${settings.deposit_bps}-${settings.deposit_enabled}`}
          settings={settings}
          onSubmit={(v: DepositInput) =>
            save(
              {
                deposit_enabled: v.deposit_enabled,
                deposit_bps: inputToBps(v.deposit_percent) ?? 0,
                hold_min: Number(v.hold_min),
                min_advance_min: Number(v.min_advance_min),
                tolerance_min: Number(v.tolerance_min),
                late_cancel_threshold_min: Number(v.late_cancel_threshold_min),
                quote_validity_hours: Number(v.quote_validity_hours),
              },
              "Seña y reservas guardadas.",
            )
          }
        />
      )}
    </SettingsState>
  );
}
