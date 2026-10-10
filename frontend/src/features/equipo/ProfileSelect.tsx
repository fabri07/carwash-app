"use client";

import { FormControl } from "@/components/ui/form";
import { NativeSelect } from "@/components/ui/native-select";
import type { PermissionProfile } from "@/types/api";

/**
 * Selector de perfil para los formularios de empleado. Va dentro de un
 * `FormItem`: `FormControl` le pone el `id` que usa la etiqueta y el
 * `aria-invalid` del error. Nativo a propósito (ver `native-select.tsx`).
 */
interface ProfileSelectProps {
  profiles: PermissionProfile[];
  value: string;
  onChange: (id: string) => void;
  onBlur?: () => void;
  disabled?: boolean;
}

export function ProfileSelect({ profiles, value, onChange, onBlur, disabled }: ProfileSelectProps) {
  return (
    <FormControl>
      <NativeSelect
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        disabled={disabled}
      >
        <option value="" disabled>
          Elegí un perfil
        </option>
        {profiles.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </NativeSelect>
    </FormControl>
  );
}
