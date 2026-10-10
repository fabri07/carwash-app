import { formatAmount, formatARS, parseARS } from "@/lib/money";

describe("formatARS (centavos enteros)", () => {
  it("formato argentino: punto de miles, coma decimal", () => {
    expect(formatARS(2000000)).toBe("$ 20.000,00");
    expect(formatARS(150)).toBe("$ 1,50");
    expect(formatARS(5)).toBe("$ 0,05");
    expect(formatARS(0)).toBe("$ 0,00");
    expect(formatARS(123456789)).toBe("$ 1.234.567,89");
    expect(formatARS(-2050)).toBe("-$ 20,50");
  });

  it("rechaza lo que no son centavos enteros", () => {
    expect(() => formatARS(10.5)).toThrow(RangeError);
    expect(() => formatARS(Number.NaN)).toThrow(RangeError);
  });

  it("formatAmount es lo mismo sin el signo pesos", () => {
    expect(formatAmount(2000050)).toBe("20.000,50");
  });
});

describe("parseARS (no repetir parseMoney_)", () => {
  it('"20.000" son veinte mil pesos, no veinte', () => {
    expect(parseARS("20.000")).toBe(2000000);
  });

  it('"abc" es null, no 0 silencioso', () => {
    expect(parseARS("abc")).toBeNull();
    expect(parseARS("abc")).not.toBe(0);
  });

  it("lee las formas habituales", () => {
    expect(parseARS("20000")).toBe(2000000);
    expect(parseARS("20.000,50")).toBe(2000050);
    expect(parseARS("1,5")).toBe(150);
    expect(parseARS("$ 1.234,56")).toBe(123456);
    expect(parseARS(" 0,05 ")).toBe(5);
    expect(parseARS("1.234.567")).toBe(123456700);
    expect(parseARS("0")).toBe(0);
  });

  it("lo ambiguo o inválido es null", () => {
    for (const s of [
      "",
      "   ",
      "$",
      "20.5",
      "1,234",
      "1,2,3",
      "1.23.456",
      "-5",
      "12a",
      ",5",
      "1.000.0",
    ]) {
      expect(parseARS(s)).toBeNull();
    }
  });

  it("un número fuera del rango seguro es null", () => {
    expect(parseARS("9".repeat(20))).toBeNull();
  });
});
