"use client";

import Link from "next/link";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { RegisterForm } from "@/features/auth/RegisterForm";
import { useAuthActions } from "@/features/auth/useAuthActions";

export function RegisterPageClient() {
  const { register } = useAuthActions();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">
          <h1>Crear cuenta</h1>
        </CardTitle>
        <CardDescription>Registrá tu negocio para empezar.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <RegisterForm onSubmit={register} />
        <p className="text-center text-sm text-muted-foreground">
          ¿Ya tenés cuenta?{" "}
          <Link
            href="/login"
            className="inline-flex min-h-touch items-center font-medium text-primary underline-offset-4 hover:underline"
          >
            Iniciá sesión
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
