"""Permisos de los empleados — FASE-4-CONTRATO §2 (Y4, Y5).

Como `Role` (ADR-0005): un solo Enum, en MAYÚSCULA, congelado. Cada permiso protege
endpoints concretos; el `OWNER` los tiene todos implícitamente y es el único que configura
el negocio y el equipo, así que no hay permiso para "dar permisos" (un empleado nunca se
da más de lo que tiene).

Se guardan como `text[]` en `permission_profiles.permissions`, con un CHECK de subconjunto
contra estos valores. Agregar uno es cambio de código **y** migración del CHECK: lo
verifica `tests/meta/test_permisos.py`.
"""

from enum import StrEnum


class Permission(StrEnum):
    AGENDA_VER = "AGENDA_VER"
    TURNOS_GESTIONAR = "TURNOS_GESTIONAR"
    JOBS_OPERAR = "JOBS_OPERAR"
    COBROS_REGISTRAR = "COBROS_REGISTRAR"
    COBROS_ANULAR = "COBROS_ANULAR"
    CAJA_VER = "CAJA_VER"
    CATALOGO_EDITAR = "CATALOGO_EDITAR"
    PRECIOS_EDITAR = "PRECIOS_EDITAR"
    CLIENTES_VER = "CLIENTES_VER"
    CLIENTES_EDITAR = "CLIENTES_EDITAR"
    GASTOS_REGISTRAR = "GASTOS_REGISTRAR"
    REPORTES_VER = "REPORTES_VER"


ALL_PERMISSIONS: frozenset[Permission] = frozenset(Permission)

#: Perfiles que `provision_tenant` crea en cada negocio (§2.2). Son editables: el dueño
#: los ajusta o crea otros. Cajero ⊇ Lavador (D4-10): cuando la misma persona cobra y
#: lava usa Cajero; Lavador es para cuando son personas distintas.
DEFAULT_PROFILES: dict[str, frozenset[Permission]] = {
    "Encargado": ALL_PERMISSIONS,
    "Cajero": frozenset(
        {
            Permission.AGENDA_VER,
            Permission.TURNOS_GESTIONAR,
            Permission.JOBS_OPERAR,
            Permission.COBROS_REGISTRAR,
            Permission.CAJA_VER,
            Permission.CLIENTES_VER,
            Permission.CLIENTES_EDITAR,
        }
    ),
    "Lavador": frozenset({Permission.AGENDA_VER, Permission.JOBS_OPERAR, Permission.CLIENTES_VER}),
}

#: Perfil que reciben los `STAFF` que ya existían antes de la migración 0004 (no pierden
#: acceso) y el que se usa si una carga no indica otro.
LEGACY_STAFF_PROFILE = "Encargado"


def sorted_permissions(permissions: frozenset[Permission] | set[Permission]) -> list[Permission]:
    """Orden estable (el del enum) para guardar y responder siempre igual."""
    return [p for p in Permission if p in permissions]
