"use client";

import { useMemo, useState } from "react";

import type { SmartColumn } from "@/components/data/SmartTable";
import { NativeSelect } from "@/components/ui/native-select";
import {
  ALL,
  useVehicleColors,
  useVehicleMakes,
  useVehicleModels,
} from "@/features/vehiculos/queries";
import { VehicleCatalogTable } from "@/features/vehiculos/VehicleCatalogTable";
import { VehicleModelForm } from "@/features/vehiculos/VehicleModelForm";
import { VehicleNameForm } from "@/features/vehiculos/VehicleNameForm";
import { vehicleColorsApi, vehicleMakesApi, vehicleModelsApi } from "@/services/vehicles.service";
import type { VehicleColor, VehicleMake, VehicleModel } from "@/types/api";
import { bodyTypeLabel, type MakeInput, type ModelInput } from "@/validation/vehicles";

// ── Marcas ──────────────────────────────────────────────────────────────────

export function MakesTab() {
  return (
    <VehicleCatalogTable<VehicleMake, MakeInput>
      title="Marcas"
      singular="marca"
      feminine
      searchPlaceholder="Buscar: toyota, volks…"
      emptyMessage="No hay marcas. Sumá las que atendés."
      exportFilename="marcas"
      useRows={useVehicleMakes}
      create={(v, key) => vehicleMakesApi.create({ name: v.name }, key)}
      update={(item, v) => vehicleMakesApi.update(item.id, { name: v.name })}
      setHidden={(item, hidden) => vehicleMakesApi.update(item.id, { hidden })}
      renderForm={(props) => <VehicleNameForm kind="make" {...props} />}
    />
  );
}

// ── Modelos ─────────────────────────────────────────────────────────────────

const modelColumns: SmartColumn<VehicleModel>[] = [
  {
    key: "body_type",
    header: "Carrocería",
    cell: (m) => bodyTypeLabel(m.body_type),
    csvValue: (m) => bodyTypeLabel(m.body_type),
  },
];

export function ModelsTab() {
  // Las ocultas también: un modelo de una marca oculta se administra igual.
  const makesQuery = useVehicleMakes({ ...ALL, q: "", include_hidden: true });
  const makes = useMemo(() => makesQuery.data?.items ?? [], [makesQuery.data]);
  const [makeId, setMakeId] = useState("");
  const make = makes.find((m) => m.id === makeId);

  return (
    <VehicleCatalogTable<VehicleModel, ModelInput>
      title="Modelos"
      singular="modelo"
      searchPlaceholder="Buscar: hilux, toyota corolla…"
      emptyMessage={
        makeId ? "Esta marca no tiene modelos. Sumá los que atendés." : "No hay modelos."
      }
      exportFilename="modelos"
      canCreate={make !== undefined}
      useRows={useVehicleModels}
      // Sin marca elegida se lista todo: con la búsqueda "toyota hilux" alcanza.
      scope={{ make_id: makeId || undefined }}
      extraColumns={
        makeId
          ? modelColumns
          : [
              {
                key: "make_name",
                header: "Marca",
                cell: (m) => m.make_name,
                csvValue: (m) => m.make_name,
              },
              ...modelColumns,
            ]
      }
      toolbarExtra={
        <NativeSelect
          aria-label="Marca"
          value={makeId}
          onChange={(e) => setMakeId(e.target.value)}
          className="sm:w-52"
        >
          <option value="">Todas las marcas</option>
          {makes.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
              {m.hidden_at ? " (oculta)" : ""}
            </option>
          ))}
        </NativeSelect>
      }
      create={(v, key) =>
        vehicleModelsApi.create({ make_id: makeId, name: v.name, body_type: v.body_type }, key)
      }
      update={(item, v) =>
        vehicleModelsApi.update(item.id, { name: v.name, body_type: v.body_type })
      }
      setHidden={(item, hidden) => vehicleModelsApi.update(item.id, { hidden })}
      renderForm={(props) => (
        <VehicleModelForm makeName={props.item?.make_name ?? make?.name ?? ""} {...props} />
      )}
    />
  );
}

// ── Colores ─────────────────────────────────────────────────────────────────

export function ColorsTab() {
  return (
    <VehicleCatalogTable<VehicleColor, MakeInput>
      title="Colores"
      singular="color"
      searchPlaceholder="Buscar: gris, azul…"
      emptyMessage="No hay colores. Sumá los que más ves."
      exportFilename="colores"
      useRows={useVehicleColors}
      create={(v, key) => vehicleColorsApi.create({ name: v.name }, key)}
      update={(item, v) => vehicleColorsApi.update(item.id, { name: v.name })}
      setHidden={(item, hidden) => vehicleColorsApi.update(item.id, { hidden })}
      renderForm={(props) => <VehicleNameForm kind="color" {...props} />}
    />
  );
}
