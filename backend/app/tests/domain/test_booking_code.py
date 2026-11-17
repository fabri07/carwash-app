"""Código público del turno — FASE-5-CONTRATO §4.2 (paso 7)."""

import itertools

import pytest

from app.domain.booking_code import (
    CODE_ALPHABET,
    CODE_LENGTH,
    CODE_PREFIX,
    new_booking_code,
    normalize_booking_code,
)


def test_el_alfabeto_no_tiene_caracteres_ambiguos():
    assert len(CODE_ALPHABET) == len(set(CODE_ALPHABET)) == 31
    assert not set("0O1IL") & set(CODE_ALPHABET)


def test_un_codigo_nuevo_tiene_prefijo_y_seis_simbolos_del_alfabeto():
    for _ in range(200):
        code = new_booking_code()
        assert code.startswith(CODE_PREFIX) and len(code) == len(CODE_PREFIX) + CODE_LENGTH
        assert set(code[len(CODE_PREFIX) :]) <= set(CODE_ALPHABET)


def test_el_azar_se_inyecta():
    simbolos = itertools.cycle("ABC")
    assert new_booking_code(lambda _alfabeto: next(simbolos)) == "TUR-ABCABC"


@pytest.mark.parametrize(
    ("tipeado", "esperado"),
    [
        ("TUR-K7M2QX", "TUR-K7M2QX"),
        ("tur-k7m2qx", "TUR-K7M2QX"),
        ("  k7m2qx ", "TUR-K7M2QX"),
        ("TUR K7M2QX", "TUR-K7M2QX"),
        ("TURK7M2QX", "TUR-K7M2QX"),
        ("k7m 2qx", "TUR-K7M2QX"),
        ("turabc", "TUR-TURABC"),  # un código que empieza con TUR no pierde su principio
    ],
)
def test_normaliza_lo_que_tipea_el_cliente(tipeado, esperado):
    assert normalize_booking_code(tipeado) == esperado


@pytest.mark.parametrize("tipeado", [None, "", "TUR-", "TUR-K7M2Q", "TUR-K7M2QXX", "TUR-K0M2QX"])
def test_no_adivina(tipeado):
    assert normalize_booking_code(tipeado) is None


def test_el_codigo_de_un_turno_a_mano_es_interno_y_no_sirve_para_el_turnero():
    from app.domain.booking_code import (  # noqa: PLC0415
        INTERNAL_CODE_PREFIX,
        new_internal_booking_code,
        normalize_booking_code,
    )

    codigo = new_internal_booking_code()
    assert codigo.startswith(INTERNAL_CODE_PREFIX) == codigo.startswith("INT-")
    assert len(codigo) == len("INT-") + 6
    assert set(codigo[4:]) <= set(CODE_ALPHABET)
    # El cliente no lo consulta ni lo cancela con un WhatsApp: no es un código del turnero.
    assert normalize_booking_code(codigo) is None
    assert new_internal_booking_code(lambda alfabeto: alfabeto[0]) == "INT-222222"
