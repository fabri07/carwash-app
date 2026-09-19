"""`phone.py` — FASE-3-CONTRATO §3, R-C-005/006/007."""

import dataclasses

import pytest

from app.domain.phone import PhoneReason, PhoneResult, normalize_phone_ar, phones_equivalent


@pytest.mark.parametrize(
    ("crudo", "e164"),
    [
        ("1144445555", "+5491144445555"),  # 10 dígitos
        ("11 4444-5555", "+5491144445555"),
        ("(0351) 412-3456", "+5493514123456"),  # 11 con 0 inicial
        ("01144445555", "+5491144445555"),
        ("5491144445555", "+5491144445555"),  # 13 con 549
        ("+54 9 11 4444-5555", "+5491144445555"),
        ("+54 9 11 2345-6789", "+5491123456789"),
        ("1123456789", "+5491123456789"),
    ],
)
def test_normaliza_a_e164(crudo, e164):
    assert normalize_phone_ar(crudo) == PhoneResult(e164=e164, ambiguous=False, reason=None)


@pytest.mark.parametrize(
    ("crudo", "motivo"),
    [
        ("543514123456", PhoneReason.MISSING_MOBILE_9),  # 12 con 54 sin 9
        ("+54 351 412-3456", PhoneReason.MISSING_MOBILE_9),
        ("351 15 412-3456", PhoneReason.LOCAL_PREFIX_15),  # área + 15 + abonado: 12
        ("11 15 2345-6789", PhoneReason.LOCAL_PREFIX_15),
        ("2966 15 123456", PhoneReason.LOCAL_PREFIX_15),
        ("0351 15 412-3456", PhoneReason.LOCAL_PREFIX_15),  # 13 con 0 inicial
        ("15 2345-6789", PhoneReason.LOCAL_PREFIX_15),  # 10 que arrancan con 15: sin área
        ("015 2345-6789", PhoneReason.LOCAL_PREFIX_15),
        ("+54 9 15 2345-6789", PhoneReason.LOCAL_PREFIX_15),
    ],
)
def test_lo_ambiguo_no_se_adivina(crudo, motivo):
    assert normalize_phone_ar(crudo) == PhoneResult(e164=None, ambiguous=True, reason=motivo)


@pytest.mark.parametrize(
    ("crudo", "motivo"),
    [
        (None, PhoneReason.EMPTY),
        ("", PhoneReason.EMPTY),
        ("sin teléfono", PhoneReason.EMPTY),
        ("412-3456", PhoneReason.INVALID_LENGTH),  # 7
        ("15 412-3456", PhoneReason.INVALID_LENGTH),  # 9: 15 local de Córdoba sin área
        ("93514123456", PhoneReason.INVALID_LENGTH),  # 11 sin 0 inicial
        ("549351412345", PhoneReason.INVALID_LENGTH),  # 12 con 549: le falta un dígito
        ("123456789012", PhoneReason.INVALID_LENGTH),  # 12 sin 54 ni 15 en posición de área
        ("1234567890123", PhoneReason.INVALID_LENGTH),  # 13 sin 549 ni 0
        ("+54 9 351 15 412-3456", PhoneReason.INVALID_LENGTH),  # 15
        ("0054 9 351 412-3456", PhoneReason.INVALID_LENGTH),  # 15
        ("0123456789", PhoneReason.INVALID_NATIONAL),  # nacional que arranca con 0
        ("00123456789", PhoneReason.INVALID_NATIONAL),
        ("5490123456789", PhoneReason.INVALID_NATIONAL),
    ],
)
def test_lo_invalido_no_es_ambiguo(crudo, motivo):
    assert normalize_phone_ar(crudo) == PhoneResult(e164=None, ambiguous=False, reason=motivo)


def test_el_resultado_es_inmutable():
    resultado = normalize_phone_ar("1144445555")
    with pytest.raises(dataclasses.FrozenInstanceError):
        resultado.e164 = "+5490000000000"  # a propósito: se prueba que falla


# --- phones_equivalent (solo migración F8) --------------------------------


@pytest.mark.parametrize(
    ("a", "b", "esperado"),
    [
        ("5491144445555", "1144445555", True),  # R-C-005: con y sin 549
        ("+54 9 11 4444-5555", "01144445555", True),
        ("1144445555", "1144445555", True),
        ("44445555", "1144445555", True),  # n = 8
        ("4445555", "1144445555", False),  # n = 7 < 8, aunque el sufijo coincide
        ("4445555", "4445555", False),  # iguales pero cortos: no alcanza
        ("1144445555", "1144445556", False),
        ("1111144445555", "2221144445555", True),  # solo cuentan los últimos 10
        ("", "1144445555", False),
        ("1144445555", "", False),
        (None, "1144445555", False),
        ("1144445555", None, False),
        (None, None, False),
    ],
)
def test_phones_equivalent(a, b, esperado):
    assert phones_equivalent(a, b) is esperado
    assert phones_equivalent(b, a) is esperado
