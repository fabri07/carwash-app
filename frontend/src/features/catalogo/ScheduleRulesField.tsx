"use client";

import { useFieldArray, type Control } from "react-hook-form";
import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { WEEKDAYS } from "@/features/configuracion/BusinessHoursForm";
import type { ServiceEditInput } from "@/validation/services";

/**
 * Reglas horarias de un servicio (FASE-5-CONTRATO §3.2): en qué ventana puede
 * **arrancar** cada día. Varias filas el mismo día = varias ventanas (mañana y
 * tarde). Sin filas, el servicio arranca en cualquier momento del horario del
 * negocio. Se guardan como lista completa: lo que queda acá reemplaza lo anterior.
 */
export function ScheduleRulesField({ control }: { control: Control<ServiceEditInput> }) {
  const { fields, append, remove } = useFieldArray({ control, name: "schedule_rules" });
  return (
    <fieldset className="space-y-3 rounded-lg border p-3">
      <legend className="px-1 text-sm font-medium">Días y horarios en que puede empezar</legend>
      {fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Sin reglas: todo el horario del negocio. Agregá una ventana si este servicio solo se puede
          empezar algunos días o en ciertas horas.
        </p>
      ) : (
        <ul className="space-y-3">
          {fields.map((row, index) => (
            <li
              key={row.id}
              className="grid grid-cols-[1fr_auto] items-end gap-2 sm:grid-cols-[1.4fr_1fr_1fr_auto]"
            >
              <FormField
                control={control}
                name={`schedule_rules.${index}.weekday`}
                render={({ field }) => (
                  <FormItem className="col-span-2 sm:col-span-1">
                    <FormLabel>Día</FormLabel>
                    <FormControl>
                      <NativeSelect {...field}>
                        {WEEKDAYS.map((name, i) => (
                          <option key={name} value={String(i + 1)}>
                            {name}
                          </option>
                        ))}
                      </NativeSelect>
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={control}
                name={`schedule_rules.${index}.window_start`}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Desde</FormLabel>
                    <FormControl>
                      <Input type="time" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={control}
                name={`schedule_rules.${index}.last_start`}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Último inicio</FormLabel>
                    <FormControl>
                      <Input type="time" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={`Quitar la ventana ${index + 1}`}
                onClick={() => remove(index)}
              >
                <Trash2 />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => append({ weekday: "1", window_start: "09:00", last_start: "12:00" })}
      >
        <Plus aria-hidden="true" />
        Agregar ventana
      </Button>
    </fieldset>
  );
}
