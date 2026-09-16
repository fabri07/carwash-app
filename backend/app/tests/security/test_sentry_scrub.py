"""Scrubbing de Sentry: la patente es el PII fuerte de este dominio (A13, parte local)."""

from app.observability import sentry

PATENTE = "AB123CD"


def test_patente_redactada_en_headers_query_body_vars_breadcrumbs_y_extra():
    event = {
        "request": {
            "headers": {"X-Custom": PATENTE, "Cookie": "access_token=x", "Accept": "json"},
            "query_string": f"q={PATENTE}",
            "data": {"valor": PATENTE, "name": "ok"},
        },
        "exception": {"values": [{"stacktrace": {"frames": [{"vars": {"x": f"auto {PATENTE}"}}]}}]},
        "breadcrumbs": {"values": [{"data": {"url": f"/v1/x?p={PATENTE}"}}]},
        "extra": {"patente": "cualquier cosa", "dni_like": "30123456"},
    }
    out = sentry._scrub_event(event, {})
    req = out["request"]
    assert req["headers"]["X-Custom"] == "[Filtered]"
    assert req["headers"]["Cookie"] == "[Filtered]"
    assert req["headers"]["Accept"] == "json"
    assert req["query_string"] == "[Filtered]"
    assert req["data"] == {"valor": "[Filtered]", "name": "ok"}
    assert out["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["x"] == "[Filtered]"
    assert out["breadcrumbs"]["values"][0]["data"]["url"] == "[Filtered]"
    assert out["extra"] == {"patente": "[Filtered]", "dni_like": "[Filtered]"}
    assert PATENTE not in repr(out)


def test_formato_viejo_de_patente_y_body_crudo():
    out = sentry._scrub_event({"request": {"data": "patente ABC123 en texto"}}, {})
    assert out["request"]["data"] == "[Filtered]"


def test_query_sin_pii_se_conserva():
    out = sentry._scrub_event({"request": {"query_string": "limit=50"}}, {})
    assert out["request"]["query_string"] == "limit=50"


def test_traces_sampler_compara_path_exacto():
    assert sentry._traces_sampler({"asgi_scope": {"method": "OPTIONS", "path": "/v1/x"}}) == 0.0
    assert sentry._traces_sampler({"asgi_scope": {"method": "GET", "path": "/health"}}) == 0.0
    assert sentry._traces_sampler({"transaction_context": {"name": "GET /ready"}}) == 0.0
    tasa = sentry._traces_sampler({"transaction_context": {"name": "GET /health-scores/1"}})
    assert tasa > 0.0


def test_init_sin_dsn_es_no_op(settings_env):
    settings_env(APP_ENV="production", SENTRY_DSN="")
    sentry.init_sentry("web")
    assert sentry._resolve_release()
