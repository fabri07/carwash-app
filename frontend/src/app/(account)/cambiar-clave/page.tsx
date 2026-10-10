import type { Metadata } from "next";

import { ChangePasswordPageClient } from "@/features/auth/ChangePasswordPageClient";

export const metadata: Metadata = {
  title: "Cambiar contraseña | carwash.app",
};

export default function ChangePasswordPage() {
  return <ChangePasswordPageClient />;
}
