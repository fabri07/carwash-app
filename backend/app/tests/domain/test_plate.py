"""`plate.py` — FASE-3-CONTRATO §1.2 y §3, R-C-008/009."""

import pytest

from app.domain.enums import PlateFormat
from app.domain.exceptions import InvalidPlateError
from app.domain.plate import MAX_PLATE_LENGTH, classify_plate, normalize_plate


@pytest.mark.parametrize(
    ("crudo", "normalizada"),
    [
        ("ab123cd", "AB123CD"),
        (" AB 123 CD ", "AB123CD"),
        ("abc-123", "ABC123"),
        ("abc.123", "ABC123"),
        ("ñandú 12", "AND12"),  # solo A-Z0-9 ASCII
        ("A1234567890"[:10], "A123456789"),  # justo 10
        ("SIN0001", "SIN0001"),  # no es centinela: tiene un 1
        ("SIN1", "SIN1"),
    ],
)
def test_normalize_plate(crudo, normalizada):
    assert normalize_plate(crudo) == normalizada


@pytest.mark.parametrize(
    "crudo",
    [
        None,
        "",
        "   ",
        "---",
        "SIN000",
        "sin000",
        "SIN 000",
        "SIN",
        "SIN0",
        "000000",
        "0",
        "SINPATENTE",
    ],
)
def test_centinelas_y_vacio_son_none(crudo):
    # R-C-009: `SIN000` era una convención humana para "sin patente"; ahora es NULL.
    assert normalize_plate(crudo) is None


def test_mas_de_diez_caracteres_se_rechaza_en_vez_de_truncar():
    # El legacy truncaba a 10 en silencio: dos patentes distintas podían colisionar.
    assert MAX_PLATE_LENGTH == 10
    with pytest.raises(InvalidPlateError):
        normalize_plate("ABC123DEF45")


@pytest.mark.parametrize(
    ("normalizada", "formato"),
    [
        ("ABC123", PlateFormat.AR_1994),
        ("AB123CD", PlateFormat.MERCOSUR),
        ("A123BCD", PlateFormat.OTRO),  # moto Mercosur: se acepta con advertencia
        ("AB123C", PlateFormat.OTRO),
        ("ABCD123", PlateFormat.OTRO),
        ("123ABC", PlateFormat.OTRO),
        ("AB1234CD", PlateFormat.OTRO),
        ("ABC1234", PlateFormat.OTRO),
    ],
)
def test_classify_plate(normalizada, formato):
    assert classify_plate(normalizada) is formato
