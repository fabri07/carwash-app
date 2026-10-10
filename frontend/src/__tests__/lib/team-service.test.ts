import type { InternalAxiosRequestConfig } from "axios";

import { api } from "@/lib/api";
import {
  createProfile,
  createStaff,
  deleteProfile,
  deleteStaff,
  listProfiles,
  listStaff,
  resetStaffPassword,
  updateProfile,
  updateStaff,
} from "@/services/team.service";

describe("team.service (FASE-4-CONTRATO §4)", () => {
  const seen: InternalAxiosRequestConfig[] = [];
  beforeAll(() => {
    api.defaults.adapter = async (config) => {
      seen.push(config);
      return { data: { ok: true }, status: 200, statusText: "OK", headers: {}, config };
    };
  });

  it("habla con los endpoints del contrato", async () => {
    await listProfiles({ limit: 25, offset: 0 });
    await createProfile({ name: "Cajero", permissions: ["CAJA_VER"] }, "k1");
    await updateProfile("p1", { name: "Caja" });
    await deleteProfile("p1");
    await listStaff({ limit: 25, offset: 25 });
    await createStaff({ username: "juan", password: "12345678", permission_profile_id: "p1" });
    await updateStaff("s1", { email: null });
    await deleteStaff("s1");
    await resetStaffPassword("s1", { password: "12345678" });

    expect(seen.map((c) => `${c.method} ${c.url}`)).toEqual([
      "get /permission-profiles",
      "post /permission-profiles",
      "patch /permission-profiles/p1",
      "delete /permission-profiles/p1",
      "get /staff",
      "post /staff",
      "patch /staff/s1",
      "delete /staff/s1",
      "post /staff/s1/reset-password",
    ]);
    expect(seen[0]!.params).toEqual({ limit: 25, offset: 0 });
    expect(seen[4]!.params).toEqual({ limit: 25, offset: 25 });
    // El alta manda Idempotency-Key solo si se la pasan.
    expect(seen[1]!.headers["Idempotency-Key"]).toBe("k1");
    expect(seen[5]!.headers["Idempotency-Key"]).toBeUndefined();
    // `email: null` explícito viaja: es lo que borra el email.
    expect(JSON.parse(seen[6]!.data as string)).toEqual({ email: null });
  });
});
