"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Lock } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { configKeys, useSettings } from "@/features/configuracion/queries";
import { BookingCard } from "@/features/turnos/BookingCard";
import { DepositDialog, ResolveDialog } from "@/features/turnos/MoneyDialogs";
import {
  DEPOSIT_STATUS_LABELS,
  PAGE,
  runAction,
  useBookings,
  useDepositCases,
  useInboxCounts,
  useRefreshInbox,
} from "@/features/turnos/queries";
import { ReasonDialog } from "@/features/turnos/ReasonDialog";
import { RescheduleDialog } from "@/features/turnos/RescheduleDialog";
import { formatDay } from "@/features/turnos/time";
import { useCan, useIsOwner, usePermissionsLoaded } from "@/hooks/usePermission";
import { formatRange, localDate } from "@/lib/agenda";
import { formatARS } from "@/lib/money";
import {
  approveBooking,
  cancelBooking,
  confirmDeposit,
  markIncident,
  markNoShow,
  rejectBooking,
  rescheduleBooking,
  resolveDepositCase,
} from "@/services/bookings.service";
import { paymentMethodsApi, resourcesApi } from "@/services/config.service";
import { toast } from "@/stores/toastStore";
import type { DepositCase, InboxBooking, PaymentMethod, Resource } from "@/types/api";

const DEFAULT_TZ = "America/Argentina/Cordoba";

type Action =
  | {
      kind: "cancel" | "reject" | "incident" | "noShow" | "approve" | "reschedule" | "deposit";
      booking: InboxBooking;
    }
  | { kind: "resolve"; item: DepositCase };

/** Estados desde donde se reprograma (D5-10). */
const RESCHEDULABLE = new Set(["PENDIENTE_SEÑA", "CONFIRMADO", "PENDIENTE_REVISION"]);
/** Desde donde el negocio cancela (R-T-036 + 5.1-4). */
const CANCELLABLE = new Set(["PENDIENTE_SEÑA", "CONFIRMADO", "PENDIENTE_REVISION"]);

/**
 * `/turnos`: la bandeja del dueño (FASE-5-CONTRATO §4.1, PR 5.3). Cuatro pestañas: Próximos,
 * Pendientes de seña, Lista de espera y Señas a resolver. Verla pide `AGENDA_VER`; los botones
 * aparecen según `TURNOS_GESTIONAR` (gestionar turnos) y `COBROS_REGISTRAR` (señas). Ocultar no
 * es autorizar: el backend responde 403 igual.
 */
export function TurnosPageClient() {
  const isOwner = useIsOwner();
  const can = useCan();
  const loaded = usePermissionsLoaded();
  const canView = can("AGENDA_VER");
  const canManage = can("TURNOS_GESTIONAR");
  const canCharge = can("COBROS_REGISTRAR");

  const settings = useSettings(canView);
  const timeZone = settings.data?.timezone ?? DEFAULT_TZ;
  const resources = useQuery({
    queryKey: configKeys.list("resources", PAGE),
    queryFn: () => resourcesApi.list(PAGE),
    enabled: canView,
  });
  const methods = useQuery({
    queryKey: configKeys.list("payment-methods", PAGE),
    queryFn: () => paymentMethodsApi.list(PAGE),
    enabled: canCharge,
  });
  const counts = useInboxCounts(canView);
  const refresh = useRefreshInbox();
  const [action, setAction] = useState<Action | null>(null);
  const [keys] = useState(() => new Map<string, string>());

  if (!isOwner && !loaded) {
    return <p className="text-sm text-muted-foreground">Cargando…</p>;
  }
  if (!canView) {
    return (
      <section aria-labelledby="turnos-title" className="rounded-xl border bg-card">
        <h1 id="turnos-title" className="sr-only">
          Turnos
        </h1>
        <EmptyState
          icon={<Lock className="h-6 w-6" />}
          title="No tenés acceso a los turnos"
          description="La bandeja de turnos la ven el dueño y quienes tengan permiso para ver la agenda. Si la necesitás, pedíselo."
          action={{ label: "Volver al inicio", href: "/dashboard" }}
        />
      </section>
    );
  }

  const resourceList: Resource[] = resources.data?.items ?? [];
  const methodList: PaymentMethod[] = methods.data?.items ?? [];
  const resourceName = (id: string) => resourceList.find((r) => r.id === id)?.name ?? "Puesto";
  const today = localDate(new Date(), timeZone);
  const close = () => setAction(null);
  /** Una clave por acción y turno: un doble toque o un reintento no se aplican dos veces. */
  const keyFor = (what: string, id: string) => {
    const k = `${what}:${id}`;
    if (!keys.has(k)) keys.set(k, crypto.randomUUID());
    return keys.get(k)!;
  };
  async function done(fn: () => Promise<unknown>, message: string) {
    await runAction(fn);
    await refresh();
    toast.success(message);
  }

  const shared = { timeZone, resourceName, canManage, canCharge, today, onAction: setAction };
  const c = counts.data;

  return (
    <section aria-labelledby="turnos-title" className="space-y-4">
      <div>
        <h1 id="turnos-title" className="text-2xl font-semibold">
          Turnos
        </h1>
        <p className="text-sm text-muted-foreground">
          Lo que entra por el turnero y lo que espera una decisión tuya: señas, lista de espera y
          devoluciones.
        </p>
      </div>
      <Tabs defaultValue="proximos">
        <TabsList className="h-auto flex-wrap justify-start">
          <TabsTrigger value="proximos">Próximos</TabsTrigger>
          <TabsTrigger value="senas">Pendientes de seña{badge(c?.pending_deposit)}</TabsTrigger>
          <TabsTrigger value="espera">Lista de espera{badge(c?.in_review)}</TabsTrigger>
          <TabsTrigger value="resolver">Señas a resolver{badge(c?.deposit_cases)}</TabsTrigger>
        </TabsList>
        <TabsContent value="proximos">
          <UpcomingTab {...shared} />
        </TabsContent>
        <TabsContent value="senas">
          <DepositsTab {...shared} instructions={settings.data?.deposit_instructions ?? null} />
        </TabsContent>
        <TabsContent value="espera">
          <WaitlistTab {...shared} />
        </TabsContent>
        <TabsContent value="resolver">
          <CasesTab {...shared} />
        </TabsContent>
      </Tabs>

      {action?.kind === "reschedule" && (
        <RescheduleDialog
          booking={action.booking}
          timeZone={timeZone}
          resources={resourceList}
          onClose={close}
          onSubmit={async (payload) => {
            let outside = false;
            await runAction(async () => {
              const r = await rescheduleBooking(action.booking.id, payload);
              outside = r.outside_rules;
            });
            await refresh();
            if (outside) {
              toast.warning(
                "Turno movido fuera del horario del negocio o de las reglas del servicio. Avisale al cliente.",
              );
            } else {
              toast.success("Turno movido. Avisale al cliente.");
            }
          }}
        />
      )}
      {action?.kind === "deposit" && (
        <DepositDialog
          booking={action.booking}
          methods={methodList}
          onClose={close}
          onSubmit={async (payload) => {
            let late = false;
            await runAction(async () => {
              const r = await confirmDeposit(
                action.booking.id,
                payload,
                keyFor("deposit", action.booking.id),
              );
              late = r.late;
            });
            await refresh();
            toast.success(
              late ? "Seña registrada: el turno se confirmó tarde." : "Seña confirmada.",
            );
          }}
        />
      )}
      {action?.kind === "resolve" && (
        <ResolveDialog
          item={action.item}
          methods={methodList}
          onClose={close}
          onSubmit={(payload) =>
            done(
              () =>
                resolveDepositCase(
                  action.item.cancellation.id,
                  payload,
                  keyFor("resolve", action.item.cancellation.id),
                ),
              "Seña resuelta.",
            )
          }
        />
      )}
      <ReasonDialog
        open={action?.kind === "cancel"}
        onOpenChange={(open) => !open && close()}
        title="Cancelar el turno"
        description="La cancelación la decide el negocio: no cuenta como incidencia del cliente. Si pagó seña, queda en Señas a resolver."
        label="Motivo"
        confirmLabel="Cancelar turno"
        variant="destructive"
        onSubmit={(reason) =>
          action?.kind === "cancel"
            ? done(
                () =>
                  cancelBooking(action.booking.id, { reason }, keyFor("cancel", action.booking.id)),
                "Turno cancelado. Avisale al cliente.",
              )
            : Promise.resolve()
        }
      />
      <ReasonDialog
        open={action?.kind === "reject"}
        onOpenChange={(open) => !open && close()}
        title="Rechazar el pedido"
        description="El cliente queda sin turno. Contale el motivo por WhatsApp."
        label="Motivo"
        confirmLabel="Rechazar"
        variant="destructive"
        onSubmit={(reason) =>
          action?.kind === "reject"
            ? done(
                () =>
                  rejectBooking(action.booking.id, { reason }, keyFor("reject", action.booking.id)),
                "Pedido rechazado.",
              )
            : Promise.resolve()
        }
      />
      <ReasonDialog
        open={action?.kind === "incident"}
        onOpenChange={(open) => !open && close()}
        title="Marcar una incidencia del cliente"
        description="Con 2 o más incidencias en 90 días, sus próximos pedidos del turnero van a la lista de espera."
        label="Qué pasó"
        confirmLabel="Marcar incidencia"
        onSubmit={(reason) =>
          action?.kind === "incident"
            ? done(
                () =>
                  markIncident(
                    action.booking.id,
                    { reason },
                    keyFor("incident", action.booking.id),
                  ),
                "Incidencia marcada.",
              )
            : Promise.resolve()
        }
      />
      <ConfirmDialog
        open={action?.kind === "noShow"}
        onOpenChange={(open) => !open && close()}
        title="¿El cliente no vino?"
        description="El turno queda como no asistió y cuenta como incidencia del cliente."
        confirmLabel="No asistió"
        variant="destructive"
        onConfirm={() =>
          action?.kind === "noShow"
            ? done(
                () => markNoShow(action.booking.id, keyFor("noShow", action.booking.id)),
                "Marcado como no asistió.",
              )
            : undefined
        }
      />
      <ConfirmDialog
        open={action?.kind === "approve"}
        onOpenChange={(open) => !open && close()}
        title="¿Aprobar el pedido?"
        description={
          action?.kind === "approve"
            ? `${formatRange(action.booking.start_at, action.booking.end_at, timeZone)}. Si el negocio pide seña, el turno queda pendiente de seña con una retención nueva; si no, confirmado.`
            : ""
        }
        confirmLabel="Aprobar"
        onConfirm={() =>
          action?.kind === "approve"
            ? done(
                () => approveBooking(action.booking.id, keyFor("approve", action.booking.id)),
                "Pedido aprobado. Avisale al cliente.",
              )
            : undefined
        }
      />
    </section>
  );
}

function badge(n: number | undefined) {
  if (!n) return null;
  return (
    <span className="ml-2 rounded-full bg-primary px-2 text-xs font-semibold text-primary-foreground">
      {n}
    </span>
  );
}

interface TabProps {
  timeZone: string;
  resourceName: (id: string) => string;
  canManage: boolean;
  canCharge: boolean;
  today: string;
  onAction: (action: Action) => void;
}

function ListState({
  loading,
  error,
  empty,
  emptyMessage,
  children,
}: {
  loading: boolean;
  error: boolean;
  empty: boolean;
  emptyMessage: string;
  children: React.ReactNode;
}) {
  if (loading) return <p className="text-sm text-muted-foreground">Cargando…</p>;
  if (error) {
    return (
      <p role="alert" className="text-sm font-medium text-destructive">
        No se pudo cargar la bandeja. Revisá tu conexión y recargá.
      </p>
    );
  }
  if (empty) return <p className="text-sm text-muted-foreground">{emptyMessage}</p>;
  return <>{children}</>;
}

function ManageButtons({
  booking,
  canManage,
  onAction,
  noShow,
}: {
  booking: InboxBooking;
  canManage: boolean;
  onAction: (a: Action) => void;
  noShow?: boolean;
}) {
  if (!canManage) return null;
  return (
    <>
      {RESCHEDULABLE.has(booking.status) && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => onAction({ kind: "reschedule", booking })}
        >
          Reprogramar
        </Button>
      )}
      {noShow && (
        <Button size="sm" variant="outline" onClick={() => onAction({ kind: "noShow", booking })}>
          No asistió
        </Button>
      )}
      <Button size="sm" variant="outline" onClick={() => onAction({ kind: "incident", booking })}>
        Marcar incidencia
      </Button>
      {CANCELLABLE.has(booking.status) && (
        <Button size="sm" variant="ghost" onClick={() => onAction({ kind: "cancel", booking })}>
          Cancelar
        </Button>
      )}
    </>
  );
}

function UpcomingTab({ timeZone, resourceName, canManage, today, onAction }: TabProps) {
  const query = useBookings({
    ...PAGE,
    status: ["CONFIRMADO", "PENDIENTE_SEÑA", "RECIBIDO"],
    from: today,
  });
  const items = query.data?.items ?? [];
  const days = new Map<string, InboxBooking[]>();
  for (const b of items) {
    const day = localDate(new Date(b.start_at), timeZone);
    days.set(day, [...(days.get(day) ?? []), b]);
  }
  const now = Date.now();
  return (
    <ListState
      loading={query.isLoading}
      error={query.isError}
      empty={items.length === 0}
      emptyMessage="No hay turnos próximos."
    >
      <div className="space-y-4">
        {[...days.entries()].map(([day, bookings]) => (
          <section key={day} aria-label={day} className="space-y-2">
            <h2 className="text-sm font-semibold uppercase text-muted-foreground">
              {day === today ? "Hoy" : formatDay(bookings[0]!.start_at, timeZone)}
            </h2>
            <ul className="divide-y rounded-lg border bg-card">
              {bookings.map((b) => (
                <BookingCard key={b.id} booking={b} timeZone={timeZone} resourceName={resourceName}>
                  <ManageButtons
                    booking={b}
                    canManage={canManage}
                    onAction={onAction}
                    noShow={b.status === "CONFIRMADO" && new Date(b.start_at).getTime() <= now}
                  />
                </BookingCard>
              ))}
            </ul>
          </section>
        ))}
        {query.data?.has_more && (
          <p className="text-sm text-muted-foreground">
            Se muestran los primeros {items.length} turnos.
          </p>
        )}
      </div>
    </ListState>
  );
}

function DepositsTab({
  timeZone,
  resourceName,
  canManage,
  canCharge,
  today,
  onAction,
  instructions,
}: TabProps & { instructions: string | null }) {
  const query = useBookings({ ...PAGE, status: ["PENDIENTE_SEÑA", "VENCIDO"], from: today });
  // Un vencido sin seña (un pedido de la lista de espera que nadie decidió) no espera plata.
  const items = (query.data?.items ?? []).filter(
    (b) => b.status === "PENDIENTE_SEÑA" || b.deposit_required_cents > 0,
  );
  return (
    <div className="space-y-3">
      <p className="rounded-lg border bg-muted/40 p-3 text-sm">
        {instructions
          ? `Lo que ve el cliente para pagar: ${instructions}`
          : "El cliente ve: «Coordiná el pago de la seña por WhatsApp». Podés cargar alias o CBU en Configuración → Seña y reservas."}
      </p>
      <ListState
        loading={query.isLoading}
        error={query.isError}
        empty={items.length === 0}
        emptyMessage="No hay señas pendientes."
      >
        <ul className="divide-y rounded-lg border bg-card">
          {items.map((b) => (
            <BookingCard key={b.id} booking={b} timeZone={timeZone} resourceName={resourceName}>
              {canCharge && (
                <Button size="sm" onClick={() => onAction({ kind: "deposit", booking: b })}>
                  {b.status === "VENCIDO" ? "Confirmar seña tardía" : "Confirmar seña"}
                </Button>
              )}
              {canManage && b.status === "PENDIENTE_SEÑA" && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => onAction({ kind: "cancel", booking: b })}
                >
                  Cancelar
                </Button>
              )}
            </BookingCard>
          ))}
        </ul>
      </ListState>
    </div>
  );
}

function WaitlistTab({ timeZone, resourceName, canManage, today, onAction }: TabProps) {
  const query = useBookings({ ...PAGE, status: ["PENDIENTE_REVISION"], from: today });
  const items = query.data?.items ?? [];
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Pedidos de clientes con 2 o más incidencias en 90 días: no pagaron seña ni ocupan el horario
        hasta que los apruebes.
      </p>
      <ListState
        loading={query.isLoading}
        error={query.isError}
        empty={items.length === 0}
        emptyMessage="No hay pedidos en lista de espera."
      >
        <ul className="divide-y rounded-lg border bg-card">
          {items.map((b) => (
            <BookingCard key={b.id} booking={b} timeZone={timeZone} resourceName={resourceName}>
              {canManage && (
                <>
                  <Button size="sm" onClick={() => onAction({ kind: "approve", booking: b })}>
                    Aprobar
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => onAction({ kind: "reschedule", booking: b })}
                  >
                    Reprogramar
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => onAction({ kind: "reject", booking: b })}
                  >
                    Rechazar
                  </Button>
                </>
              )}
            </BookingCard>
          ))}
        </ul>
      </ListState>
    </div>
  );
}

function CasesTab({ timeZone, resourceName, canCharge, onAction }: TabProps) {
  const query = useDepositCases();
  const items = query.data?.items ?? [];
  return (
    <ListState
      loading={query.isLoading}
      error={query.isError}
      empty={items.length === 0}
      emptyMessage="No hay señas por resolver."
    >
      <ul className="divide-y rounded-lg border bg-card">
        {items.map((item) => (
          <BookingCard
            key={item.cancellation.id}
            booking={item.booking}
            timeZone={timeZone}
            resourceName={resourceName}
          >
            <p className="w-full text-sm">
              {DEPOSIT_STATUS_LABELS[item.cancellation.deposit_status]} · seña cobrada{" "}
              {formatARS(item.cancellation.deposit_paid_cents)} · motivo: {item.cancellation.reason}
            </p>
            {canCharge && (
              <Button size="sm" onClick={() => onAction({ kind: "resolve", item })}>
                Resolver
              </Button>
            )}
          </BookingCard>
        ))}
      </ul>
    </ListState>
  );
}
