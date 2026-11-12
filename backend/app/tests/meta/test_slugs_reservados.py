"""B12 · FASE-5-CONTRATO §3.8 — cada ruta de primer nivel del front es un slug reservado.

El turnero público vive en `/<slug>` (F5): un negocio con slug `login` taparía la pantalla de
login, o al revés. Dos chequeos:

- cada carpeta de primer nivel de `frontend/src/app/` (dentro de los grupos `(…)`, que no suman
  segmento a la URL) y cada entrada de `frontend/public/` que podría ser un slug está en
  `RESERVED_SLUGS`;
- la copia del front (`frontend/src/lib/reservedSlugs.ts`, que usa el formulario del negocio y su
  propio test de carpetas) es idéntica a la del backend.
"""

import re

from app.domain.slug import RESERVED_SLUGS, SLUG_PATTERN
from app.tests.meta._rutas import REPO

FRONT_APP = REPO / "frontend/src/app"
FRONT_PUBLIC = REPO / "frontend/public"
FRONT_LISTA = REPO / "frontend/src/lib/reservedSlugs.ts"
_SLUG = re.compile(SLUG_PATTERN)


def _segmentos_de_primer_nivel() -> set[str]:
    """Carpetas de `app/` que son un segmento de URL: las de los grupos `(…)` y las sueltas.

    Los segmentos dinámicos (`[slug]`) y privados (`_x`) no son una ruta fija: se saltean.
    """
    nombres: set[str] = set()
    for entrada in FRONT_APP.iterdir():
        if not entrada.is_dir():
            continue
        hijos = list(entrada.iterdir()) if entrada.name.startswith("(") else [entrada]
        nombres |= {h.name for h in hijos if h.is_dir()}
    return {n for n in nombres if not n.startswith(("[", "_", "("))}


def _publicos() -> set[str]:
    """Lo de `public/` que se sirve en `/<nombre>` y podría ser un slug (sin punto)."""
    return {e.name for e in FRONT_PUBLIC.iterdir() if _SLUG.fullmatch(e.name)}


def test_cada_carpeta_de_primer_nivel_del_front_es_un_slug_reservado():
    segmentos = _segmentos_de_primer_nivel()
    assert {"login", "configuracion", "catalogo"} <= segmentos  # el detector ve las rutas
    faltan = sorted(segmentos - RESERVED_SLUGS)
    assert not faltan, f"rutas del front que un negocio podría tomar como slug: {faltan}"


def test_cada_entrada_publica_del_front_es_un_slug_reservado():
    publicos = _publicos()
    assert "icons" in publicos
    faltan = sorted(publicos - RESERVED_SLUGS)
    assert not faltan, f"archivos públicos que un negocio podría tapar con su slug: {faltan}"


def test_la_lista_del_front_es_la_misma_que_la_del_backend():
    fuente = FRONT_LISTA.read_text(encoding="utf-8")
    bloque = fuente.split("RESERVED_SLUGS", 1)[1].split("];", 1)[0]
    en_front = set(re.findall(r'"([a-z0-9-]+)"', bloque))
    assert en_front == RESERVED_SLUGS
