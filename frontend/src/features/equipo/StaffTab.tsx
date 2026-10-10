"use client";

import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, Pencil, Plus, UserX } from "lucide-react";

import { SmartTable, type SmartColumn } from "@/components/data/SmartTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ResetPasswordForm } from "@/features/equipo/ResetPasswordForm";
import { StaffCreateForm } from "@/features/equipo/StaffCreateForm";
import { StaffEditForm } from "@/features/equipo/StaffEditForm";
import { teamKeys, useProfileOptions, useStaffPage } from "@/features/equipo/queries";
import { apiErrorCode, FormSubmitError, teamError } from "@/lib/errors";
import { createStaff, deleteStaff, resetStaffPassword, updateStaff } from "@/services/team.service";
import { toast } from "@/stores/toastStore";
import type { StaffMember } from "@/types/api";
import type { ResetPasswordInput, StaffCreateInput, StaffEditInput } from "@/validation/team";

type DialogState =
  | { kind: "create"; idempotencyKey: string }
  | { kind: "edit"; staff: StaffMember }
  | { kind: "reset"; staff: StaffMember }
  | { kind: "delete"; staff: StaffMember }
  | null;

const PAGE_SIZE = 25;

function staffName(s: StaffMember): string {
  return s.username ?? s.email ?? "empleado";
}

/** Pestaña Empleados de `/configuracion/equipo`. */
export function StaffTab() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState({ limit: PAGE_SIZE, offset: 0 });
  const [dialog, setDialog] = useState<DialogState>(null);
  const staffQuery = useStaffPage(page);
  const profilesQuery = useProfileOptions();
  const profiles = useMemo(() => profilesQuery.data?.items ?? [], [profilesQuery.data]);
  const profileName = useMemo(() => new Map(profiles.map((p) => [p.id, p.name])), [profiles]);

  const close = () => setDialog(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: teamKeys.staff });

  const columns: SmartColumn<StaffMember>[] = [
    { key: "username", header: "Usuario", hideable: false },
    { key: "email", header: "Email" },
    {
      key: "permission_profile_id",
      header: "Perfil",
      cell: (s) => (s.permission_profile_id && profileName.get(s.permission_profile_id)) || "—",
      csvValue: (s) => (s.permission_profile_id && profileName.get(s.permission_profile_id)) || "",
    },
    {
      key: "must_change_password",
      header: "Debe cambiar clave",
      cell: (s) =>
        s.must_change_password ? (
          <Badge variant="secondary">Sí</Badge>
        ) : (
          <span className="text-muted-foreground">No</span>
        ),
      csvValue: (s) => (s.must_change_password ? "Sí" : "No"),
    },
  ];

  async function create(values: StaffCreateInput, idempotencyKey: string) {
    let replay = false;
    try {
      await createStaff(
        {
          username: values.username,
          password: values.password,
          permission_profile_id: values.permission_profile_id,
          email: values.email || null,
        },
        idempotencyKey,
      );
    } catch (e) {
      // Replay de un alta que ya se hizo (doble toque, reintento tras timeout).
      if (apiErrorCode(e) !== "DUPLICATE_IDEMPOTENT") throw new FormSubmitError(teamError(e));
      replay = true;
    }
    await refresh();
    close();
    if (replay) {
      // Lo guardado es el PRIMER envío: si después se corrigieron datos en el diálogo, no
      // son estos. No confirmamos un usuario o clave que quizá no quedaron.
      toast.info("Ese alta ya se había guardado. Revisá en la lista el usuario que quedó.");
    } else {
      toast.success(`Listo. ${values.username} ya puede entrar con su usuario y la clave inicial.`);
    }
  }

  async function edit(staff: StaffMember, values: StaffEditInput) {
    try {
      await updateStaff(staff.id, {
        username: values.username,
        permission_profile_id: values.permission_profile_id,
        // Vacío borra el email (`null` explícito, contrato de `StaffUpdate`).
        email: values.email || null,
      });
    } catch (e) {
      throw new FormSubmitError(teamError(e));
    }
    await refresh();
    close();
    toast.success("Cambios guardados.");
  }

  async function reset(staff: StaffMember, values: ResetPasswordInput) {
    try {
      await resetStaffPassword(staff.id, { password: values.password });
    } catch (e) {
      throw new FormSubmitError(teamError(e));
    }
    await refresh();
    close();
    toast.success(
      `Clave nueva guardada. ${staffName(staff)} la va a tener que cambiar al entrar; sus sesiones abiertas se cerraron.`,
    );
  }

  async function deactivate(staff: StaffMember) {
    try {
      await deleteStaff(staff.id);
    } catch (e) {
      throw new Error(teamError(e).message);
    }
    await refresh();
    toast.success(`${staffName(staff)} ya no puede entrar a la app.`);
  }

  return (
    <>
      <SmartTable
        label="Empleados"
        columns={columns}
        page={staffQuery.data}
        isLoading={staffQuery.isLoading}
        error={staffQuery.isError ? teamError(staffQuery.error).message : null}
        getRowId={(s) => s.id}
        onPageChange={(offset) => setPage((p) => ({ ...p, offset }))}
        onLimitChange={(limit) => setPage({ limit, offset: 0 })}
        emptyMessage="Todavía no hay empleados. Creá el primero para que entre con su usuario."
        exportFilename="empleados"
        toolbarActions={
          <Button
            type="button"
            onClick={() => setDialog({ kind: "create", idempotencyKey: crypto.randomUUID() })}
          >
            <Plus aria-hidden="true" />
            Nuevo empleado
          </Button>
        }
        renderActions={(s) => (
          <>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Editar ${staffName(s)}`}
              onClick={() => setDialog({ kind: "edit", staff: s })}
            >
              <Pencil />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Resetear la clave de ${staffName(s)}`}
              onClick={() => setDialog({ kind: "reset", staff: s })}
            >
              <KeyRound />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Desactivar a ${staffName(s)}`}
              onClick={() => setDialog({ kind: "delete", staff: s })}
            >
              <UserX />
            </Button>
          </>
        )}
      />

      <Dialog
        open={dialog?.kind === "create" || dialog?.kind === "edit" || dialog?.kind === "reset"}
        onOpenChange={(open) => !open && close()}
      >
        <DialogContent>
          {dialog?.kind === "create" && (
            <>
              <DialogHeader>
                <DialogTitle>Nuevo empleado</DialogTitle>
                <DialogDescription>
                  Elegí su usuario y una clave inicial. Pasásela en persona: la va a cambiar al
                  entrar.
                </DialogDescription>
              </DialogHeader>
              <StaffCreateForm
                profiles={profiles}
                onSubmit={(v) => create(v, dialog.idempotencyKey)}
                onCancel={close}
              />
            </>
          )}
          {dialog?.kind === "edit" && (
            <>
              <DialogHeader>
                <DialogTitle>Editar a {staffName(dialog.staff)}</DialogTitle>
                <DialogDescription>Usuario, perfil y email.</DialogDescription>
              </DialogHeader>
              <StaffEditForm
                staff={dialog.staff}
                profiles={profiles}
                onSubmit={(v) => edit(dialog.staff, v)}
                onCancel={close}
              />
            </>
          )}
          {dialog?.kind === "reset" && (
            <>
              <DialogHeader>
                <DialogTitle>Resetear la clave de {staffName(dialog.staff)}</DialogTitle>
                <DialogDescription>
                  Se cierran sus sesiones abiertas y va a tener que elegir una clave propia al
                  entrar.
                </DialogDescription>
              </DialogHeader>
              <ResetPasswordForm onSubmit={(v) => reset(dialog.staff, v)} onCancel={close} />
            </>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={dialog?.kind === "delete"}
        onOpenChange={(open) => !open && close()}
        title={dialog?.kind === "delete" ? `¿Desactivar a ${staffName(dialog.staff)}?` : ""}
        description="No va a poder entrar más a la app y se cierran sus sesiones abiertas. Lo que ya cargó queda registrado."
        confirmLabel="Desactivar"
        variant="destructive"
        onConfirm={() => (dialog?.kind === "delete" ? deactivate(dialog.staff) : undefined)}
      />
    </>
  );
}
