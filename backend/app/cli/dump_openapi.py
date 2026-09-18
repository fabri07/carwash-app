"""Vuelca el contrato OpenAPI a `backend/openapi.json` (ADR-0013).

    uv run python -m app.cli.dump_openapi

El archivo se commitea; el frontend genera sus tipos desde acá.
"""

import json
import sys
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[2] / "openapi.json"


def render() -> str:
    from app.main import create_app  # noqa: PLC0415

    spec = create_app().openapi()
    return json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    target = Path(args[0]) if args else OUTPUT
    target.write_text(render(), encoding="utf-8")
    print(f"openapi escrito en {target}")
    return 0


if __name__ == "__main__":  # pragma: no cover  # entrypoint de línea de comandos
    raise SystemExit(main())
