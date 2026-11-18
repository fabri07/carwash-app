/**
 * Piso táctil de las pantallas de operación (FASE-6-CONTRATO A8): 44 px mínimo en TODO control,
 * porque se opera con las manos mojadas. Una sola constante para que no se reparta en cada
 * componente; `undersizedControls` la verifica sobre el DOM renderizado (lo usan los tests).
 */

/** Clases de Tailwind que garantizan 44 px (2.75 rem) de alto y ancho mínimos. */
export const TOUCH_TARGET = "min-h-11 min-w-11";

/** Botón de acción principal (LLEGÓ, INICIAR, FINALIZAR, COBRAR…): grande, de ancho completo. */
export const BIG_ACTION = "min-h-16 w-full text-xl font-bold";

/** Clases que declaran un alto mínimo de al menos 44 px (`h-11` = 2.75 rem en adelante). */
const SIZED =
  /(?:^|\s)(?:min-h-(?:11|12|14|16|20|touch)|h-(?:11|12|14|16|20)|min-h-\[(?:4[4-9]|[5-9]\d|\d{3,})px\])(?:\s|$)/;

const CONTROLS =
  'button, a[href], input:not([type="hidden"]), select, textarea, [role="button"], [role="tab"]';

/**
 * Controles dentro de `root` que NO declaran un piso de 44 px. En jsdom no hay layout ni CSS, así
 * que se mira la clase (el único lugar donde el piso está escrito). Vacío = todo cumple.
 */
export function undersizedControls(root: ParentNode): Element[] {
  return Array.from(root.querySelectorAll(CONTROLS)).filter(
    (el) => !SIZED.test(el.getAttribute("class") ?? ""),
  );
}
