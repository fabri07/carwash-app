import { buildCSV, downloadCSV, neutralizeFormula, toCSVValue, UTF8_BOM } from "@/lib/csv";

describe("csv: escape (A14)", () => {
  it("valores simples pasan tal cual; null y undefined son celda vacía", () => {
    expect(toCSVValue("Lavado")).toBe("Lavado");
    expect(toCSVValue(1500)).toBe("1500");
    expect(toCSVValue(null)).toBe("");
    expect(toCSVValue(undefined)).toBe("");
  });

  it("entrecomilla ante ; , comillas y saltos de línea, y duplica las comillas", () => {
    expect(toCSVValue("a;b")).toBe('"a;b"');
    expect(toCSVValue("a,b")).toBe('"a,b"');
    expect(toCSVValue('dijo "hola"')).toBe('"dijo ""hola"""');
    expect(toCSVValue("línea 1\nlínea 2")).toBe('"línea 1\nlínea 2"');
    expect(toCSVValue("con\rretorno")).toBe('"con\rretorno"');
  });

  it("neutraliza fórmulas (= + - @, tab, CR) anteponiendo un apóstrofo", () => {
    expect(toCSVValue("=HYPERLINK(1)")).toBe("'=HYPERLINK(1)");
    expect(toCSVValue("+54 11")).toBe("'+54 11");
    expect(toCSVValue("-2+3")).toBe("'-2+3");
    expect(toCSVValue("@SUM(A1)")).toBe("'@SUM(A1)");
    expect(neutralizeFormula("\tcmd")).toBe("'\tcmd");
    // Neutralizada Y con separador: se entrecomilla después de neutralizar.
    expect(toCSVValue("=1;2")).toBe('"\'=1;2"');
  });

  it("un número negativo puro no es fórmula", () => {
    expect(toCSVValue("-1500")).toBe("-1500");
    expect(toCSVValue(-20)).toBe("-20");
    expect(neutralizeFormula("-20,50")).toBe("-20,50");
  });

  it("arma filas con ; y CRLF", () => {
    expect(
      buildCSV(
        ["Usuario", "Email"],
        [
          ["juan", null],
          ["ana", "a;b@x.com"],
        ],
      ),
    ).toBe('Usuario;Email\r\njuan;\r\nana;"a;b@x.com"');
    expect(buildCSV(["a", "b"], [[1, 2]], ",")).toBe("a,b\r\n1,2");
  });
});

describe("downloadCSV", () => {
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  afterEach(() => {
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  });

  it("descarga con BOM, nombre con fecha y sin duplicar .csv", async () => {
    let blob: Blob | undefined;
    URL.createObjectURL = jest.fn((b: Blob) => {
      blob = b;
      return "blob:x";
    });
    URL.revokeObjectURL = jest.fn();
    const clicked: HTMLAnchorElement[] = [];
    const click = jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this);
    });

    downloadCSV("empleados.csv", ["Usuario"], [["juan"]], new Date("2026-10-09T12:00:00Z"));

    expect(clicked[0]?.download).toBe("empleados-2026-10-09.csv");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:x");
    // jsdom no trae `Blob.arrayBuffer`; FileReader sí.
    const buffer = await new Promise<ArrayBuffer>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as ArrayBuffer);
      reader.readAsArrayBuffer(blob!);
    });
    const bytes = new Uint8Array(buffer);
    // EF BB BF: el BOM UTF-8 que Excel necesita para leer los acentos.
    expect([...bytes.slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
    expect(String.fromCharCode(...bytes.slice(3))).toBe("Usuario\r\njuan");
    expect(UTF8_BOM).toBe("﻿");
    click.mockRestore();
  });
});
