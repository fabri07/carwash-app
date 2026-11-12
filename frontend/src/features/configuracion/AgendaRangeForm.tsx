"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Form } from "@/components/ui/form";
import {
  DialogFormFooter,
  SelectField,
  SwitchField,
  TextField,
} from "@/features/configuracion/fields";
import { useFormSubmit } from "@/hooks/useFormSubmit";
import type { Resource } from "@/types/api";
import { agendaRangeSchema, type AgendaRangeInput } from "@/validation/agenda";

interface AgendaRangeFormProps {
  /** `block`: cierra la agenda; `opening`: la abre fuera del horario habitual. */
  kind: "block" | "opening";
  resources: Resource[];
  /** Día que viene propuesto (hoy, en la zona del negocio). */
  defaultDate: string;
  onSubmit: (values: AgendaRangeInput) => Promise<void>;
  onCancel: () => void;
}

/** Alta de un bloqueo o una apertura especial: día completo o franja, un puesto o todos. */
export function AgendaRangeForm({
  kind,
  resources,
  defaultDate,
  onSubmit,
  onCancel,
}: AgendaRangeFormProps) {
  const form = useForm<AgendaRangeInput>({
    resolver: zodResolver(agendaRangeSchema),
    defaultValues: {
      date: defaultDate,
      full_day: kind === "block",
      start_time: kind === "block" ? "" : "09:00",
      end_time: kind === "block" ? "" : "13:00",
      resource_id: "",
      reason: "",
    },
  });
  const { onSubmit: submit, formError } = useFormSubmit(form, onSubmit);
  const fullDay = form.watch("full_day");
  return (
    <Form {...form}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <TextField control={form.control} name="date" label="Día" type="date" />
        <SwitchField
          control={form.control}
          name="full_day"
          label="Todo el día"
          hint={
            kind === "block"
              ? "Apagalo para bloquear solo una franja de ese día."
              : "Apagalo para abrir solo una franja de ese día."
          }
        />
        {!fullDay && (
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField control={form.control} name="start_time" label="Desde" type="time" />
            <TextField control={form.control} name="end_time" label="Hasta" type="time" />
          </div>
        )}
        <SelectField
          control={form.control}
          name="resource_id"
          label="Puesto"
          options={[
            { value: "", label: "Todos los puestos" },
            ...resources.map((r) => ({ value: r.id, label: r.name })),
          ]}
        />
        <TextField
          control={form.control}
          name="reason"
          label="Motivo (opcional)"
          placeholder={kind === "block" ? "Feriado, mantenimiento…" : "Domingo especial…"}
        />
        <DialogFormFooter
          formError={formError}
          submitting={form.formState.isSubmitting}
          submitLabel={kind === "block" ? "Crear bloqueo" : "Crear apertura"}
          onCancel={onCancel}
        />
      </form>
    </Form>
  );
}
