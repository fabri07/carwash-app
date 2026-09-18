import { Droplets } from "lucide-react";

export default function PublicLayout({ children }: { children: React.ReactNode }) {
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
