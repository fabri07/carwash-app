"use client";

import { QueryClientProvider } from "@tanstack/react-query";

import { AuthHydrationBoundary } from "@/components/auth/AuthHydrationBoundary";
import { ServiceWorkerRegistrar } from "@/components/pwa/ServiceWorkerRegistrar";
import { ToastBridge } from "@/components/ToastBridge";
import { Toaster } from "@/components/ui/sonner";
import { queryClient } from "@/lib/queryClient";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthHydrationBoundary>{children}</AuthHydrationBoundary>
      <Toaster position="top-center" richColors closeButton />
      <ToastBridge />
      <ServiceWorkerRegistrar />
    </QueryClientProvider>
  );
}
