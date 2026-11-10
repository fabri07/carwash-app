"use client";

import { useId, useMemo } from "react";

import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import {
  ALL,
  useVehicleColors,
  useVehicleMakes,
  useVehicleModels,
} from "@/features/vehiculos/queries";
import { useAllVehicleSizes } from "@/features/configuracion/queries";
import type { VehicleModel } from "@/types/api";
import { bodyTypeLabel } from "@/validation/vehicles";

/** Lo elegido. `null` = sin elegir (el texto libre del vehículo queda de respaldo). */
export interface VehicleSelection {
  make_id: string | null;
  model_id: string | null;
  color_id: string | null;
}

export const EMPTY_SELECTION: VehicleSelection = { make_id: null, model_id: null, color_id: null };

interface VehiclePickerProps {
  value: VehicleSelection;
  onChange: (next: VehicleSelection) => void;
  /**
   * El tamaño que sugiere el modelo elegido (`body_type_sizes`, A10), o `null` si no sugiere o
   * se sacó el modelo. Quien usa el selector decide si lo aplica: es una sugerencia.
   */
  onSuggestSize?: (vehicleSizeId: string | null, model: VehicleModel | null) => void;
  disabled?: boolean;
}

/**
 * Selector marca → modelo → color del catálogo del negocio (FASE-4-CONTRATO D4-8, A10). Lo usa
 * la carga de vehículos (F6). Solo ofrece filas visibles: lo oculto no se elige.
 *
 * Cambiar la marca borra el modelo (el modelo es de la marca). Elegir un modelo informa el tamaño
 * sugerido por su carrocería y lo muestra debajo.
 */
export function VehiclePicker({ value, onChange, onSuggestSize, disabled }: VehiclePickerProps) {
  const id = useId();
  const makesQuery = useVehicleMakes({ ...ALL, q: "", include_hidden: false });
  const modelsQuery = useVehicleModels(
    { ...ALL, q: "", include_hidden: false, make_id: value.make_id ?? undefined },
    value.make_id !== null,
  );
  const colorsQuery = useVehicleColors({ ...ALL, q: "", include_hidden: false });
  const sizesQuery = useAllVehicleSizes();

  const makes = makesQuery.data?.items ?? [];
  // `keepPreviousData` deja a mano los modelos de la marca anterior mientras llegan los nuevos:
  // se filtran por la marca elegida para que nunca quede {Toyota, Ranger}.
  const models = useMemo(
    () =>
      value.make_id
        ? (modelsQuery.data?.items ?? []).filter((m) => m.make_id === value.make_id)
        : [],
    [modelsQuery.data, value.make_id],
  );
  const loadingModels =
    value.make_id !== null && (modelsQuery.isPlaceholderData || modelsQuery.isLoading);
  const colors = colorsQuery.data?.items ?? [];
  const model = models.find((m) => m.id === value.model_id) ?? null;
  const suggested = model?.suggested_vehicle_size_id
    ? sizesQuery.data?.items.find((s) => s.id === model.suggested_vehicle_size_id)
    : undefined;

  function changeMake(makeId: string) {
    const next = { ...value, make_id: makeId || null, model_id: null };
    onChange(next);
    if (value.model_id !== null) onSuggestSize?.(null, null);
  }

  function changeModel(modelId: string) {
    // Solo un modelo de la marca elegida (`models` ya viene filtrado): otro id cuenta como ninguno.
    const picked = models.find((m) => m.id === modelId) ?? null;
    onChange({ ...value, model_id: picked?.id ?? null });
    onSuggestSize?.(picked?.suggested_vehicle_size_id ?? null, picked);
  }

  return (
    <div className="grid gap-4 sm:grid-cols-3">
      <div className="space-y-2">
        <Label htmlFor={`${id}-make`}>Marca</Label>
        <NativeSelect
          id={`${id}-make`}
          value={value.make_id ?? ""}
          disabled={disabled || makesQuery.isLoading}
          onChange={(e) => changeMake(e.target.value)}
        >
          <option value="">{makesQuery.isLoading ? "Cargando…" : "Elegí la marca"}</option>
          {makes.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </NativeSelect>
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${id}-model`}>Modelo</Label>
        <NativeSelect
          id={`${id}-model`}
          value={value.model_id ?? ""}
          disabled={disabled || value.make_id === null || loadingModels}
          onChange={(e) => changeModel(e.target.value)}
        >
          <option value="">
            {value.make_id === null
              ? "Primero elegí la marca"
              : loadingModels
                ? "Cargando…"
                : "Elegí el modelo"}
          </option>
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </NativeSelect>
        {model && (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {bodyTypeLabel(model.body_type)}
            {suggested ? ` · Tamaño sugerido: ${suggested.label}` : " · Sin tamaño sugerido"}
          </p>
        )}
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${id}-color`}>Color</Label>
        <NativeSelect
          id={`${id}-color`}
          value={value.color_id ?? ""}
          disabled={disabled || colorsQuery.isLoading}
          onChange={(e) => onChange({ ...value, color_id: e.target.value || null })}
        >
          <option value="">Sin color</option>
          {colors.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </NativeSelect>
      </div>
    </div>
  );
}
