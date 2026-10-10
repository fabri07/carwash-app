"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";

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
import { ProfileForm } from "@/features/equipo/ProfileForm";
import { teamKeys, useProfilesPage } from "@/features/equipo/queries";
import { apiErrorCode, FormSubmitError, teamError } from "@/lib/errors";
import { PERMISSION_LABELS, sortPermissions } from "@/lib/permissions";
import { createProfile, deleteProfile, updateProfile } from "@/services/team.service";
import { toast } from "@/stores/toastStore";
import type { PermissionProfile } from "@/types/api";
import type { ProfileInput } from "@/validation/team";

type DialogState =
  | { kind: "create"; idempotencyKey: string }
  | { kind: "edit"; profile: PermissionProfile }
  | { kind: "delete"; profile: PermissionProfile }
  | null;

const PAGE_SIZE = 25;

function permissionLabels(p: PermissionProfile): string[] {
  return sortPermissions(p.permissions).map((code) => PERMISSION_LABELS[code]);
}

/** Pestaña Perfiles de `/configuracion/equipo`. */
export function ProfilesTab() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState({ limit: PAGE_SIZE, offset: 0 });
  const [dialog, setDialog] = useState<DialogState>(null);
  const profilesQuery = useProfilesPage(page);

  const close = () => setDialog(null);
  // Todo el equipo: la tabla de empleados muestra el nombre del perfil.
  const refresh = () => queryClient.invalidateQueries({ queryKey: teamKeys.all });

  const columns: SmartColumn<PermissionProfile>[] = [
    { key: "name", header: "Perfil", hideable: false, className: "font-medium" },
    {
      key: "permissions",
      header: "Permisos",
      cell: (p) =>
        p.permissions.length === 0 ? (
          <span className="text-muted-foreground">Sin permisos</span>
        ) : (
          <div className="flex flex-wrap gap-1">
            {permissionLabels(p).map((label) => (
              <Badge key={label} variant="outline">
                {label}
              </Badge>
            ))}
          </div>
        ),
      csvValue: (p) => permissionLabels(p).join(", "),
    },
  ];

  async function save(values: ProfileInput, current?: PermissionProfile, idempotencyKey?: string) {
    const payload = { name: values.name, permissions: sortPermissions(values.permissions) };
    let replay = false;
    try {
      if (current) await updateProfile(current.id, payload);
      else await createProfile(payload, idempotencyKey);
    } catch (e) {
      if (current || apiErrorCode(e) !== "DUPLICATE_IDEMPOTENT") {
        throw new FormSubmitError(teamError(e));
      }
      replay = true;
    }
    await refresh();
    close();
    if (replay) toast.info("Ese perfil ya se había creado. Revisá en la lista cómo quedó.");
    else toast.success(current ? "Perfil actualizado." : "Perfil creado.");
  }

  async function remove(profile: PermissionProfile) {
    try {
      await deleteProfile(profile.id);
    } catch (e) {
      // 409 PROFILE_IN_USE: el mensaje queda en el diálogo.
      throw new Error(teamError(e).message);
    }
    await refresh();
    toast.success(`Perfil "${profile.name}" borrado.`);
  }

  return (
    <>
      <SmartTable
        label="Perfiles de permisos"
        columns={columns}
        page={profilesQuery.data}
        isLoading={profilesQuery.isLoading}
        error={profilesQuery.isError ? teamError(profilesQuery.error).message : null}
        getRowId={(p) => p.id}
        onPageChange={(offset) => setPage((p) => ({ ...p, offset }))}
        emptyMessage="No hay perfiles. Creá uno para poder dar de alta empleados."
        exportFilename="perfiles"
        toolbarActions={
          <Button
            type="button"
            onClick={() => setDialog({ kind: "create", idempotencyKey: crypto.randomUUID() })}
          >
            <Plus aria-hidden="true" />
            Nuevo perfil
          </Button>
        }
        renderActions={(p) => (
          <>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Editar el perfil ${p.name}`}
              onClick={() => setDialog({ kind: "edit", profile: p })}
            >
              <Pencil />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Borrar el perfil ${p.name}`}
              onClick={() => setDialog({ kind: "delete", profile: p })}
            >
              <Trash2 />
            </Button>
          </>
        )}
      />

      <Dialog
        open={dialog?.kind === "create" || dialog?.kind === "edit"}
        onOpenChange={(open) => !open && close()}
      >
        <DialogContent>
          {(dialog?.kind === "create" || dialog?.kind === "edit") && (
            <>
              <DialogHeader>
                <DialogTitle>
                  {dialog.kind === "edit" ? `Editar ${dialog.profile.name}` : "Nuevo perfil"}
                </DialogTitle>
                <DialogDescription>
                  Marcá lo que puede hacer quien tenga este perfil. Los cambios rigen desde su
                  próxima acción.
                </DialogDescription>
              </DialogHeader>
              <ProfileForm
                profile={dialog.kind === "edit" ? dialog.profile : undefined}
                onSubmit={(v) =>
                  dialog.kind === "edit"
                    ? save(v, dialog.profile)
                    : save(v, undefined, dialog.idempotencyKey)
                }
                onCancel={close}
              />
            </>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={dialog?.kind === "delete"}
        onOpenChange={(open) => !open && close()}
        title={dialog?.kind === "delete" ? `¿Borrar el perfil ${dialog.profile.name}?` : ""}
        description="Solo se puede borrar un perfil que ningún empleado tenga asignado."
        confirmLabel="Borrar"
        variant="destructive"
        onConfirm={() => (dialog?.kind === "delete" ? remove(dialog.profile) : undefined)}
      />
    </>
  );
}
