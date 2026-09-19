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


@pytest.mark.parametrize(
    ("nombre", "servicio"),
    [("deploy-staging.yml", "api-staging"), ("deploy-prod.yml", "api")],
)
def test_el_deploy_fija_el_commit_y_el_smoke_lo_exige(nombre, servicio):
    # `railway up` no inyecta RAILWAY_GIT_COMMIT_SHA: sin esto /health dice "unknown" y el
    # smoke no distingue la versión nueva de la anterior (que sigue sirviendo si la
    # migración falla). Un deploy fallado se vería verde.
    wf = _workflow(nombre)
    fija = wf.find(f'railway variable set "GIT_COMMIT_SHA=${{GITHUB_SHA}}" --service {servicio}')
    sube = wf.find(f"railway up --service {servicio}")
    assert 0 <= fija < sube, "GIT_COMMIT_SHA se fija antes de railway up"
    assert "--skip-deploys" in wf[fija:sube]
    assert "sh scripts/smoke_health.sh" in wf and '"${GITHUB_SHA}"' in wf[sube:]
