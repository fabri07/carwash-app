"""Almacenamiento de objetos (PR 4.5). R2 con un cliente falso: ningún test toca la red."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.config.settings import Settings
from app.infrastructure import storage as storage_mod
from app.infrastructure.storage import (
    IMMUTABLE_CACHE_CONTROL,
    InMemoryStorage,
    R2Storage,
    StorageError,
    get_storage,
)

R2_FALSO = {
    "R2_ACCOUNT_ID": "cuenta-falsa",
    "R2_ACCESS_KEY_ID": "clave-falsa",
    "R2_SECRET_ACCESS_KEY": "secreto-falso",
    "R2_BUCKET": "logos-test",
    "R2_PUBLIC_BASE_URL": "https://logos.example.com",
}


def _config(**cambios: str) -> Settings:
    return Settings.model_validate({**R2_FALSO, **cambios})


class ClienteFalso:
    def __init__(self, falla: bool = False) -> None:
        self.llamadas: list[tuple[str, dict]] = []
        self.falla = falla

    def put_object(self, **kwargs):
        self.llamadas.append(("put", kwargs))
        if self.falla:
            raise RuntimeError("red caída")

    def delete_object(self, **kwargs):
        self.llamadas.append(("delete", kwargs))
        if self.falla:
            raise RuntimeError("red caída")


def _r2(cliente: ClienteFalso) -> R2Storage:
    return R2Storage(_config(), client_factory=lambda _s: cliente)


async def test_r2_sube_con_tipo_y_cache_inmutable():
    cliente = ClienteFalso()
    await _r2(cliente).put("logos/t/abc.png", b"datos", "image/png")
    assert cliente.llamadas == [
        (
            "put",
            {
                "Bucket": "logos-test",
                "Key": "logos/t/abc.png",
                "Body": b"datos",
                "ContentType": "image/png",
                "CacheControl": IMMUTABLE_CACHE_CONTROL,
            },
        )
    ]


async def test_r2_borra():
    cliente = ClienteFalso()
    await _r2(cliente).delete("logos/t/abc.png")
    assert cliente.llamadas == [("delete", {"Bucket": "logos-test", "Key": "logos/t/abc.png"})]


@pytest.mark.parametrize("op", ["put", "delete"])
async def test_un_error_de_r2_es_storage_error(op):
    r2 = _r2(ClienteFalso(falla=True))
    with pytest.raises(StorageError):
        if op == "put":
            await r2.put("k", b"x", "image/png")
        else:
            await r2.delete("k")


def test_el_cliente_real_apunta_a_la_cuenta_de_r2():
    """boto3 arma el cliente sin conectarse: se verifica endpoint, región y firma."""
    cliente = storage_mod._boto3_client(_config())
    assert cliente.meta.endpoint_url == "https://cuenta-falsa.r2.cloudflarestorage.com"
    assert cliente.meta.region_name == "auto"
    assert cliente.meta.config.signature_version == "s3v4"


async def test_en_memoria_guarda_y_borra():
    mem = InMemoryStorage()
    await mem.put("a", b"1", "image/png")
    assert mem.objects["a"].data == b"1"
    await mem.delete("a")
    await mem.delete("no-existe")
    assert mem.objects == {}


def test_sin_variables_no_hay_storage(settings_env):
    settings_env(**dict.fromkeys(R2_FALSO, ""))
    assert get_storage() is None


def test_con_las_cinco_variables_hay_r2(settings_env):
    settings_env(**R2_FALSO)
    storage_mod._reset_r2_storage()
    try:
        assert isinstance(get_storage(), R2Storage)
        assert get_storage() is get_storage()
    finally:
        storage_mod._reset_r2_storage()


def test_el_cliente_se_crea_una_sola_vez_aunque_lo_pidan_muchos_hilos(settings_env, monkeypatch):
    """FastAPI resuelve `get_storage` (sync) en el threadpool: dos hilos a la vez no pueden crear
    dos clientes (boto3 con la sesión por defecto no es seguro entre hilos)."""
    settings_env(**R2_FALSO)
    storage_mod._reset_r2_storage()
    creados: list[int] = []

    def factory_lenta(_settings):
        creados.append(1)
        time.sleep(0.05)  # ensancha la ventana de la carrera
        return ClienteFalso()

    monkeypatch.setattr(storage_mod, "_boto3_client", factory_lenta)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            instancias = list(pool.map(lambda _: storage_mod._r2_storage(), range(8)))
        assert len(creados) == 1
        assert all(i is instancias[0] for i in instancias)
    finally:
        storage_mod._reset_r2_storage()


@pytest.mark.parametrize("unica", list(R2_FALSO))
def test_cualquier_variable_sola_avisa_configuracion_a_medias(settings_env, monkeypatch, unica):
    avisos: list[str] = []
    monkeypatch.setattr(storage_mod, "_partial_config_warned", False)
    monkeypatch.setattr(storage_mod.logger, "warning", lambda evento, **_: avisos.append(evento))
    settings_env(**{**dict.fromkeys(R2_FALSO, ""), unica: R2_FALSO[unica]})
    assert get_storage() is None
    assert get_storage() is None
    assert avisos == ["storage.r2_partially_configured"]


def test_sin_ninguna_variable_no_avisa(settings_env, monkeypatch):
    avisos: list[str] = []
    monkeypatch.setattr(storage_mod, "_partial_config_warned", False)
    monkeypatch.setattr(storage_mod.logger, "warning", lambda evento, **_: avisos.append(evento))
    settings_env(**dict.fromkeys(R2_FALSO, ""))
    assert get_storage() is None
    assert avisos == []


def test_url_publica():
    s = _config(R2_PUBLIC_BASE_URL="https://logos.example.com/")
    assert s.public_url("logos/t/a.png") == "https://logos.example.com/logos/t/a.png"
    assert s.public_url(None) is None
    assert _config(R2_PUBLIC_BASE_URL="").public_url("k") is None
