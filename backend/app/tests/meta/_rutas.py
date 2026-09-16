"""Helpers compartidos por los tests meta."""

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
REPO = BACKEND.parent
APP = BACKEND / "app"


def fuentes_de_app() -> list[Path]:
    """Código de la app, sin tests ni migraciones."""
    return [p for p in APP.rglob("*.py") if "tests" not in p.parts and "migrations" not in p.parts]
