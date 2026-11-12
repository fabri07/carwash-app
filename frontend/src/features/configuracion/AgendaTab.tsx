"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarOff, CalendarPlus, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { AgendaRangeForm } from "@/features/configuracion/AgendaRangeForm";
import { configKeys, useSettings } from "@/features/configuracion/queries";
import { useCan } from "@/hooks/usePermission";
import { formatRange, localDate } from "@/lib/agenda";
import { apiErrorCode, configError, FormSubmitError } from "@/lib/errors";
import {
  createBlock,
  createOpening,
  listBlocks,
  listOpenings,
  removeBlock,
  removeOpening,
} from "@/services/agenda.service";
import { resourcesApi } from "@/services/config.service";
import { toast } from "@/stores/toastStore";
import type { AgendaBlock, AgendaOpening, AgendaOverlap, Resource } from "@/types/api";
import { toAgendaPayload, type AgendaRangeInput } from "@/validation/agenda";

/** Tope del backend: bloqueos y aperturas vigentes son unos pocos, caben en una página. */
const ALL = { limit: 200, offset: 0 };
const DEFAULT_TZ = "America/Argentina/Cordoba";

export const agendaKeys = {
  blocks: ["agenda", "blocks"] as const,
  openings: ["agenda", "openings"] as const,
};

type Kind = "block" | "opening";
type Dialogs =
  | { kind: "create"; what: Kind; idempotencyKey: string }
  | { kind: "delete"; what: Kind; item: AgendaBlock | AgendaOpening }
  | null;

const STATUS_LABEL: Record<string, string> = {
  PENDIENTE_SEÑA: "pendiente de seña",
  PENDIENTE_COTIZACION: "a cotizar",
  PENDIENTE_REVISION: "en lista de espera",
  CONFIRMADO: "confirmado",
  RECIBIDO: "recibido",
};

/**
 * Pestaña "Bloqueos y aperturas" (FASE-5-CONTRATO §3.3, PR 5.1). Un **bloqueo** cierra
 * la agenda (feriado, mantenimiento) y gana sobre todo; una **apertura especial** suma
 * horario fuera del habitual (un domingo). Verla pide `AGENDA_VER`; crear y quitar,
 * `TURNOS_GESTIONAR`. Un bloqueo no cancela los turnos que pisa: se muestran para avisarles.
 */
export function AgendaTab() {
  const queryClient = useQueryClient();
  const can = useCan();
  const canManage = can("TURNOS_GESTIONAR");
  const settings = useSettings();
  const timeZone = settings.data?.timezone ?? DEFAULT_TZ;
  const blocks = useQuery({ queryKey: agendaKeys.blocks, queryFn: () => listBlocks(ALL) });
  const openings = useQuery({ queryKey: agendaKeys.openings, queryFn: () => listOpenings(ALL) });
  const resources = useQuery({
    queryKey: configKeys.list("resources", ALL),
    queryFn: () => resourcesApi.list(ALL),
  });
  const [dialog, setDialog] = useState<Dialogs>(null);
  const [overlaps, setOverlaps] = useState<AgendaOverlap[]>([]);

  const resourceList: Resource[] = resources.data?.items ?? [];
  const resourceName = (id: string | null) =>
    id === null ? "Todos los puestos" : (resourceList.find((r) => r.id === id)?.name ?? "Puesto");
  const close = () => setDialog(null);

  async function create(what: Kind, values: AgendaRangeInput, idempotencyKey: string) {
    const payload = toAgendaPayload(values);
    try {
      if (what === "block") {
        const created = await createBlock(payload, idempotencyKey);
        setOverlaps(created.overlapping_bookings);
      } else {
        await createOpening(payload, idempotencyKey);
      }
    } catch (e) {
      if (apiErrorCode(e) !== "DUPLICATE_IDEMPOTENT") throw new FormSubmitError(configError(e));
      // El primer intento se guardó pero la respuesta se perdió: no tenemos la lista de turnos
      // que el bloqueo pisa (R-T-052), así que no decimos "creado" como si nada.
      await queryClient.invalidateQueries({ queryKey: ["agenda"] });
      close();
      toast.info(
        what === "block"
          ? "Ese bloqueo ya se había creado. Revisá si quedaron turnos dentro para avisarles."
          : "Esa apertura ya se había creado. Revisá en la lista cómo quedó.",
      );
      return;
    }
    await queryClient.invalidateQueries({ queryKey: ["agenda"] });
    close();
    toast.success(what === "block" ? "Bloqueo creado." : "Apertura creada.");
  }

  async function remove(what: Kind, id: string) {
    try {
      await (what === "block" ? removeBlock(id) : removeOpening(id));
    } catch (e) {
      throw new Error(configError(e).message);
    }
    await queryClient.invalidateQueries({ queryKey: ["agenda"] });
    toast.success(what === "block" ? "Bloqueo quitado." : "Apertura quitada.");
  }

  function openCreate(what: Kind) {
    setDialog({ kind: "create", what, idempotencyKey: crypto.randomUUID() });
  }

  return (
    <div className="space-y-6">
      {overlaps.length > 0 && (
        <div
          role="alert"
          className="space-y-2 rounded-lg border border-destructive/40 bg-destructive/5 p-4"
        >
          <p className="font-medium">Estos turnos quedan dentro del bloqueo: avisales.</p>
          <p className="text-sm">
            El bloqueo no los cancela. Coordiná con cada cliente un cambio de horario o cancelalo
            desde la bandeja de turnos.
          </p>
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {overlaps.map((b) => (
              <li key={b.id}>
                {formatRange(b.start_at, b.end_at, timeZone)} — {b.customer_name || "Cliente"} (
                {b.code}, {resourceName(b.resource_id)}, {STATUS_LABEL[b.status] ?? b.status})
              </li>
            ))}
          </ul>
          <Button type="button" variant="outline" size="sm" onClick={() => setOverlaps([])}>
            Entendido
          </Button>
        </div>
      )}

      <AgendaSection
        title="Bloqueos"
        description="Días o franjas en que no se toman turnos: feriados, vacaciones, mantenimiento de un puesto."
        emptyMessage="No hay bloqueos próximos."
        icon={<CalendarOff className="h-4 w-4" aria-hidden="true" />}
        addLabel="Nuevo bloqueo"
        canManage={canManage}
        loading={blocks.isLoading}
        error={blocks.isError ? configError(blocks.error).message : null}
        items={blocks.data?.items ?? []}
        describe={(b) => formatRange(b.starts_at, b.ends_at, timeZone)}
        resourceName={resourceName}
        onAdd={() => openCreate("block")}
        onRemove={(item) => setDialog({ kind: "delete", what: "block", item })}
        removeLabel="Quitar bloqueo"
      />

      <AgendaSection
        title="Aperturas especiales"
        description="Horario extra fuera del habitual, por ejemplo un domingo. Un bloqueo gana sobre una apertura."
        emptyMessage="No hay aperturas especiales próximas."
        icon={<CalendarPlus className="h-4 w-4" aria-hidden="true" />}
        addLabel="Nueva apertura"
        canManage={canManage}
        loading={openings.isLoading}
        error={openings.isError ? configError(openings.error).message : null}
        items={openings.data?.items ?? []}
        describe={(o) => formatRange(o.starts_at, o.ends_at, timeZone)}
        resourceName={resourceName}
        onAdd={() => openCreate("opening")}
        onRemove={(item) => setDialog({ kind: "delete", what: "opening", item })}
        removeLabel="Quitar apertura"
      />

      <Dialog open={dialog?.kind === "create"} onOpenChange={(open) => !open && close()}>
        <DialogContent>
          {dialog?.kind === "create" && (
            <>
              <DialogHeader>
                <DialogTitle>
                  {dialog.what === "block" ? "Nuevo bloqueo" : "Nueva apertura especial"}
                </DialogTitle>
                <DialogDescription>
                  Día y horas en el horario del negocio (
                  {timeZone.split("/").pop()?.replace(/_/g, " ")}).
                </DialogDescription>
              </DialogHeader>
              <AgendaRangeForm
                kind={dialog.what}
                resources={resourceList}
                defaultDate={localDate(new Date(), timeZone)}
                onSubmit={(v) => create(dialog.what, v, dialog.idempotencyKey)}
                onCancel={close}
              />
            </>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={dialog?.kind === "delete"}
        onOpenChange={(open) => !open && close()}
        title={
          dialog?.kind === "delete"
            ? dialog.what === "block"
              ? "¿Quitar este bloqueo?"
              : "¿Quitar esta apertura?"
            : ""
        }
        description={
          dialog?.kind === "delete"
            ? `${formatRange(dialog.item.starts_at, dialog.item.ends_at, timeZone)} — ${resourceName(dialog.item.resource_id)}`
            : ""
        }
        confirmLabel="Quitar"
        variant="destructive"
        onConfirm={() =>
          dialog?.kind === "delete" ? remove(dialog.what, dialog.item.id) : undefined
        }
      />
    </div>
  );
}

interface AgendaSectionProps<T extends AgendaBlock | AgendaOpening> {
  title: string;
  description: string;
  emptyMessage: string;
  icon: React.ReactNode;
  addLabel: string;
  removeLabel: string;
  canManage: boolean;
  loading: boolean;
  error: string | null;
  items: T[];
  describe: (item: T) => string;
  resourceName: (id: string | null) => string;
  onAdd: () => void;
  onRemove: (item: T) => void;
}

function AgendaSection<T extends AgendaBlock | AgendaOpening>({
  title,
  description,
  emptyMessage,
  icon,
  addLabel,
  removeLabel,
  canManage,
  loading,
  error,
  items,
  describe,
  resourceName,
  onAdd,
  onRemove,
}: AgendaSectionProps<T>) {
  const headingId = `agenda-${title.toLowerCase().replace(/\s+/g, "-")}`;
  return (
    <section aria-labelledby={headingId} className="space-y-3 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 id={headingId} className="flex items-center gap-2 text-lg font-semibold">
            {icon}
            {title}
          </h2>
          <p className="text-sm text-muted-foreground">{description}</p>
        </div>
        {canManage && (
          <Button type="button" onClick={onAdd}>
            <Plus aria-hidden="true" />
            {addLabel}
          </Button>
        )}
      </div>
      {loading ? (
        <p className="text-sm text-muted-foreground">Cargando…</p>
      ) : error ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error}
        </p>
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{emptyMessage}</p>
      ) : (
        <ul className="divide-y rounded-lg border">
          {items.map((item) => (
            <li key={item.id} className="flex items-center justify-between gap-3 p-3">
              <div className="min-w-0">
                <p className="font-medium">{describe(item)}</p>
                <p className="text-sm text-muted-foreground">
                  {resourceName(item.resource_id)} · {item.reason}
                </p>
              </div>
              {canManage && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`${removeLabel}: ${describe(item)}`}
                  onClick={() => onRemove(item)}
                >
                  <Trash2 />
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
