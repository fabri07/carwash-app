import { Suspense } from "react";
import type { Metadata } from "next";

import { LoginPageClient } from "./LoginPageClient";

export const metadata: Metadata = {
  title: "Iniciar sesión | carwash.app",
};

export default function LoginPage() {
  return (
    // `useSearchParams` (para `?next=`) exige un límite de Suspense en build.
    <Suspense>
      <LoginPageClient />
    </Suspense>
  );
}
