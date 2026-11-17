/**
 * Targets táctiles (contrato Fase 6, A8): todo control operativo mide al menos
 * 44 x 44 px (guía de Apple/WCAG 2.5.5). Tailwind 3: `11` = 2.75rem = 44 px.
 */
export const MIN_TOUCH_TARGET_PX = 44;

/** Clases para un control que debe cumplir el mínimo. */
export const TOUCH_TARGET_CLASS = "min-h-11 min-w-11";

/** Controles que el usuario toca. */
export const INTERACTIVE_SELECTOR =
  'button, a[href], input:not([type="hidden"]), select, textarea, [role="button"], [role="tab"], [role="checkbox"], [role="switch"], [role="menuitem"]';

const PX_PER_UNIT = 4; // escala de Tailwind: 1 unidad = 0.25rem = 4 px

/**
 * Alto o ancho mínimo (px) que una clase de Tailwind garantiza para `axis`, o
 * `null` si no dice nada. Entiende `h-N`/`min-h-N`/`size-N`, `h-[44px]` y
 * `h-[2.75rem]`.
 */
export function classMinPx(className: string, axis: "h" | "w"): number | null {
  let best: number | null = null;
  for (const token of className.split(/\s+/)) {
    const m = /^(?:min-)?(?:(h|w)|size)-(?:(\d+(?:\.\d+)?)|\[(\d+(?:\.\d+)?)(px|rem)\])$/.exec(
      token,
    );
    if (!m) continue;
    if (m[1] && m[1] !== axis) continue;
    const px = m[2] ? Number(m[2]) * PX_PER_UNIT : Number(m[3]) * (m[4] === "rem" ? 16 : 1);
    best = best === null ? px : Math.max(best, px);
  }
  return best;
}

function styleMinPx(el: HTMLElement, axis: "h" | "w"): number | null {
  const raw =
    axis === "h" ? el.style.minHeight || el.style.height : el.style.minWidth || el.style.width;
  const m = /^(\d+(?:\.\d+)?)(px|rem)$/.exec(raw);
  if (!m) return null;
  return Number(m[1]) * (m[2] === "rem" ? 16 : 1);
}

/**
 * Controles de `root` que NO garantizan 44 px (alto y ancho). jsdom no calcula
 * layout, así que se leen las clases de Tailwind y el estilo en línea. Un
 * control se exime con `data-touch-exempt` (p. ej. un link dentro de un texto).
 */
export function findSmallTouchTargets(root: ParentNode): HTMLElement[] {
  const small: HTMLElement[] = [];
  root.querySelectorAll<HTMLElement>(INTERACTIVE_SELECTOR).forEach((el) => {
    if (el.hasAttribute("data-touch-exempt")) return;
    // Un input dentro de un <label> grande lo toca el label.
    const hit = el.closest("label") ?? el;
    for (const axis of ["h", "w"] as const) {
      const cls = hit.getAttribute("class") ?? "";
      const px = Math.max(classMinPx(cls, axis) ?? 0, styleMinPx(hit, axis) ?? 0);
      if (px < MIN_TOUCH_TARGET_PX) {
        small.push(el);
        return;
      }
    }
  });
  return small;
}
