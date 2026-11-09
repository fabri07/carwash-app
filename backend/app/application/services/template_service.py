"""Plantillas de servicios del rubro — FASE-4-CONTRATO §4 (PR 4.3) y adenda §11.5.

`get_template` arma la lista sugerida desde el glosario (sin precios: dependen de cada negocio y
de cada tamaño) y `apply` crea los servicios que el dueño eligió **con su precio por tamaño**.

- **Todo o nada.** Corre en la transacción del request: si un precio falla, no queda nada.
- **Sin precio no hay servicio** (D4-6, A8): cada ítem a crear necesita un precio para cada
  tamaño vivo; si falta alguno, 422 con la lista y no se crea ningún servicio.
- **Idempotente por `glossary_code`** (§6, Y6): un término que ya tiene un servicio vivo no se
  duplica ni se pisa: se informa como `skipped`. Repetir el paso del wizard es inocuo. Un
  servicio propio con el mismo nombre también se respeta (`skipped`, motivo `NAME`).
- **Categorías por sección** del glosario, creadas solo si en esa sección se crea algún servicio
  (idempotente por nombre).
- **Pocas consultas:** tamaños y servicios vivos se leen una vez por lote y los precios se insertan
  juntos (`insert_priced`), sin una consulta por precio.
- Los ítems que ya existen no se validan ni se tocan: el cuerpo viejo de un reintento sigue
  siendo válido aunque después se haya sumado un tamaño.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from app.application.services.errors import AlreadyExistsError, GlossaryCodeTakenError
from app.application.services.service_catalog_service import (
    PriceInput,
    ServiceCatalogService,
    ServiceView,
)
from app.domain.enums import PricingMode
from app.domain.exceptions import InvalidAmountError
from app.domain.glossary import GlossaryTerm, TemplateKind, load_glossary
from app.domain.search import normalize
from app.persistence.models.catalog import Service, ServiceCategory


class SkipReason(StrEnum):
    GLOSSARY_CODE = "GLOSSARY_CODE"  # ya hay un servicio vivo de ese término
    NAME = "NAME"  # ya hay un servicio propio con ese nombre


@dataclass(frozen=True)
class TemplateEntry:
    term: GlossaryTerm
    already_added: bool


@dataclass(frozen=True)
class TemplateItemInput:
    glossary_code: str
    pricing_mode: PricingMode
    prices: Sequence[PriceInput]


@dataclass(frozen=True)
class Skipped:
    glossary_code: str
    service: Service
    reason: SkipReason


@dataclass
class ApplyResult:
    created: list[ServiceView] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    categories_created: list[ServiceCategory] = field(default_factory=list)


class TemplateService(ServiceCatalogService):
    async def get_template(self, kind: TemplateKind) -> list[TemplateEntry]:
        """Lo que sugiere la plantilla, en el orden del glosario, marcando lo ya agregado.

        "Ya agregado" es un servicio vivo del término **o** con el mismo nombre (`search_key`):
        un servicio propio "Lavado exterior" ya cubre el término y no se vuelve a ofrecer.
        """
        index = await self.live_service_index()
        return [
            TemplateEntry(term, term.code in index.by_code or normalize(term.term) in index.by_key)
            for term in load_glossary().template(kind)
        ]

    async def apply(self, kind: TemplateKind, items: Sequence[TemplateItemInput]) -> ApplyResult:
        await self._enter()
        allowed = {t.code: t for t in load_glossary().template(kind)}
        codes = [i.glossary_code for i in items]
        if len(set(codes)) != len(codes):
            raise InvalidAmountError("a template item is repeated")
        outside = [c for c in codes if c not in allowed]
        if outside:
            raise InvalidAmountError(
                f"not part of the {kind.value} template: {', '.join(sorted(outside))}"
            )

        # Una lectura de tamaños y una de servicios para todo el lote (no una por ítem).
        live = await self._live_sizes()
        index = await self.live_service_index()
        result = ApplyResult()
        to_create: list[TemplateItemInput] = []
        for item in items:
            term = allowed[item.glossary_code]
            if item.glossary_code in index.by_code:
                result.skipped.append(
                    Skipped(
                        item.glossary_code,
                        index.by_code[item.glossary_code],
                        SkipReason.GLOSSARY_CODE,
                    )
                )
            elif normalize(term.term) in index.by_key:
                # Servicio propio con el mismo nombre: se respeta y no se vincula al término.
                result.skipped.append(
                    Skipped(item.glossary_code, index.by_key[normalize(term.term)], SkipReason.NAME)
                )
            else:
                to_create.append(item)
        # Todos los precios se validan antes de crear nada: o entra la plantilla entera o ninguna.
        for item in to_create:
            self._check_coverage(item.prices, live)

        categories: dict[int, ServiceCategory] = {}
        for item in to_create:
            term = allowed[item.glossary_code]
            if term.section not in categories:  # solo las secciones donde algo se crea
                category, created = await self.find_or_create_category(
                    term.section_title, term.section
                )
                categories[term.section] = category
                if created:
                    result.categories_created.append(category)
            try:
                view = await self.insert_priced(
                    Service(
                        tenant_id=self._tenant_id,
                        name=term.term,
                        search_key=normalize(term.term),
                        pricing_mode=item.pricing_mode,
                        category_id=categories[term.section].id,
                        glossary_code=term.code,
                    ),
                    self.sorted_by_size(item.prices, live),
                )
            except GlossaryCodeTakenError:
                # Otro request (otra pestaña del wizard) lo creó entre la lectura y el alta.
                winner = (await self.live_service_index()).by_code[term.code]
                result.skipped.append(Skipped(term.code, winner, SkipReason.GLOSSARY_CODE))
            except AlreadyExistsError:
                winner = (await self.live_service_index()).by_key[normalize(term.term)]
                result.skipped.append(Skipped(term.code, winner, SkipReason.NAME))
            else:
                result.created.append(view)
        return result
