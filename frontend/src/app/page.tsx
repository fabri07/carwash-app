import { redirect } from "next/navigation";

/**
 * La raíz no tiene contenido propio en la Fase 2: manda al dashboard, y si no
 * hay sesión el middleware lo desvía a /login.
 */
export default function RootPage() {
  redirect("/dashboard");
}
