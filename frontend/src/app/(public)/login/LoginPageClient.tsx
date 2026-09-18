"use client";

import { useEffect } from "react";
import Link from "next/link";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { LoginForm } from "@/features/auth/LoginForm";
import { useAuthActions } from "@/features/auth/useAuthActions";

export function LoginPageClient() {
  const { login, trySilentRefresh } = useAuthActions();

  useEffect(() => {
    void trySilentRefresh();
    // Solo al montar: un intento por visita a /login.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">
          <h1>Iniciar sesión</h1>
        </CardTitle>
        <CardDescription>Entrá con el email de tu cuenta.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <LoginForm onSubmit={login} />
        <p className="text-center text-sm text-muted-foreground">
          ¿No tenés cuenta?{" "}
          <Link
            href="/register"
            className="inline-flex min-h-touch items-center font-medium text-primary underline-offset-4 hover:underline"
          >
            Registrate
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
