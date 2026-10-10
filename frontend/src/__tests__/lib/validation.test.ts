import { newPassword, utf8Length } from "@/validation/auth";

describe("clave nueva: tope de bcrypt en bytes UTF-8", () => {
  it("cuenta bytes, no caracteres", () => {
    expect(utf8Length("abc")).toBe(3);
    expect(utf8Length("ñ")).toBe(2);
    expect(utf8Length("€")).toBe(3);
    expect(utf8Length("🚗")).toBe(4);
  });

  it("72 bytes pasa, 73 no", () => {
    expect(newPassword.safeParse("a".repeat(72)).success).toBe(true);
    expect(newPassword.safeParse("ñ".repeat(37)).success).toBe(false);
  });
});
