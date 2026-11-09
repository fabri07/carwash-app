#!/usr/bin/env python3
"""Genera `app/data/glosario.json` desde `docs/GLOSARIO-RUBRO.md` — FASE-4-CONTRATO Y6 y A12.

El glosario vive en código (sin tablas): el `.md` es la fuente que edita el dueño y el `.json` lo
que lee la app. `tests/meta/test_glosario.py` (A12) corre `build()` y exige que el `.json`
commiteado sea idéntico: si se toca uno sin regenerar, el test se pone rojo.

    python scripts/build_glosario.py            # reescribe app/data/glosario.json
    python scripts/build_glosario.py --check    # sale con 1 si el .json está vencido

Qué lee:

- Toda tabla con columna **Término** (secciones 1 a 11): término, tipo, inglés, sinónimos.
  `code` es el término normalizado con guiones bajos (`"Tratamiento cerámico"` →
  `tratamiento_ceramico`), único y estable mientras no se renombre el término.
- La sección 5 (defectos): la columna "Lo resuelve" se resuelve contra los términos y pasa a ser
  el mapeo defecto → servicios (`defects`). Una técnica resuelta se reemplaza por el servicio del
  que forma parte (`TECHNIQUE_PARENT`): quien busca "sacar rayas" quiere algo vendible.
- La sección 12 (cómo lo pide el cliente): frases → términos (`phrases`), para la búsqueda.

**Plantillas** (regla del contrato, §11.5): `lavadero` = tipo `servicio` y `adicional` de las
secciones 1 y 2; `detailing` = ídem, secciones 1 a 4. Una `tecnica`, un `defecto`, un `producto`,
un `insumo`, un `modificador` o una `entidad` **nunca** es plantilla: solo búsqueda.

Sin PII: es vocabulario público del rubro.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.domain.search import normalize  # noqa: E402

REPO = BACKEND.parent
SOURCE = REPO / "docs" / "GLOSARIO-RUBRO.md"
TARGET = BACKEND / "app" / "data" / "glosario.json"

SELLABLE_TYPES = ("servicio", "adicional")
#: Secciones del `.md` (número) que alimentan cada plantilla.
TEMPLATE_SECTIONS: dict[str, tuple[int, ...]] = {
    "lavadero": (1, 2),
    "detailing": (1, 2, 3, 4),
}
DEFECTS_SECTION = 5
PHRASES_SECTION = 12

#: Una técnica no se vende: si un defecto la nombra, apunta al servicio del que es parte.
#: Clave y valor son nombres de término, ya normalizados.
TECHNIQUE_PARENT: dict[str, str] = {
    "descontaminado quimico": "descontaminado",
    "lijado en humedo": "correccion de pintura",
}

_SECTION = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$")
_PARENS = re.compile(r"\([^)]*\)")
_QUOTED = re.compile(r'"([^"]+)"')


def code_of(term: str) -> str:
    """`"Vinilo / ploteo"` → `vinilo_ploteo`: el término normalizado con `_`."""
    return normalize(term).replace(" ", "_")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _tables(markdown: str) -> list[tuple[int, str, list[str], list[list[str]]]]:
    """`(número de sección, título, encabezado, filas)` de cada tabla del documento."""
    found: list[tuple[int, str, list[str], list[list[str]]]] = []
    number, title = 0, ""
    header: list[str] | None = None
    rows: list[list[str]] = []

    def close() -> None:
        nonlocal header, rows
        if header is not None:
            found.append((number, title, header, rows))
        header, rows = None, []

    for line in markdown.splitlines():
        match = _SECTION.match(line)
        if match:
            close()
            number, title = int(match.group(1)), match.group(2)
            continue
        if not line.lstrip().startswith("|"):
            close()
            continue
        cells = _cells(line)
        if header is None:
            header = cells
        elif all(set(c) <= set("-: ") for c in cells):
            continue
        else:
            rows.append(cells)
    close()
    return found


def _resolve(name: str, by_name: dict[str, str], by_alias: dict[str, str]) -> str | None:
    """Código del término que nombra `name` (por nombre y, si no, por sinónimo exacto)."""
    key = normalize(_PARENS.sub("", name))
    if not key:
        return None
    return by_name.get(key) or by_alias.get(key)


def _resolve_all(
    value: str, by_name: dict[str, str], by_alias: dict[str, str], types: dict[str, str]
) -> list[str]:
    """Códigos de lo que nombra una celda tipo `"Retoque de pintura, Lijado en húmedo"`.

    Separa por `,`, `+` y `/`; una técnica se cambia por su servicio; solo quedan los vendibles.
    """
    codes: list[str] = []
    for part in re.split(r"[,+/]", value):
        code = _resolve(part, by_name, by_alias)
        if code is not None and types[code] not in SELLABLE_TYPES:
            parent = TECHNIQUE_PARENT.get(normalize(_PARENS.sub("", part)))
            code = by_name.get(parent) if parent else None
        if code is not None and code not in codes and types[code] in SELLABLE_TYPES:
            codes.append(code)
    return codes


def build(markdown: str) -> dict[str, Any]:
    """El contenido de `glosario.json`, determinista, a partir del texto del `.md`."""
    terms: list[dict[str, Any]] = []
    raw_defects: list[tuple[str, str]] = []
    raw_phrases: list[tuple[list[str], str]] = []

    for number, title, header, rows in _tables(markdown):
        columns = {name: i for i, name in enumerate(header)}
        if "Término" in columns:
            for row in rows:
                term = row[columns["Término"]]
                kind = row[columns["Tipo"]]
                if number == DEFECTS_SECTION:
                    raw_defects.append((code_of(term), row[columns["Lo resuelve"]]))
                terms.append(
                    {
                        "code": code_of(term),
                        "term": term,
                        "type": kind,
                        "english": row[columns["Inglés"]],
                        "synonyms": _split(row[columns["También lo buscan como"]])
                        if "También lo buscan como" in columns
                        else [],
                        "section": number,
                        "section_title": title,
                    }
                )
        elif number == PHRASES_SECTION and header[0].startswith("Lo que escribe"):
            for row in rows:
                raw_phrases.append((_QUOTED.findall(row[0]), row[1]))

    codes = [t["code"] for t in terms]
    duplicated = sorted({c for c in codes if codes.count(c) > 1})
    if duplicated:
        raise ValueError(f"códigos de glosario repetidos (renombrar el término): {duplicated}")

    by_name = {normalize(t["term"]): t["code"] for t in terms}
    by_alias: dict[str, str] = {}
    for t in terms:
        for synonym in t["synonyms"]:
            by_alias.setdefault(normalize(synonym), t["code"])
    types = {t["code"]: t["type"] for t in terms}

    defects = [
        {"code": code, "services": _resolve_all(resolves, by_name, by_alias, types)}
        for code, resolves in raw_defects
    ]
    phrases = [
        {"text": text, "services": _resolve_all(target, by_name, by_alias, types)}
        for texts, target in raw_phrases
        for text in texts
    ]
    templates = {
        kind: [t["code"] for t in terms if t["section"] in sections and t["type"] in SELLABLE_TYPES]
        for kind, sections in TEMPLATE_SECTIONS.items()
    }
    return {
        "source": "docs/GLOSARIO-RUBRO.md",
        "terms": terms,
        "defects": defects,
        "phrases": phrases,
        "templates": templates,
    }


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="no escribe: falla si está vencido")
    args = parser.parse_args()
    expected = render(build(SOURCE.read_text(encoding="utf-8")))
    if args.check:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != expected:
            print(f"{TARGET} está vencido: correr scripts/build_glosario.py", file=sys.stderr)
            return 1
        return 0
    TARGET.write_text(expected, encoding="utf-8")
    print(f"escrito {TARGET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
