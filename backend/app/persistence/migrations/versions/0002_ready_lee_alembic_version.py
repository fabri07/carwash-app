"""El rol de runtime puede LEER alembic_version (lo necesita /ready).

Revision ID: 0002_ready_lee_alembic_version
Revises: 0001_inicial
Create Date: 2026-09-21

`/ready` compara la revisión aplicada en la base contra el head de este código, para
que un deploy sin migrar salga en rojo (staging estuvo dos días con la base vacía y
el deploy en verde). Ese chequeo corre sobre el engine de runtime, o sea el rol
`carwash_app`, que a propósito no tiene privilegios por default sobre ninguna tabla.
Sin este GRANT el chequeo tira `InsufficientPrivilege` y la red de seguridad se
convierte en su propio caño roto: /ready en 503 permanente y todos los deploys en
rojo apuntando al lugar equivocado.

El detalle de por qué es seguro está en `rls.grant_alembic_version_read`.
"""

from collections.abc import Sequence

from alembic import op

from app.persistence.db.rls import grant_alembic_version_read, revoke_alembic_version_read

revision: str = "0002_ready_lee_alembic_version"
down_revision: str | None = "0001_inicial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in grant_alembic_version_read():
        op.execute(statement)


def downgrade() -> None:
    for statement in revoke_alembic_version_read():
        op.execute(statement)
