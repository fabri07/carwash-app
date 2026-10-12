"use client";

import { Lock } from "lucide-react";

import type { SmartColumn } from "@/components/data/SmartTable";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { BusinessHourForm } from "@/features/configuracion/BusinessHourForm";
import { CatalogTab } from "@/features/configuracion/CatalogTab";
import { bpsAsPercent, shortTime, weekdayLabel } from "@/features/configuracion/labels";
import { PaymentMethodForm } from "@/features/configuracion/PaymentMethodForm";
import { ResourceForm } from "@/features/configuracion/ResourceForm";
import { VehicleSizeForm } from "@/features/configuracion/VehicleSizeForm";
import { usePermission } from "@/hooks/usePermission";
import {
  createBusinessHour,
  createPaymentMethod,
  createResource,
  createVehicleSize,
  deleteBusinessHour,
  deletePaymentMethod,
  deleteResource,
  deleteVehicleSize,
  listBusinessHours,
  listPaymentMethods,
  listResources,
  listVehicleSizes,
  updateBusinessHour,
  updatePaymentMethod,
  updateResource,
  updateVehicleSize,
} from "@/services/configuracion.service";
import type { BusinessHour, PaymentMethod, Resource, VehicleSize } from "@/types/api";

const hourName = (h: BusinessHour) =>
  `el horario del ${weekdayLabel(h.weekday).toLowerCase()} de ${shortTime(h.opens_at)} a ${shortTime(h.closes_at)}`;

const hourColumns: SmartColumn<BusinessHour>[] = [
  {
    key: "weekday",
    header: "Día",
    hideable: false,
    className: "font-medium",
    cell: (h) => weekdayLabel(h.weekday),
    csvValue: (h) => weekdayLabel(h.weekday),
  },
  {
    key: "opens_at",
    header: "Abre",
    cell: (h) => shortTime(h.opens_at),
    csvValue: (h) => shortTime(h.opens_at),
  },
  {
    key: "closes_at",
    header: "Cierra",
    cell: (h) => shortTime(h.closes_at),
    csvValue: (h) => shortTime(h.closes_at),
  },
];

const resourceColumns: SmartColumn<Resource>[] = [
  { key: "name", header: "Puesto", hideable: false, className: "font-medium" },
  { key: "sort_order", header: "Orden", defaultVisible: false },
];

function paymentUses(m: PaymentMethod): string[] {
  return [m.for_income && "Cobrar", m.for_expense && "Pagar gastos"].filter(
    (x): x is string => typeof x === "string",
  );
}

const paymentColumns: SmartColumn<PaymentMethod>[] = [
  { key: "label", header: "Medio de pago", hideable: false, className: "font-medium" },
  { key: "code", header: "Código" },
  {
    key: "commission_bps",
    header: "Comisión",
    cell: (m) => bpsAsPercent(m.commission_bps),
    csvValue: (m) => bpsAsPercent(m.commission_bps),
  },
  { key: "settlement_account", header: "Cuenta de destino" },
  {
    key: "uses",
    header: "Se usa para",
    cell: (m) => (
      <div className="flex flex-wrap gap-1">
        {paymentUses(m).map((u) => (
          <Badge key={u} variant="outline">
            {u}
          </Badge>
        ))}
      </div>
    ),
    csvValue: (m) => paymentUses(m).join(", "),
  },
];

const sizeColumns: SmartColumn<VehicleSize>[] = [
  { key: "label", header: "Tamaño", hideable: false, className: "font-medium" },
  { key: "code", header: "Código" },
  { key: "sort_order", header: "Orden", defaultVisible: false },
];

function HoursTab() {
  return (
    <CatalogTab
      catalog="business-hours"
      service={{
        list: listBusinessHours,
        create: createBusinessHour,
        update: updateBusinessHour,
        remove: deleteBusinessHour,
      }}
      columns={hourColumns}
      errorScope="hours"
      sortItems={(a, b) => a.weekday - b.weekday || a.opens_at.localeCompare(b.opens_at)}
      copy={{
        tableLabel: "Horarios de atención",
        newLabel: "Nuevo horario",
        newTitle: "Nuevo horario",
        editTitle: (h) => `Editar el horario del ${weekdayLabel(h.weekday).toLowerCase()}`,
        dialogDescription:
          "Si cortás al mediodía, cargá dos horarios para el mismo día. Un día sin horarios queda cerrado.",
        emptyMessage: "No hay horarios. Sin horarios no se pueden dar turnos.",
        exportFilename: "horarios",
        rowName: hourName,
        deleteTitle: (h) => `¿Borrar ${hourName(h)}?`,
        deleteDescription: "Los turnos ya dados no se tocan.",
        created: "Horario agregado.",
        deleted: () => "Horario borrado.",
        replayed: "Ese horario ya se había agregado. Revisá en la lista cómo quedó.",
      }}
      renderForm={({ row, onSubmit, onCancel }) => (
        <BusinessHourForm businessHour={row} onSubmit={onSubmit} onCancel={onCancel} />
      )}
    />
  );
}

function ResourcesTab() {
  return (
    <CatalogTab
      catalog="resources"
      service={{
        list: listResources,
        create: createResource,
        update: updateResource,
        remove: deleteResource,
      }}
      columns={resourceColumns}
      copy={{
        tableLabel: "Puestos",
        newLabel: "Nuevo puesto",
        newTitle: "Nuevo puesto",
        editTitle: (r) => `Editar ${r.name}`,
        dialogDescription:
          "Un puesto es un lugar donde se trabaja un auto a la vez: un box, una rampa, una cabina.",
        emptyMessage: "No hay puestos. Cargá al menos uno para poder dar turnos.",
        exportFilename: "puestos",
        rowName: (r) => `el puesto ${r.name}`,
        deleteTitle: (r) => `¿Borrar el puesto ${r.name}?`,
        deleteDescription: "Solo se puede borrar un puesto sin turnos por delante.",
        created: "Puesto creado.",
        deleted: (r) => `Puesto "${r.name}" borrado.`,
        replayed: "Ese puesto ya se había creado. Revisá en la lista cómo quedó.",
      }}
      renderForm={({ row, onSubmit, onCancel }) => (
        <ResourceForm resource={row} onSubmit={onSubmit} onCancel={onCancel} />
      )}
    />
  );
}

function PaymentMethodsTab() {
  return (
    <CatalogTab
      catalog="payment-methods"
      service={{
        list: listPaymentMethods,
        create: createPaymentMethod,
        update: updatePaymentMethod,
        remove: deletePaymentMethod,
      }}
      columns={paymentColumns}
      copy={{
        tableLabel: "Medios de pago",
        newLabel: "Nuevo medio de pago",
        newTitle: "Nuevo medio de pago",
        editTitle: (m) => `Editar ${m.label}`,
        dialogDescription: "Cómo te pagan los clientes y con qué pagás los gastos.",
        emptyMessage: "No hay medios de pago. Cargá al menos uno para poder cobrar.",
        exportFilename: "medios-de-pago",
        rowName: (m) => `el medio de pago ${m.label}`,
        deleteTitle: (m) => `¿Borrar el medio de pago ${m.label}?`,
        deleteDescription: "Los cobros ya registrados con este medio no se tocan.",
        created: "Medio de pago creado.",
        deleted: (m) => `Medio de pago "${m.label}" borrado.`,
        replayed: "Ese medio de pago ya se había creado. Revisá en la lista cómo quedó.",
      }}
      renderForm={({ row, onSubmit, onCancel }) => (
        <PaymentMethodForm paymentMethod={row} onSubmit={onSubmit} onCancel={onCancel} />
      )}
    />
  );
}

function VehicleSizesTab() {
  return (
    <CatalogTab
      catalog="vehicle-sizes"
      service={{
        list: listVehicleSizes,
        create: createVehicleSize,
        update: updateVehicleSize,
        remove: deleteVehicleSize,
      }}
      columns={sizeColumns}
      copy={{
        tableLabel: "Tamaños de vehículo",
        newLabel: "Nuevo tamaño",
        newTitle: "Nuevo tamaño",
        editTitle: (s) => `Editar ${s.label}`,
        dialogDescription: "Cada servicio tiene un precio para cada tamaño.",
        emptyMessage: "No hay tamaños. Cargá al menos uno para ponerles precio a los servicios.",
        exportFilename: "tamanos",
        rowName: (s) => `el tamaño ${s.label}`,
        deleteTitle: (s) => `¿Borrar el tamaño ${s.label}?`,
        deleteDescription: "Los turnos y trabajos ya cargados con este tamaño no se tocan.",
        created: "Tamaño creado.",
        deleted: (s) => `Tamaño "${s.label}" borrado.`,
        replayed: "Ese tamaño ya se había creado. Revisá en la lista cómo quedó.",
      }}
      renderForm={({ row, onSubmit, onCancel }) => (
        <VehicleSizeForm vehicleSize={row} onSubmit={onSubmit} onCancel={onCancel} />
      )}
    />
  );
}

/**
 * `/configuracion/operacion` (PR 4.2): horarios, puestos, medios de pago y
 * tamaños. Es una pantalla de edición: sin `CATALOGO_EDITAR` se ve un aviso y
 * no se pide nada (leer los catálogos sí está abierto en la API, para la
 * agenda y la caja).
 */
export function OperationPageClient() {
  const canEdit = usePermission("CATALOGO_EDITAR");

  if (!canEdit) {
    return (
      <section aria-labelledby="operation-title" className="rounded-xl border bg-card">
        <h1 id="operation-title" className="sr-only">
          Operación
        </h1>
        <EmptyState
          icon={<Lock className="h-6 w-6" />}
          title="No tenés acceso a esta sección"
          description="Los horarios, puestos, medios de pago y tamaños los configura el dueño o quien tenga permiso para editar el catálogo."
          action={{ label: "Volver al inicio", href: "/dashboard" }}
        />
      </section>
    );
  }

  return (
    <section aria-labelledby="operation-title" className="space-y-4">
      <div>
        <h1 id="operation-title" className="text-2xl font-semibold">
          Operación
        </h1>
        <p className="text-sm text-muted-foreground">
          Cuándo atendés, dónde se trabaja, cómo te pagan y qué tamaños de vehículo manejás.
        </p>
      </div>
      <Tabs defaultValue="horarios">
        <TabsList className="flex-wrap justify-start">
          <TabsTrigger value="horarios">Horarios</TabsTrigger>
          <TabsTrigger value="puestos">Puestos</TabsTrigger>
          <TabsTrigger value="medios">Medios de pago</TabsTrigger>
          <TabsTrigger value="tamanos">Tamaños</TabsTrigger>
        </TabsList>
        <TabsContent value="horarios">
          <HoursTab />
        </TabsContent>
        <TabsContent value="puestos">
          <ResourcesTab />
        </TabsContent>
        <TabsContent value="medios">
          <PaymentMethodsTab />
        </TabsContent>
        <TabsContent value="tamanos">
          <VehicleSizesTab />
        </TabsContent>
      </Tabs>
    </section>
  );
}
