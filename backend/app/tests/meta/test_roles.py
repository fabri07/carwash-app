"""ADR-0005 — roles como Enum conectado (parte estática; la de Postgres va en security/)."""

import re

from app.domain.roles import Role
from app.persistence.models import User
from app.tests.meta._rutas import BACKEND, fuentes_de_app


def test_los_valores_son_mayuscula_y_sin_superadmin():
    assert {r.value for r in Role} == {"OWNER", "STAFF"}
    assert all(r.value == r.value.upper() == r.name for r in Role)


def test_la_columna_usa_el_enum_nativo():
    tipo = User.__table__.c.role.type
    assert tipo.enum_class is Role and tipo.native_enum is True and tipo.name == "role"


def test_la_migracion_congela_los_mismos_valores():
    migracion = (BACKEND / "app/persistence/migrations/versions/0001_inicial.py").read_text()
    congelados = re.search(r"ROLES = \(([^)]*)\)", migracion)
    assert congelados is not None
    assert set(re.findall(r'"(\w+)"', congelados.group(1))) == {r.value for r in Role}


def test_no_hay_literales_de_rol_en_el_codigo():
    patron = re.compile(r"""(require_role\(\s*["']|role\s*(==|!=)\s*["'])""")
    culpables = [
        f"{p}:{n}"
        for p in fuentes_de_app()
        for n, linea in enumerate(p.read_text().splitlines(), 1)
        if patron.search(linea)
    ]
    assert not culpables, f"literales de rol: {culpables}"
