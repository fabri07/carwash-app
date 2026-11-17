import { render } from "@testing-library/react";

import {
  MIN_TOUCH_TARGET_PX,
  TOUCH_TARGET_CLASS,
  classMinPx,
  findSmallTouchTargets,
} from "@/lib/touchTargets";
import { expectTouchTargets } from "@/lib/touchTargets.testing";

describe("touchTargets", () => {
  it("la constante son 44 px y la clase exportada los cumple en ambos ejes", () => {
    expect(MIN_TOUCH_TARGET_PX).toBe(44);
    expect(classMinPx(TOUCH_TARGET_CLASS, "h")).toBe(44);
    expect(classMinPx(TOUCH_TARGET_CLASS, "w")).toBe(44);
  });

  it("lee h-N, min-h-N, size-N y valores arbitrarios", () => {
    expect(classMinPx("h-10", "h")).toBe(40);
    expect(classMinPx("h-14 min-h-11", "h")).toBe(56);
    expect(classMinPx("size-12", "w")).toBe(48);
    expect(classMinPx("h-[44px]", "h")).toBe(44);
    expect(classMinPx("min-h-[2.75rem]", "h")).toBe(44);
    expect(classMinPx("w-full p-2", "w")).toBeNull();
    expect(classMinPx("h-12", "w")).toBeNull();
  });

  it("acepta controles grandes, exentos y los cubiertos por un label grande", () => {
    const { container } = render(
      <div>
        <button className="min-h-11 min-w-11">Llegó</button>
        <button className="h-14 w-14">Iniciar</button>
        <a href="#x" className="size-12">
          x
        </a>
        <button style={{ minHeight: "44px", minWidth: "3rem" }}>E</button>
        <a href="#legal" data-touch-exempt>
          términos
        </a>
        <label className="min-h-11 min-w-11">
          <input type="checkbox" />
        </label>
        <input type="hidden" />
      </div>,
    );
    expect(findSmallTouchTargets(container)).toEqual([]);
    expectTouchTargets(container);
  });

  it("detecta los chicos y expectTouchTargets falla con el detalle", () => {
    const { container } = render(
      <div>
        <button className="h-8 w-8">Chico</button>
        <button className="min-h-11">Sin ancho</button>
        <button>Sin nada</button>
      </div>,
    );
    expect(findSmallTouchTargets(container)).toHaveLength(3);
    expect(() => expectTouchTargets(container)).toThrow(/tooSmall/);
  });
});
