import { bodyTypeLabel, makeSchema, modelSchema } from "@/validation/vehicles";

describe("validation/vehicles", () => {
  it("nombres: recorta, exige una letra o número y respeta el largo", () => {
    expect(makeSchema.parse({ name: "  Citroën " })).toEqual({ name: "Citroën" });
    expect(makeSchema.safeParse({ name: "!!!" }).success).toBe(false);
    expect(makeSchema.safeParse({ name: "x".repeat(41) }).success).toBe(false);
    expect(modelSchema.safeParse({ name: "x".repeat(60), body_type: "SUV" }).success).toBe(true);
    expect(modelSchema.safeParse({ name: "Hilux", body_type: "CAMION" }).success).toBe(false);
  });

  it("etiqueta de carrocería, con respaldo al código", () => {
    expect(bodyTypeLabel("FURGON")).toBe("Furgón");
    expect(bodyTypeLabel("NUEVA" as never)).toBe("NUEVA");
  });
});
