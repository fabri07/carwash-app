"""ADR-0007 — formato, pre-commit y pin de Python 3.12."""

import tomllib

import pytest

from app.tests.meta._rutas import BACKEND, REPO


def _deploy_file(ruta: str):
    archivo = REPO / ruta
    if not archivo.exists():
        pytest.skip(f"{ruta} es de `deploy` y todavía no existe")
    return archivo.read_text()


def test_ruff_format_configurado():
    cfg = tomllib.loads((BACKEND / "pyproject.toml").read_text())
    assert "format" in cfg["tool"]["ruff"]


def test_requires_python_es_312():
    cfg = tomllib.loads((BACKEND / "pyproject.toml").read_text())
    assert cfg["project"]["requires-python"] == ">=3.12,<3.13"


def test_ci_corre_format_check():
    assert "ruff format --check" in _deploy_file(".github/workflows/ci-backend.yml")


def test_precommit_incluye_ruff_format():
    assert "id: ruff-format" in _deploy_file(".pre-commit-config.yaml")


def test_pin_de_python_consistente():
    assert _deploy_file(".python-version").strip() == "3.12"
    assert "python:3.12-slim" in _deploy_file("backend/Dockerfile")
    assert 'python-version: "3.12"' in _deploy_file(".github/workflows/ci-backend.yml")
