import { Droplets } from "lucide-react";

/**
 * Pantallas de cuenta con sesión pero sin el shell (hoy, `/cambiar-clave`): un
 * empleado con clave por cambiar no tiene menú que usar. Mismo marco que
 * `(public)`; la protección la pone `middleware.ts` por prefijo.
 */
export default function AccountLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-background px-4 py-10">
      <div className="mb-6 flex items-center gap-2 text-lg font-semibold">
        <Droplets className="h-7 w-7 text-primary" aria-hidden="true" />
        carwash.app
      </div>
      <div className="w-full max-w-sm">{children}</div>
    </main>
  );
}
