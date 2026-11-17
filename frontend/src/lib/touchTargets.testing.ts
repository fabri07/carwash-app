import { findSmallTouchTargets, MIN_TOUCH_TARGET_PX } from "@/lib/touchTargets";

/**
 * Helper de test para 6.2: falla si algún control de `container` no garantiza
 * 44 x 44 px. Uso: `expectTouchTargets(container)` tras renderizar la pantalla.
 */
export function expectTouchTargets(container: ParentNode): void {
  const small = findSmallTouchTargets(container);
  const detail = small.map((el) => el.outerHTML.slice(0, 120));
  expect({ min: MIN_TOUCH_TARGET_PX, tooSmall: detail }).toEqual({
    min: MIN_TOUCH_TARGET_PX,
    tooSmall: [],
  });
}
