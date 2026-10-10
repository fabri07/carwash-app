"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ChangePasswordForm } from "@/features/auth/ChangePasswordForm";
import { useLogout } from "@/features/auth/useLogout";
import { changePasswordError, FormSubmitError } from "@/lib/errors";
import { HOME_PATH } from "@/lib/routes";
import { changePasswordRequest, getMeRequest } from "@/services/auth.service";
import { useAuthStore } from "@/stores/authStore";
import type { ChangePasswordInput } from "@/validation/auth";

/**
 * Cambio de clave (FASE-4-CONTRATO §2.3). Sirve para los dos casos:
 *
 * - **Obligatorio:** el dueño eligió la clave (alta o reset). Hasta cambiarla,
 *   el backend responde 403 `PASSWORD_CHANGE_REQUIRED` a todo menos `me`,
 *   `change-password`, `refresh` y `logout`, y el shell manda acá.
 * - **Voluntario:** cualquiera puede cambiar la suya.
 *
 * Al terminar, el backend rota las cookies (cierra las otras sesiones) y
 * devuelve la sesión con el flag apagado; se guarda y se va al inicio.
 */
export function ChangePasswordPageClient() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const setSession = useAuthStore((s) => s.setSession);
  const mustChange = useAuthStore((s) => s.mustChangePassword);
  const { logout, loggingOut } = useLogout();

  // Entrar directo por URL (o recargar) no pasa por el shell: el flag llega de acá.
  const { data: me } = useQuery({
    queryKey: ["auth", "me"],
    queryFn: getMeRequest,
    staleTime: 0,
    retry: false,
  });
  useEffect(() => {
    if (me) setSession(me);
  }, [me, setSession]);

  async function submit(values: ChangePasswordInput) {
    try {
      const session = await changePasswordRequest({
        current_password: values.current_password,
        new_password: values.new_password,
      });
      setSession(session);
      queryClient.setQueryData(["auth", "me"], session);
    } catch (e) {
      throw new FormSubmitError(changePasswordError(e));
    }
    router.replace(HOME_PATH);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">
          <h1>Cambiar contraseña</h1>
        </CardTitle>
        <CardDescription>
          {mustChange
            ? "Tu contraseña la eligió el dueño del negocio. Elegí una nueva para seguir; solo vos la vas a saber."
            : "Elegí una contraseña nueva. Las otras sesiones abiertas se van a cerrar."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ChangePasswordForm onSubmit={submit} />
        <Button
          type="button"
          variant="ghost"
          className="w-full"
          disabled={loggingOut}
          onClick={() => void logout()}
        >
          Cerrar sesión
        </Button>
      </CardContent>
    </Card>
  );
}
