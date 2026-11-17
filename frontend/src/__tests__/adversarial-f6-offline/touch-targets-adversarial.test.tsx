/**
 * T3 6.3 — ataque 8: `findSmallTouchTargets` / `expectTouchTargets`.
 */
import { render } from "@testing-library/react";

import { classMinPx, findSmallTouchTargets } from "@/lib/touchTargets";
import { expectTouchTargets } from "@/lib/touchTargets.testing";

function small(ui: React.ReactElement) {
  const { container } = render(ui);
  return findSmallTouchTargets(container);
}

describe("8a. detecta controles chicos", () => {
  it.each([
    ["min-h-8 min-w-8", "min-h-8 min-w-8"],
    ["h-10 w-10", "h-10 w-10"],
    ["w-9 (alto sin clase)", "w-9"],
    ["h-9 w-9 icon", "h-9 w-9"],
    ["min-h-10 min-w-11", "min-h-10 min-w-11"],
    ["h-11 sin ancho", "h-11"],
    ["size-10", "size-10"],
    ["h-[40px] w-[40px]", "h-[40px] w-[40px]"],
    ["h-[2.5rem] w-[2.5rem]", "h-[2.5rem] w-[2.5rem]"],
    ["max-h-11 (tope, no piso)", "max-h-11 max-w-11"],
    ["sin clases", ""],
  ])("botón %s", (_n, cls) => {
    expect(small(<button className={cls}>x</button>)).toHaveLength(1);
  });

  it("botón ícono sin texto (h-8 w-8) y con aria-label", () => {
    expect(
      small(
        <button aria-label="Cerrar" className="h-8 w-8 rounded">
          <svg width="16" height="16" />
        </button>,
      ),
    ).toHaveLength(1);
  });

  it("estilos inline chicos: height/width 40px, minHeight 2rem", () => {
    expect(small(<button style={{ height: "40px", width: "40px" }}>x</button>)).toHaveLength(1);
    expect(small(<button style={{ minHeight: "2rem", minWidth: "2rem" }}>x</button>)).toHaveLength(
      1,
    );
    expect(small(<button style={{ minHeight: 30, minWidth: 30 }}>x</button>)).toHaveLength(1);
  });

  it("inline chico NO se compensa con una clase vacía, y clase grande + inline chico: gana el máximo (jsdom no resuelve cascada)", () => {
    expect(
      small(
        <button className="h-4 w-4" style={{ minHeight: "44px", minWidth: "44px" }}>
          x
        </button>,
      ),
    ).toEqual([]);
  });

  it.each([
    "a[href]",
    "input",
    "select",
    "textarea",
    "role=button",
    "role=tab",
    "role=checkbox",
    "role=switch",
    "role=menuitem",
  ])("otros controles: %s", (kind) => {
    const cls = "h-8 w-8";
    const ui =
      kind === "a[href]" ? (
        <a href="#x" className={cls}>
          x
        </a>
      ) : kind === "input" ? (
        <input className={cls} />
      ) : kind === "select" ? (
        <select className={cls} />
      ) : kind === "textarea" ? (
        <textarea className={cls} />
      ) : (
        <div role={kind.split("=")[1]} className={cls} />
      );
    expect(small(ui)).toHaveLength(1);
  });

  it("un input chico dentro de un label chico se detecta; dentro de un label grande no", () => {
    expect(
      small(
        <label className="h-8 w-8">
          <input type="checkbox" />
        </label>,
      ),
    ).toHaveLength(1);
    expect(
      small(
        <label className="min-h-11 min-w-11">
          <input type="checkbox" />
        </label>,
      ),
    ).toEqual([]);
  });

  it("expectTouchTargets lanza listando el outerHTML de cada chico", () => {
    const { container } = render(
      <div>
        <button className="h-8 w-8" data-id="uno">
          a
        </button>
        <button className="min-h-11 min-w-11">ok</button>
        <button className="h-10 w-10" data-id="dos">
          b
        </button>
      </div>,
    );
    expect(() => expectTouchTargets(container)).toThrow(/uno[\s\S]*dos/);
  });
});

describe("8b. no da falsos positivos con lo correcto", () => {
  it.each([
    "min-h-11 min-w-11",
    "h-11 w-11",
    "h-12 w-12",
    "size-12",
    "size-11",
    "h-14 w-14",
    "min-h-12 min-w-12",
    "h-[44px] w-[44px]",
    "h-[2.75rem] w-[2.75rem]",
    "h-8 w-8 min-h-11 min-w-11",
    "h-12 min-w-11",
    "p-2 min-h-11 min-w-11 text-sm",
  ])("clase %s", (cls) => {
    expect(small(<button className={cls}>x</button>)).toEqual([]);
  });

  it("data-touch-exempt en cualquier control", () => {
    expect(
      small(
        <div>
          <a href="#t" data-touch-exempt>
            t
          </a>
          <button className="h-4 w-4" data-touch-exempt>
            x
          </button>
          <input className="h-4" data-touch-exempt />
        </div>,
      ),
    ).toEqual([]);
  });

  it("inputs hidden no cuentan", () => {
    expect(small(<input type="hidden" />)).toEqual([]);
  });

  it("estilos inline correctos (px, rem, height+width)", () => {
    expect(small(<button style={{ minHeight: "44px", minWidth: "44px" }}>x</button>)).toEqual([]);
    expect(small(<button style={{ height: "3rem", width: "3rem" }}>x</button>)).toEqual([]);
    expect(small(<button style={{ height: 48, width: 48 }}>x</button>)).toEqual([]);
  });

  it("FALSO POSITIVO: botón de ancho completo `h-12 w-full` (muy común en móvil) cumple 44 px y no debería marcarse", () => {
    expect(small(<button className="h-12 w-full">Iniciar</button>)).toEqual([]);
  });

  it("FALSO POSITIVO: `min-h-11 flex-1` y `min-h-11 w-full` también cumplen en ancho", () => {
    expect(small(<button className="min-h-11 flex-1">x</button>)).toEqual([]);
    expect(small(<button className="min-h-11 w-full">x</button>)).toEqual([]);
  });

  it("FALSO POSITIVO: minHeight inline chico + height inline grande (el alto efectivo es el mayor)", () => {
    expect(
      small(
        <button style={{ minHeight: "10px", height: "50px", minWidth: "10px", width: "50px" }}>
          x
        </button>,
      ),
    ).toEqual([]);
  });

  it("FALSO POSITIVO: botón oculto (`hidden`/aria-hidden) no es tocable", () => {
    expect(
      small(
        <button hidden className="h-4 w-4">
          x
        </button>,
      ),
    ).toEqual([]);
  });
});

describe("8c. controles que el selector no mira (falsos negativos)", () => {
  it.each(["radio", "link", "option", "tab", "slider", "spinbutton"])(
    "HALLAZGO: [role=%s] chico debería detectarse",
    (role) => {
      expect(small(<div role={role} className="h-8 w-8" tabIndex={0} />)).toHaveLength(1);
    },
  );

  it("HALLAZGO: <summary> chico debería detectarse", () => {
    expect(
      small(
        <details>
          <summary className="h-8 w-8">m</summary>
        </details>,
      ),
    ).toHaveLength(1);
  });

  it("HALLAZGO: un botón chico dentro de un label grande no queda tapado por el label", () => {
    expect(
      small(
        <label className="min-h-11 min-w-11">
          <button className="h-6 w-6">x</button>
        </label>,
      ),
    ).toHaveLength(1);
  });

  it("variantes responsive/estado no cuentan como garantía (h-8 md:h-12 sigue chico)", () => {
    expect(small(<button className="h-8 w-8 md:h-12 md:w-12">x</button>)).toHaveLength(1);
  });

  it("classMinPx: conflicto h-14 h-8 devuelve el máximo (documenta)", () => {
    expect(classMinPx("h-14 h-8", "h")).toBe(56);
  });
});
