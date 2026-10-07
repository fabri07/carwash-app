"""Repositorio de cotizaciones — FASE-3-CONTRATO §1.4 y §2.3."""

from app.persistence.models.quote import Quote
from app.persistence.repositories.base import BaseRepository


class QuoteRepository(BaseRepository[Quote]):
    model = Quote
