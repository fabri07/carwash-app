"use client";

import { FormSubmitError } from "@/lib/errors";
import { toast } from "@/stores/toastStore";
import { operacionErrorMessage, type OperacionAction } from "./errors";
import {
  useAllVehicleSizes,
  useChargeableServices,
  usePaymentMethods,
  useResourceList,
} from "./queries";
import { QUEUED_NOTICE } from "./useActionRunner";
import type { Outcome } from "./useOperacionSubmit";

/** Catálogo que necesitan las tres cargas a mano: tamaños, servicios, puestos y medios de pago. */
export function useCargaData() {
  const sizes = useAllVehicleSizes();
  const services = useChargeableServices();
  const resources = useResourceList();
  const methods = usePaymentMethods();
  return {
    loading: sizes.isLoading || services.isLoading || resources.isLoading || methods.isLoading,
    error: sizes.isError || services.isError || resources.isError || methods.isError,
    sizes: sizes.data?.items ?? [],
    services: services.data ?? [],
    resources: resources.data?.items ?? [],
    methods: methods.data?.items ?? [],
  };
}

/**
 * Cierra el envío de una carga: un rechazo vuelve al formulario como error general (lo cargado no
 * se pierde: react-hook-form conserva los valores) y un guardado sin señal avisa que quedó en cola.
 * Devuelve `true` si salió bien o quedó encolado.
 */
export function settle(out: Outcome, action: OperacionAction, okMessage: string): void {
  if (out.status === "error") {
    throw new FormSubmitError({ field: null, message: operacionErrorMessage(out.error, action) });
  }
  if (out.status === "queued") toast.info(QUEUED_NOTICE);
  else toast.success(okMessage);
}
