"""Tablas de transición como datos — FASE-3-CONTRATO §2.

Una máquina es un mapa `acción → (estados de origen, estado destino)`. Lo que no está
en la tabla es `InvalidTransition`: whitelist, nunca blacklist ([corregir] R-O-018).
`None` como origen es la creación (∅ en el contrato).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.domain.exceptions import InvalidTransition


@dataclass(frozen=True, slots=True)
class Transition[S]:
    sources: frozenset[S | None]
    target: S


def freeze[A, S](table: dict[A, Transition[S]]) -> Mapping[A, Transition[S]]:
    """Vista de solo lectura: la tabla no se toca en runtime."""
    return MappingProxyType(table)


def next_state[A, S](
    machine: str, table: Mapping[A, Transition[S]], current: S | None, action: A
) -> S:
    transition = table.get(action)
    if transition is None or current not in transition.sources:
        raise InvalidTransition(machine, current, action)
    return transition.target
