#!/usr/bin/env python3
"""Corta el commit si un archivo versionado contiene un teléfono o una patente REAL.

`docs/spec/` y `docs/legacy/` son local-only (.gitignore) porque tienen datos de
clientes reales de Sola CleanCars. Los agentes los leen para extraer reglas, y en la
Fase 3 uno copió un teléfono y una patente reales en un test. Este hook toma los
valores de esas dos carpetas y los busca en los archivos que se van a commitear.

Sin `docs/spec/` (CI, un clon limpio) no hay contra qué comparar: sale 0.
Uso: lo corre pre-commit con los archivos staged como argumentos.
"""

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUENTES = [RAIZ / "docs" / "spec", RAIZ / "docs" / "legacy"]

TELEFONO = re.compile(r"(?<!\d)\d{10,13}(?!\d)")
#: AAA000 (1994) y AA000AA (Mercosur). `SIN000`, `AAA000` y `AA000AA` son centinelas o
#: ejemplos de formato, no patentes: se excluyen.
PATENTE = re.compile(r"\b(?:[A-Z]{3}\d{3}|[A-Z]{2}\d{3}[A-Z]{2})\b")
NO_SON_PATENTES = {"SIN000", "AAA000", "AA000AA"}


def _valores_reales() -> set[str]:
    valores: set[str] = set()
    for carpeta in FUENTES:
        for archivo in carpeta.rglob("*"):
            if not archivo.is_file():
                continue
            texto = archivo.read_text(encoding="utf-8", errors="ignore")
            for tel in TELEFONO.findall(texto):
                valores.add(tel[-10:])  # sin 549/54: el número local es lo que identifica
            valores.update(p for p in PATENTE.findall(texto) if p not in NO_SON_PATENTES)
    return valores


def _normalizar(texto: str) -> str:
    """Saca separadores: `11 4444-5555` y `ab 123 cd` se comparan como `1144445555` y `AB123CD`."""
    return re.sub(r"[\s\-.()+]", "", texto).upper()


def main(archivos: list[str]) -> int:
    if not any(carpeta.is_dir() for carpeta in FUENTES):
        return 0
    reales = _valores_reales()
    hallazgos = []
    for nombre in archivos:
        ruta = Path(nombre)
        if not ruta.is_file() or any(p in ruta.resolve().parents for p in FUENTES):
            continue
        for n, linea in enumerate(ruta.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            plana = _normalizar(linea)
            hallazgos.extend(f"{nombre}:{n}" for valor in reales if valor in plana)
    if hallazgos:
        print("Datos reales de docs/spec o docs/legacy en archivos versionados (usar sintéticos):")
        print("\n".join(sorted(set(hallazgos))))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
