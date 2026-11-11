"""PR 4.5 — cada tipografía del catálogo tiene su fuente declarada en el front.

`next/font` necesita la fuente escrita en el código (no se puede cargar por nombre en tiempo de
ejecución): si el catálogo suma una y `frontend/src/lib/fonts.ts` no, el negocio la elegiría y
vería la del sistema sin que nadie se entere.
"""

import re

from app.domain.branding import DEFAULT_FONT, FONTS
from app.tests.meta._rutas import BACKEND

FONTS_TS = BACKEND.parent / "frontend/src/lib/fonts.ts"


def test_cada_tipografia_tiene_su_variable_en_el_front():
    fuente = FONTS_TS.read_text(encoding="utf-8")
    bloque = fuente.split("FONT_VARS", 1)[1]
    declaradas = set(re.findall(r"^\s+(\w+): \"--font-[a-z-]+\",$", bloque, flags=re.M))
    assert declaradas == set(FONTS) - {DEFAULT_FONT}


def test_cada_familia_se_importa_de_next_font():
    fuente = FONTS_TS.read_text(encoding="utf-8")
    importadas = re.search(r"import \{([^}]+)\} from \"next/font/google\"", fuente)
    assert importadas is not None
    nombres = {n.strip() for n in importadas.group(1).split(",")}
    for font in FONTS.values():
        if font.family is not None:
            assert font.family.replace(" ", "_") in nombres, font.code
