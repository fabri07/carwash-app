"""`money.py` — FASE-3-CONTRATO §3 y X3. Corrige `parseMoney_` (R-O-029)."""

import pytest

from app.domain.exceptions import DomainError, InvalidAmountError
from app.domain.money import BIGINT_MAX, apply_bps, format_ars, parse_ars

# --- B13: casos del legacy corregidos, uno por test -------------------------


def test_b13_miles_con_punto_no_se_lee_como_decimal():
    # El legacy devolvía 20 (factor 1000 de error, R-O-029).
    assert parse_ars("20.000") == 2_000_000


def test_b13_texto_no_numerico_se_rechaza_en_vez_de_devolver_cero():
    with pytest.raises(InvalidAmountError):
        parse_ars("abc")


def test_b13_comas_como_miles_se_rechazan():
    # El legacy reemplazaba solo la primera coma: "1,234,567" → 1.234567.
    with pytest.raises(InvalidAmountError):
        parse_ars("1,234,567")


# --- parse_ars ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "centavos"),
    [
        ("20000", 2_000_000),
        ("1.500.000,50", 150_000_050),
        ("20,5", 2050),
        ("20,50", 2050),
        ("20,05", 2005),
        ("$ 20.000", 2_000_000),
        ("$20.000", 2_000_000),
        ("  $  20.000,50  ", 2_000_050),
        (" $ 15.000 ", 1_500_000),  # espacio duro de copiar/pegar
        ("0", 0),
        ("0,50", 50),
        ("0,05", 5),
        ("999", 99_900),
        ("1.000", 100_000),
        ("12.345.678", 1_234_567_800),
    ],
)
def test_parse_ars_acepta_formato_argentino(texto, centavos):
    assert parse_ars(texto) == centavos


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "   ",
        "$",
        "abc",
        "20.00",  # ¿veinte o dos mil? ambiguo
        "20,000",  # ¿veinte mil (en-US) o veinte? ambiguo
        "20,555",  # más de dos decimales
        "1.5",
        "1234.567",  # miles mal agrupados
        "1.2345",
        "1.234.56",
        "20.",
        ",50",
        "20,",
        "-100",
        "- 100",
        "$ -100",
        "100-",
        "20 000",  # espacios internos
        "20.000.",
        "0.500",  # un grupo de miles no arranca en 0
        "00",
        "0100",
        "1e3",
        "٣",  # dígito no ASCII
        "20$",
        "$$20",
        "20,5,5",
        "1.000,5.0",
    ],
)
def test_parse_ars_rechaza_ambiguo_o_invalido(texto):
    with pytest.raises(InvalidAmountError):
        parse_ars(texto)


def test_parse_ars_rechaza_lo_que_no_entra_en_bigint():
    maximo_pesos = BIGINT_MAX // 100
    assert parse_ars(str(maximo_pesos)) == maximo_pesos * 100
    with pytest.raises(InvalidAmountError):
        parse_ars(str(maximo_pesos + 1))


def test_invalid_amount_es_error_de_dominio():
    assert issubclass(InvalidAmountError, DomainError)


# --- format_ars -------------------------------------------------------------


@pytest.mark.parametrize(
    ("centavos", "texto"),
    [
        (2_000_000, "$ 20.000"),
        (2_000_050, "$ 20.000,50"),
        (0, "$ 0"),
        (5, "$ 0,05"),
        (50, "$ 0,50"),
        (99_900, "$ 999"),
        (100_000, "$ 1.000"),
        (150_000_050, "$ 1.500.000,50"),
        (-150_000, "-$ 1.500"),
        (-5, "-$ 0,05"),
    ],
)
def test_format_ars(centavos, texto):
    assert format_ars(centavos) == texto


@pytest.mark.parametrize("centavos", [0, 1, 5, 50, 99, 100, 99_999, 2_000_050, 123_456_789_01])
def test_format_y_parse_son_inversas(centavos):
    assert parse_ars(format_ars(centavos)) == centavos


# --- apply_bps ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("centavos", "bps", "esperado"),
    [
        (2_000_000, 3000, 600_000),  # seña del 30 %
        (2_000_000, 0, 0),
        (2_000_000, 10_000, 2_000_000),
        (1, 5000, 1),  # 0,5 → 1 (half-up)
        (1, 4999, 0),  # 0,4999 → 0
        (3, 5000, 2),  # 1,5 → 2
        (5, 5000, 3),  # 2,5 → 3 (half-up, no half-even)
        (101, 5000, 51),  # 50,5 → 51
        (0, 3000, 0),
        (12_345, 299, 369),  # 369,1155 → 369 (comisión 2,99 %)
    ],
)
def test_apply_bps_redondea_half_up(centavos, bps, esperado):
    assert apply_bps(centavos, bps) == esperado


@pytest.mark.parametrize(("centavos", "bps"), [(-1, 100), (100, -1), (100, 10_001)])
def test_apply_bps_rechaza_fuera_de_rango(centavos, bps):
    with pytest.raises(InvalidAmountError):
        apply_bps(centavos, bps)
