"""scripts/smoke_health.sh — el smoke post-deploy exige ambiente, commit Y esquema.

Se ejecuta el script de verdad contra un servidor HTTP local que responde como
`/health` y `/ready`. Los dos casos que justifican el script:

* una versión vieja que sigue sirviendo (porque la migración del deploy nuevo falló)
  responde el ambiente correcto, y un smoke que mirara solo el ambiente daría verde;
* una base SIN MIGRAR responde `/health` perfecto —no toca ninguna tabla— y acepta
  conexiones, así que el deploy se declaraba sano con el esquema vacío. Pasó en
  staging el 2026-09-21 y duró dos días.
"""

import json
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.tests.meta._rutas import REPO

SCRIPT = REPO / "scripts" / "smoke_health.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("curl") is None or shutil.which("jq") is None, reason="necesita curl y jq"
)

READY_SANO = {
    "status": "ready",
    "env": "staging",
    "checks": {
        "database": {"ok": True, "error": None},
        "schema": {"ok": True, "error": None},
        "redis": {"ok": True, "error": None},
    },
}

READY_SIN_MIGRAR = {
    "status": "degraded",
    "env": "staging",
    "checks": {
        "database": {"ok": True, "error": None},
        "schema": {"ok": False, "error": "sin migrar: no existe alembic_version"},
        "redis": {"ok": True, "error": None},
    },
}


class _Estado:
    """Lo que el servidor de prueba va a contestar en cada ruta."""

    def __init__(self) -> None:
        self.health: dict[str, str] = {}
        self.ready: dict[str, object] = dict(READY_SANO)
        self.ready_status = 200


@pytest.fixture
def servidor() -> Iterator[tuple[str, _Estado]]:
    estado = _Estado()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — nombre de la API de http.server
            if self.path.endswith("/ready"):
                cuerpo, codigo = json.dumps(estado.ready).encode(), estado.ready_status
            else:
                cuerpo, codigo = json.dumps(estado.health).encode(), 200
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", estado
    server.shutdown()


def _correr(base: str, env: str, sha: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(SCRIPT), base, env, sha],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "SMOKE_INTENTOS": "2",
            "SMOKE_READY_INTENTOS": "1",
            "SMOKE_PAUSA": "0",
        },
        timeout=60,
        check=False,
    )


def test_pasa_cuando_sirve_el_commit_del_deploy_y_esta_sano(servidor):
    base, estado = servidor
    estado.health.update(env="staging", commit="abc123")
    resultado = _correr(base, "staging", "abc123")
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert "está listo" in resultado.stdout


def test_falla_si_el_ambiente_es_otro(servidor):
    base, estado = servidor
    estado.health.update(env="production", commit="abc123")
    resultado = _correr(base, "staging", "abc123")
    assert resultado.returncode == 1
    assert "se esperaba 'staging'" in resultado.stdout


def test_falla_si_sigue_sirviendo_la_version_anterior(servidor):
    base, estado = servidor
    estado.health.update(env="staging", commit="version-anterior")
    resultado = _correr(base, "staging", "abc123")
    assert resultado.returncode == 1
    assert "nunca llegó a servir" in resultado.stdout


def test_falla_si_la_base_no_esta_migrada_aunque_health_este_perfecto(servidor):
    """EL caso de staging: `/health` impecable, commit correcto, base vacía."""
    base, estado = servidor
    estado.health.update(env="staging", commit="abc123")
    estado.ready = dict(READY_SIN_MIGRAR)
    estado.ready_status = 503

    resultado = _correr(base, "staging", "abc123")

    assert resultado.returncode == 1
    assert "OK: staging sirve abc123" in resultado.stdout  # /health pasó
    assert "NO está sano" in resultado.stdout
    # El mensaje tiene que mandar a la causa real, no a buscar en la app.
    assert "Pre-Deploy Command" in resultado.stdout
    assert "sin migrar" in resultado.stdout


def test_falla_si_ready_esta_caido_por_otra_razon(servidor):
    base, estado = servidor
    estado.health.update(env="staging", commit="abc123")
    estado.ready = {"status": "degraded", "checks": {"redis": {"ok": False, "error": "timeout"}}}
    estado.ready_status = 503

    resultado = _correr(base, "staging", "abc123")

    assert resultado.returncode == 1
    assert "NO está sano" in resultado.stdout
    # Sin check de esquema fallado, no inventa el diagnóstico de las migraciones.
    assert "Pre-Deploy Command" not in resultado.stdout


def test_falla_si_nadie_responde():
    resultado = _correr("http://127.0.0.1:9", "staging", "abc123")
    assert resultado.returncode == 1
