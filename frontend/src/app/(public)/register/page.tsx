import { Suspense } from "react";
import type { Metadata } from "next";

import { RegisterPageClient } from "./RegisterPageClient";

export const metadata: Metadata = {
  title: "Crear cuenta | carwash.app",
};

export default function RegisterPage() {
  return (
    <Suspense>
      <RegisterPageClient />
    </Suspense>
  );
}
