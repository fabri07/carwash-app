"""ADR-0012 — prod sale de tag, staging sale de `main`. Los workflows son de `deploy`."""

import pytest

from app.tests.meta._rutas import REPO


def _workflow(nombre: str) -> str:
    archivo = REPO / ".github/workflows" / nombre
    if not archivo.exists():
        pytest.skip(f"{nombre} es de `deploy` y todavía no existe")
    return archivo.read_text()


def test_prod_no_se_despliega_desde_un_push_a_rama():
    wf = _workflow("deploy-prod.yml")
    assert "tags:" in wf
    assert "branches:" not in wf


def test_staging_sale_de_main():
    wf = _workflow("deploy-staging.yml")
    assert "branches:" in wf and "main" in wf
