"use client";

import { Badge } from "@/components/ui/badge";
import type { SmartColumn } from "@/components/data/SmartTable";
import { BusinessHoursForm, WEEKDAYS } from "@/features/configuracion/BusinessHoursForm";
import { CatalogTab } from "@/features/configuracion/CatalogTab";
import { PaymentMethodForm } from "@/features/configuracion/PaymentMethodForm";
import { ResourceForm } from "@/features/configuracion/ResourceForm";
import { VehicleSizeForm } from "@/features/configuracion/VehicleSizeForm";
import { hhmm } from "@/lib/time";
import { bpsToPercent, formatPercent, parsePercent, percentToBps } from "@/lib/percent";
import {
  businessHoursApi,
  paymentMethodsApi,
  resourcesApi,
  vehicleSizesApi,
} from "@/services/config.service";
import type { BusinessHours, PaymentMethod, Resource, VehicleSize } from "@/types/api";
import type {
  BusinessHoursInput,
  PaymentMethodInput,
  ResourceInput,
  VehicleSizeInput,
} from "@/validation/catalog";

// ── Tamaños ─────────────────────────────────────────────────────────────────

const sizeColumns: SmartColumn<VehicleSize>[] = [
  { key: "label", header: "Nombre", hideable: false, className: "font-medium" },
  { key: "code", header: "Código" },
  { key: "sort_order", header: "Orden" },
];

export function VehicleSizesTab() {
  return (
    <CatalogTab<
      VehicleSize,
      VehicleSizeInput,
      Parameters<typeof vehicleSizesApi.create>[0],
      Parameters<typeof vehicleSizesApi.update>[1]
    >
      resource="vehicle-sizes"
      title="Tamaños de vehículo"
      singular="tamaño"
      api={vehicleSizesApi}
      columns={sizeColumns}
      emptyMessage="No hay tamaños. Creá al menos uno para poder cargar precios."
      exportFilename="tamanos"
      itemName={(s) => s.label}
      toCreate={(v) => ({ code: v.code, label: v.label, sort_order: Number(v.sort_order) })}
      toPatch={(v) => ({ label: v.label, sort_order: Number(v.sort_order) })}
      Form={VehicleSizeForm}
      deleteHint="Solo se puede quitar un tamaño que no tenga precios cargados."
    />
  );
}

// ── Puestos ─────────────────────────────────────────────────────────────────

const resourceColumns: SmartColumn<Resource>[] = [
  { key: "name", header: "Nombre", hideable: false, className: "font-medium" },
  { key: "sort_order", header: "Orden" },
];

export function ResourcesTab() {
  return (
    <CatalogTab<
      Resource,
      ResourceInput,
      Parameters<typeof resourcesApi.create>[0],
      Parameters<typeof resourcesApi.update>[1]
    >
      resource="resources"
      title="Puestos"
      singular="puesto"
      api={resourcesApi}
      columns={resourceColumns}
      emptyMessage="No hay puestos. Cada puesto es un lugar donde se atiende un vehículo a la vez."
      exportFilename="puestos"
      itemName={(r) => r.name}
      toCreate={(v) => ({ name: v.name, sort_order: Number(v.sort_order) })}
      toPatch={(v) => ({ name: v.name, sort_order: Number(v.sort_order) })}
      Form={ResourceForm}
      deleteHint="No se puede quitar un puesto que tenga turnos próximos."
    />
  );
}

// ── Horarios ────────────────────────────────────────────────────────────────

const hoursColumns: SmartColumn<BusinessHours>[] = [
  {
    key: "weekday",
    header: "Día",
    hideable: false,
    className: "font-medium",
    cell: (h) => WEEKDAYS[h.weekday - 1],
    csvValue: (h) => WEEKDAYS[h.weekday - 1],
  },
  {
    key: "opens_at",
    header: "Abre",
    cell: (h) => hhmm(h.opens_at),
    csvValue: (h) => hhmm(h.opens_at),
  },
  {
    key: "closes_at",
    header: "Cierra",
    cell: (h) => hhmm(h.closes_at),
    csvValue: (h) => hhmm(h.closes_at),
  },
];

export function BusinessHoursTab() {
  return (
    <CatalogTab<
      BusinessHours,
      BusinessHoursInput,
      Parameters<typeof businessHoursApi.create>[0],
      Parameters<typeof businessHoursApi.update>[1]
    >
      resource="business-hours"
      title="Horarios de atención"
      singular="franja"
      feminine
      api={businessHoursApi}
      columns={hoursColumns}
      emptyMessage="No hay horarios cargados. Cargá los días y horas en que atendés."
      exportFilename="horarios"
      itemName={(h) => `${WEEKDAYS[h.weekday - 1]} ${hhmm(h.opens_at)}`}
      toCreate={(v) => ({
        weekday: Number(v.weekday),
        opens_at: v.opens_at,
        closes_at: v.closes_at,
      })}
      toPatch={(v) => ({
        weekday: Number(v.weekday),
        opens_at: v.opens_at,
        closes_at: v.closes_at,
      })}
      Form={BusinessHoursForm}
      deleteHint="Se quita esa franja de atención; las otras del día quedan como están."
    />
  );
}

// ── Medios de pago ──────────────────────────────────────────────────────────

const methodColumns: SmartColumn<PaymentMethod>[] = [
  { key: "label", header: "Nombre", hideable: false, className: "font-medium" },
  { key: "code", header: "Código" },
  {
    key: "commission_bps",
    header: "Comisión",
    cell: (m) => formatPercent(m.commission_bps),
    csvValue: (m) => bpsToPercent(m.commission_bps),
  },
  {
    key: "uses",
    header: "Se usa para",
    cell: (m) => (
      <div className="flex gap-1">
        {m.for_income && <Badge variant="outline">Cobros</Badge>}
        {m.for_expense && <Badge variant="outline">Gastos</Badge>}
      </div>
    ),
    csvValue: (m) =>
      [m.for_income && "cobros", m.for_expense && "gastos"].filter(Boolean).join(", "),
  },
  { key: "settlement_account", header: "Cuenta", defaultVisible: false },
];

function commissionBps(input: string): number {
  return percentToBps(parsePercent(input) ?? 0);
}

export function PaymentMethodsTab() {
  return (
    <CatalogTab<
      PaymentMethod,
      PaymentMethodInput,
      Parameters<typeof paymentMethodsApi.create>[0],
      Parameters<typeof paymentMethodsApi.update>[1]
    >
      resource="payment-methods"
      title="Medios de pago"
      singular="medio de pago"
      api={paymentMethodsApi}
      columns={methodColumns}
      emptyMessage="No hay medios de pago. Cargá efectivo, transferencia, tarjeta…"
      exportFilename="medios-de-pago"
      itemName={(m) => m.label}
      toCreate={(v) => ({
        code: v.code,
        label: v.label,
        commission_bps: commissionBps(v.commission_percent),
        settlement_account: v.settlement_account === "" ? null : v.settlement_account,
        for_income: v.for_income,
        for_expense: v.for_expense,
      })}
      // `settlement_account: null` borra la cuenta; el código no se edita.
      toPatch={(v) => ({
        label: v.label,
        commission_bps: commissionBps(v.commission_percent),
        settlement_account: v.settlement_account === "" ? null : v.settlement_account,
        for_income: v.for_income,
        for_expense: v.for_expense,
      })}
      Form={PaymentMethodForm}
      deleteHint="No se puede quitar el último medio de pago: el negocio quedaría sin forma de cobrar."
    />
  );
}
