"""scripts/smoke_health.sh — el smoke post-deploy exige ambiente Y commit.

Se ejecuta el script de verdad contra un servidor HTTP local que responde como /health.
El caso que justifica el script es el tercero: una versión vieja que sigue sirviendo
(porque la migración del deploy nuevo falló) responde el ambiente correcto, y un smoke
que mirara solo el ambiente daría verde.
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


@pytest.fixture
def health() -> Iterator[tuple[str, dict[str, str]]]:
    respuesta: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — nombre de la API de http.server
            cuerpo = json.dumps(respuesta).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/health", respuesta
    server.shutdown()


def _correr(url: str, env: str, sha: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(SCRIPT), url, env, sha],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "SMOKE_INTENTOS": "2",
            "SMOKE_PAUSA": "0",
        },
        timeout=60,
        check=False,
    )


def test_pasa_cuando_sirve_el_commit_del_deploy(health):
    url, respuesta = health
    respuesta.update(env="staging", commit="abc123")
    resultado = _correr(url, "staging", "abc123")
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr


def test_falla_si_el_ambiente_es_otro(health):
    url, respuesta = health
    respuesta.update(env="production", commit="abc123")
    resultado = _correr(url, "staging", "abc123")
    assert resultado.returncode == 1
    assert "se esperaba 'staging'" in resultado.stdout


def test_falla_si_sigue_sirviendo_la_version_anterior(health):
    url, respuesta = health
    respuesta.update(env="staging", commit="version-anterior")
    resultado = _correr(url, "staging", "abc123")
    assert resultado.returncode == 1
    assert "nunca llegó a servir" in resultado.stdout


def test_falla_si_nadie_responde():
    resultado = _correr("http://127.0.0.1:9/health", "staging", "abc123")
    assert resultado.returncode == 1
