"""Alta terminada para los negocios que ya cumplen los requisitos.

Revision ID: 0009_onboarding_existentes
Revises: 0008_catalogo_vehiculos
Create Date: 2026-11-10

Por qué (FASE-4-CONTRATO §11.9, 4.6-8): el PR 4.6 manda al dueño sin `onboarding_completed_at` al
wizard. Los negocios que ya existían (staging, y cualquiera dado de alta antes) tienen la columna en
`NULL` aunque su catálogo esté completo: sin esto quedarían encerrados en el wizard.

Marca `onboarding_completed_at = now()` **solo** a los negocios que ya cumplen los requisitos de §6,
con la misma regla que `OnboardingService` (solo filas vivas): ≥ 1 tamaño, ≥ 1 servicio, precio vivo
para cada servicio vivo × tamaño vivo, ≥ 1 puesto, ≥ 1 franja y ≥ 1 medio de pago con
`for_income`. Los que no cumplen siguen en `NULL` y pasan por el wizard, que reanuda donde estén.

Las tablas tienen `FORCE ROW LEVEL SECURITY` y el dueño de la migración no ve filas sin tenant en
contexto: el relleno corre con `NO FORCE` y vuelve a forzar al terminar (como 0007 y 0008).

Idempotente: solo toca filas en `NULL`; re-aplicar no cambia nada.

**Downgrade no-op**: la fecha es verdadera (el negocio cumplía) y el esquema de 0008 ya tiene la
columna; volverla a `NULL` mandaría al wizard a negocios que ya operan. No hay marca para distinguir
lo que puso esta migración de lo que puso un "Terminar" real.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_onboarding_existentes"
down_revision: str | None = "0008_catalogo_vehiculos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"

#: Las tablas que lee (y la que escribe) el relleno.
TABLES = (
    "tenant_settings",
    "vehicle_sizes",
    "services",
    "service_prices",
    "resources",
    "business_hours",
    "payment_methods",
)

BACKFILL = """
UPDATE tenant_settings ts
   SET onboarding_completed_at = now()
 WHERE ts.onboarding_completed_at IS NULL
   AND EXISTS (SELECT 1 FROM vehicle_sizes z
                WHERE z.tenant_id = ts.tenant_id AND z.voided_at IS NULL)
   AND EXISTS (SELECT 1 FROM services s
                WHERE s.tenant_id = ts.tenant_id AND s.voided_at IS NULL)
   AND NOT EXISTS (
        SELECT 1
          FROM services s
          JOIN vehicle_sizes z ON z.tenant_id = s.tenant_id AND z.voided_at IS NULL
         WHERE s.tenant_id = ts.tenant_id AND s.voided_at IS NULL
           AND NOT EXISTS (SELECT 1 FROM service_prices p
                            WHERE p.tenant_id = s.tenant_id AND p.service_id = s.id
                              AND p.vehicle_size_id = z.id AND p.voided_at IS NULL))
   AND EXISTS (SELECT 1 FROM resources r
                WHERE r.tenant_id = ts.tenant_id AND r.voided_at IS NULL)
   AND EXISTS (SELECT 1 FROM business_hours h
                WHERE h.tenant_id = ts.tenant_id AND h.voided_at IS NULL)
   AND EXISTS (SELECT 1 FROM payment_methods m
                WHERE m.tenant_id = ts.tenant_id AND m.voided_at IS NULL AND m.for_income)
"""


def upgrade() -> None:
    op.execute(LOCK_TIMEOUT)
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(BACKFILL)
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    """No-op a propósito (ver el docstring del módulo)."""
