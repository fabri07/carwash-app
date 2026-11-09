"""Glosario del rubro en código — FASE-4-CONTRATO Y6.

Lee `app/data/glosario.json`, que genera `scripts/build_glosario.py` desde
`docs/GLOSARIO-RUBRO.md` (el test de deriva A12 los mantiene iguales). Es igual para todos los
negocios y de solo lectura: no hay tabla ni `tenant_id`.

Qué da:

- **plantillas** (`template`): los servicios que el wizard sugiere. Solo términos `servicio` y
  `adicional` (ver el docstring del script para la regla); lo demás nunca se sugiere como ítem;
- **sinónimos de búsqueda** (`aliases_for`): los del término, su nombre en inglés, las frases
  del cliente que apuntan a él y los defectos que resuelve (quien busca "sacar rayas" llega a
  *Corrección de pintura*).
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path

from app.domain.search import normalize

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "glosario.json"

SELLABLE_TYPES = frozenset({"servicio", "adicional"})


class TemplateKind(StrEnum):
    LAVADERO = "lavadero"
    DETAILING = "detailing"


@dataclass(frozen=True)
class GlossaryTerm:
    code: str
    term: str
    type: str
    english: str
    synonyms: tuple[str, ...]
    section: int
    section_title: str


class Glossary:
    def __init__(self, data: dict[str, object]) -> None:
        raw_terms = data["terms"]
        assert isinstance(raw_terms, list)
        self.terms: dict[str, GlossaryTerm] = {
            t["code"]: GlossaryTerm(
                code=t["code"],
                term=t["term"],
                type=t["type"],
                english=t["english"],
                synonyms=tuple(t["synonyms"]),
                section=t["section"],
                section_title=t["section_title"],
            )
            for t in raw_terms
        }
        templates = data["templates"]
        assert isinstance(templates, dict)
        self._templates: dict[str, tuple[str, ...]] = {k: tuple(v) for k, v in templates.items()}
        # Alias por servicio: el término mismo + frases del cliente + defectos que resuelve.
        aliases: dict[str, list[str]] = {}
        for term in self.terms.values():
            aliases.setdefault(term.code, []).extend([term.term, term.english, *term.synonyms])
        defects = data["defects"]
        assert isinstance(defects, list)
        for defect in defects:
            term = self.terms[defect["code"]]
            for service in defect["services"]:
                aliases.setdefault(service, []).extend([term.term, term.english, *term.synonyms])
        phrases = data["phrases"]
        assert isinstance(phrases, list)
        for phrase in phrases:
            for service in phrase["services"]:
                aliases.setdefault(service, []).append(phrase["text"])
        # Normalizados una sola vez acá: la búsqueda los compara tal cual, sin renormalizar.
        self._aliases = {
            code: tuple(dict.fromkeys(n for n in map(normalize, values) if n))
            for code, values in aliases.items()
        }

    def get(self, code: str) -> GlossaryTerm | None:
        return self.terms.get(code)

    def is_sellable(self, code: str) -> bool:
        term = self.terms.get(code)
        return term is not None and term.type in SELLABLE_TYPES

    def template(self, kind: TemplateKind) -> list[GlossaryTerm]:
        """Los términos que sugiere la plantilla, en el orden del glosario."""
        return [self.terms[code] for code in self._templates[kind.value]]

    def aliases_for(self, code: str) -> tuple[str, ...]:
        """Sinónimos de búsqueda del término `code`, ya normalizados (vacío si no existe)."""
        return self._aliases.get(code, ())


@cache
def load_glossary() -> Glossary:
    return Glossary(json.loads(DATA_FILE.read_text(encoding="utf-8")))
