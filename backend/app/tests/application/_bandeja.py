"""Armado de la bandeja del dueño (PR 5.3) sobre `_armado.Lavadero`.

El pedido de lista de espera (`PENDIENTE_REVISION`) lo crea el turnero público de 5.2, que corre
en paralelo: acá se arma igual que lo dejaría ese alta (misma fila, sin hold, con precio y seña
calculados) pasando un turno recién creado al estado de revisión. Datos sintéticos.
"""

import uuid
from datetime import datetime, time
from typing import Any

from app.domain.enums import BookingStatus
from app.persistence.models import Booking
from app.persistence.models.catalog import ServiceScheduleRule
from app.tests.application._armado import Lavadero, T

#: Franja del lunes en la zona del negocio (Córdoba, UTC−3): `T` (10:00 UTC) son las 07:00.
ABRE = time(6, 0)
CIERRA = time(18, 0)


async def en_revision(lv: Lavadero, **kw: Any) -> Booking:
    """Un pedido en la lista de espera (D5-9): `PENDIENTE_REVISION`, sin hold."""
    turno = await lv.turno(**kw)
    turno.status = BookingStatus.PENDIENTE_REVISION
    turno.hold_expires_at = None
    await lv.session.flush()
    return turno


async def horario_lunes(lv: Lavadero) -> None:
    await lv.catalogo.add_business_hours(1, ABRE, CIERRA)


async def regla(lv: Lavadero, servicio: uuid.UUID, weekday: int, desde: time, hasta: time) -> None:
    """Una ventana de inicio del servicio (§3.2), cargada directo: la edición es de 5.1."""
    lv.session.add(
        ServiceScheduleRule(
            tenant_id=lv.tenant_id,
            service_id=servicio,
            weekday=weekday,
            window_start=desde,
            last_start=hasta,
        )
    )
    await lv.session.flush()


def local(hora: int, minuto: int = 0, base: datetime = T) -> datetime:
    """Ese horario del día de `T` en Córdoba (UTC−3), como instante UTC."""
    return base.replace(hour=hora + 3, minute=minuto)
