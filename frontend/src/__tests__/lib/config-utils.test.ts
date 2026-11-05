import { hhmm } from "@/lib/time";
import { bpsToPercent, formatPercent, parsePercent, percentToBps } from "@/lib/percent";
import {
  businessHoursSchema,
  paymentMethodSchema,
  resourceSchema,
  vehicleSizeSchema,
} from "@/validation/catalog";
import { businessSchema, depositSchema } from "@/validation/settings";

describe("percent", () => {
  it("convierte bps y porcentaje sin perder el punto básico", () => {
    expect(bpsToPercent(2000)).toBe(20);
    expect(bpsToPercent(1250)).toBe(12.5);
    expect(percentToBps(20)).toBe(2000);
    expect(percentToBps(12.5)).toBe(1250);
    expect(percentToBps(0.29)).toBe(29); // 0.29 * 100 = 28.999… en float
  });

  it("formatea en argentino", () => {
    expect(formatPercent(2000)).toBe("20 %");
    expect(formatPercent(1250)).toBe("12,5 %");
    expect(formatPercent(0)).toBe("0 %");
  });

  it("lee coma o punto y rechaza lo ambiguo", () => {
    expect(parsePercent("20")).toBe(20);
    expect(parsePercent("12,5")).toBe(12.5);
    expect(parsePercent(" 12.5 % ")).toBe(12.5);
    expect(parsePercent("")).toBeNull();
    expect(parsePercent("abc")).toBeNull();
    expect(parsePercent("1,234")).toBeNull();
    expect(parsePercent("-5")).toBeNull();
  });
});

describe("time", () => {
  it("recorta los segundos para el input de hora", () => {
    expect(hhmm("09:30:00")).toBe("09:30");
    expect(hhmm("09:30")).toBe("09:30");
  });
});

describe("validación de configuración", () => {
  const business = {
    slug: "Mi-Lavadero",
    address: "",
    whatsapp_e164: "",
    instagram_url: "",
    welcome_text: "",
    cancellation_policy: "",
  };

  it("negocio: el slug se normaliza y los opcionales pueden quedar vacíos", () => {
    expect(businessSchema.parse(business).slug).toBe("mi-lavadero");
    expect(businessSchema.safeParse({ ...business, slug: "a b" }).success).toBe(false);
    expect(businessSchema.safeParse({ ...business, slug: "ab" }).success).toBe(false);
    expect(businessSchema.safeParse({ ...business, whatsapp_e164: "3515551234" }).success).toBe(
      false,
    );
    expect(businessSchema.safeParse({ ...business, whatsapp_e164: "+5493515551234" }).success).toBe(
      true,
    );
    expect(businessSchema.safeParse({ ...business, instagram_url: "http://x.com/a" }).success).toBe(
      false,
    );
    expect(
      businessSchema.safeParse({ ...business, instagram_url: "https://instagram.com/lava" })
        .success,
    ).toBe(true);
    for (const url of ["https://instagram.com//", "https://instagram.com/?x=1"]) {
      expect(businessSchema.safeParse({ ...business, instagram_url: url }).success).toBe(false);
    }
    expect(businessSchema.safeParse({ ...business, welcome_text: "x".repeat(281) }).success).toBe(
      false,
    );
  });

  const deposit = {
    deposit_enabled: true,
    deposit_percent: "20",
    hold_min: "30",
    tolerance_min: "20",
    late_cancel_threshold_min: "120",
    min_advance_min: "0",
    quote_validity_hours: "24",
  };

  it("seña: porcentaje 0,01–100 y minutos enteros", () => {
    expect(depositSchema.safeParse(deposit).success).toBe(true);
    expect(depositSchema.safeParse({ ...deposit, deposit_percent: "0" }).success).toBe(false);
    expect(depositSchema.safeParse({ ...deposit, deposit_percent: "101" }).success).toBe(false);
    expect(depositSchema.safeParse({ ...deposit, deposit_percent: "veinte" }).success).toBe(false);
    expect(depositSchema.safeParse({ ...deposit, hold_min: "0" }).success).toBe(false);
    expect(depositSchema.safeParse({ ...deposit, hold_min: "1,5" }).success).toBe(false);
    expect(depositSchema.safeParse({ ...deposit, min_advance_min: "0" }).success).toBe(true);
    expect(depositSchema.safeParse({ ...deposit, quote_validity_hours: "" }).success).toBe(false);
  });

  it("catálogo: código en mayúsculas, franja que cierra después, medio de pago útil", () => {
    expect(vehicleSizeSchema.parse({ code: "suv", label: "SUV", sort_order: "1" }).code).toBe(
      "SUV",
    );
    expect(vehicleSizeSchema.safeParse({ code: "a b", label: "x", sort_order: "1" }).success).toBe(
      false,
    );
    expect(resourceSchema.safeParse({ name: "  ", sort_order: "0" }).success).toBe(false);
    expect(
      businessHoursSchema.safeParse({ weekday: "1", opens_at: "09:00", closes_at: "09:00" })
        .success,
    ).toBe(false);
    expect(
      businessHoursSchema.safeParse({ weekday: "8", opens_at: "09:00", closes_at: "10:00" })
        .success,
    ).toBe(false);
    expect(
      businessHoursSchema.safeParse({ weekday: "7", opens_at: "09:00", closes_at: "10:00" })
        .success,
    ).toBe(true);
    const method = {
      code: "EFECTIVO",
      label: "Efectivo",
      commission_percent: "0",
      settlement_account: "",
      for_income: true,
      for_expense: false,
    };
    expect(paymentMethodSchema.safeParse(method).success).toBe(true);
    expect(paymentMethodSchema.safeParse({ ...method, for_income: false }).success).toBe(false);
    expect(paymentMethodSchema.safeParse({ ...method, commission_percent: "101" }).success).toBe(
      false,
    );
  });
});
