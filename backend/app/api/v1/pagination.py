"""Paginación como dependencia obligatoria — ADR-0006.

Adaptado de Véktor (`utils/pagination.py`), donde era código muerto
(`rg PaginationParams app` → una línea: su definición). Se conserva el clamp
`[1, 200]`, ahora declarado en la validación: `limit=100000` es 422, no un
recorte silencioso.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


@dataclass(frozen=True)
class PaginationParams:
    limit: int
    offset: int


def pagination_params(
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginationParams:
    return PaginationParams(limit=limit, offset=offset)


Paginacion = Annotated[PaginationParams, Depends(pagination_params)]
