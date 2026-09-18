"""ADR-0008 — el piso de cobertura vive en un solo lugar y solo sube."""

import tomllib

import pytest

from app.tests.meta._rutas import APP, BACKEND, REPO


def _fail_under() -> int:
    cfg = tomllib.loads((BACKEND / "pyproject.toml").read_text())
    return int(cfg["tool"]["coverage"]["report"]["fail_under"])


def test_el_piso_es_al_menos_80():
    assert _fail_under() >= 80


def test_el_piso_no_bajo_respecto_del_historico():
    historico = int((BACKEND / ".coverage-floor").read_text().strip())
    assert _fail_under() >= historico, f"el piso bajó de {historico} a {_fail_under()}"


@pytest.mark.parametrize(
    "ruta", ["Makefile", ".github/workflows/ci-backend.yml"], ids=["makefile", "ci-backend"]
)
def test_nadie_repite_el_numero_fuera_de_pyproject(ruta):
    archivo = REPO / ruta
    if not archivo.exists():
        pytest.skip(f"{ruta} es de `deploy` y todavía no existe")
    assert (
        "cov-fail-under" not in archivo.read_text()
    ), f"{ruta}: el piso se declara sólo en pyproject.toml"


def test_todo_pragma_no_cover_explica_por_que():
    # Forma exigida: `# pragma: no cover  # motivo`. Este archivo se excluye: nombra
    # la cadena para buscarla.
    marca = "pragma" + ": no cover"
    culpables = [
        f"{p}:{n}"
        for p in APP.rglob("*.py")
        if p.name != "test_compuerta_de_cobertura.py"
        for n, linea in enumerate(p.read_text().splitlines(), 1)
        if marca in linea and "#" not in linea.split(marca, 1)[1]
    ]
    assert not culpables, f"pragma sin motivo: {culpables}"


def test_los_tests_de_aislamiento_existen_y_estan_habilitados():
    ruta = APP / "tests/security/test_aislamiento_tenants.py"
    if not ruta.exists():
        pytest.skip("test cruzado de tenants: lo escribe el Tester-aislamiento de T3")
    assert "skip" not in ruta.read_text()
