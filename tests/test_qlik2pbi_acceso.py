"""La web pide código. La CLI no."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from qlik2pbi.acceso import DURACION, emitir, reiniciar_intentos, valida
from qlik2pbi.cli import main
from qlik2pbi.http_api import procesar_peticion

CODIGO = "codigo-de-prueba"
SECRETO = "secreto-de-sesion-de-prueba"


@pytest.fixture
def entorno(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ACCESS_CODE", CODIGO)
    monkeypatch.setenv("SESSION_SECRET", SECRETO)
    reiniciar_intentos()
    yield
    reiniciar_intentos()


def test_sin_configuracion_no_entra_nadie(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ACCESS_CODE", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    estado, cabeceras, cuerpo = procesar_peticion("GET", "/", {}, b"")
    texto = cuerpo.decode("utf-8")
    assert estado == 403
    assert "El acceso está cerrado." in texto
    assert 'type="file"' not in texto
    assert cabeceras["X-Robots-Tag"] == "noindex, nofollow"
    conversion = procesar_peticion("POST", "/api/convert", {"Content-Type": "text/plain"}, b"datos")
    assert conversion[0] == 401
    assert "cerrado" in conversion[2].decode("utf-8")


def test_falta_uno_de_los_dos_secretos(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ACCESS_CODE", CODIGO)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    assert procesar_peticion("GET", "/", {}, b"")[0] == 403
    monkeypatch.setenv("SESSION_SECRET", SECRETO)
    monkeypatch.setenv("ACCESS_CODE", "   ")
    assert procesar_peticion("GET", "/", {}, b"")[0] == 403


def test_codigo_incorrecto_no_abre_el_formulario(entorno):
    estado, cabeceras, cuerpo = procesar_peticion(
        "POST",
        "/api/acceso",
        {"Content-Type": "application/x-www-form-urlencoded", "X-Real-IP": "203.0.113.8"},
        "codigo=otro".encode(),
    )
    assert estado == 401
    assert "Set-Cookie" not in cabeceras
    assert 'type="file"' not in cuerpo.decode("utf-8")
    assert "El código no es válido." in cuerpo.decode("utf-8")
    pagina = procesar_peticion("GET", "/", {}, b"")
    assert pagina[0] == 200
    assert 'type="password"' in pagina[2].decode("utf-8")
    assert 'type="file"' not in pagina[2].decode("utf-8")


def test_codigo_correcto_deja_cookie_de_12_horas(entorno):
    estado, cabeceras, _ = procesar_peticion(
        "POST",
        "/api/acceso",
        {
            "Content-Type": "application/json",
            "X-Forwarded-Proto": "https",
            "X-Real-IP": "203.0.113.9",
        },
        b'{"codigo":"codigo-de-prueba"}',
    )
    assert estado == 303
    assert cabeceras["Location"] == "/"
    cookie = cabeceras["Set-Cookie"]
    assert cookie.startswith("qlik2pbi_sesion=")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Max-Age=43200" in cookie
    assert "Secure" in cookie
    assert CODIGO not in cookie
    assert SECRETO not in cookie
    token = cookie.split(";", 1)[0].split("=", 1)[1]
    assert valida(token, CODIGO, SECRETO)


def test_en_http_local_la_cookie_no_lleva_secure(entorno):
    _, cabeceras, _ = procesar_peticion(
        "POST",
        "/api/acceso",
        {"Content-Type": "application/x-www-form-urlencoded", "X-Real-IP": "203.0.113.10"},
        f"codigo={CODIGO}".encode(),
    )
    assert "Secure" not in cabeceras["Set-Cookie"]


def test_cookie_manipulada_o_caducada_no_convierte(entorno):
    token = emitir(CODIGO, SECRETO)
    trozos = token.split(".")
    trozos[2] = ("0" if trozos[2][0] != "0" else "1") + trozos[2][1:]
    manipulada = "qlik2pbi_sesion=" + ".".join(trozos)
    estado, _, _ = procesar_peticion(
        "POST",
        "/api/convert",
        {"Cookie": manipulada, "Content-Type": "text/plain"},
        b"",
    )
    assert estado == 401
    caducada = emitir(CODIGO, SECRETO, ahora=time.time() - DURACION - 10)
    assert valida(caducada, CODIGO, SECRETO) is False
    otro_codigo = emitir(CODIGO, SECRETO)
    assert valida(otro_codigo, "otro-codigo", SECRETO) is False


def test_limite_de_intentos(entorno):
    cabeceras = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Real-IP": "203.0.113.50",
    }
    for _ in range(5):
        estado, _, _ = procesar_peticion("POST", "/api/acceso", cabeceras, b"codigo=mal")
        assert estado == 401
    estado, _, cuerpo = procesar_peticion("POST", "/api/acceso", cabeceras, f"codigo={CODIGO}".encode())
    assert estado == 429
    assert "Demasiados intentos" in cuerpo.decode("utf-8")
    otra = dict(cabeceras)
    otra["X-Real-IP"] = "203.0.113.51"
    estado, _, _ = procesar_peticion("POST", "/api/acceso", otra, f"codigo={CODIGO}".encode())
    assert estado == 303


def test_cli_no_pide_codigo(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.delenv("ACCESS_CODE", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    codigo = main(
        [
            "--script",
            str(Path(__file__).resolve().parents[1] / "ejemplos" / "ventas" / "carga.qvs"),
            "--export",
            str(Path(__file__).resolve().parents[1] / "ejemplos" / "ventas" / "app.json"),
            "--out",
            str(tmp_path),
        ]
    )
    assert codigo == 0


def test_robots_bloquea_el_sitio():
    estado, cabeceras, cuerpo = procesar_peticion("GET", "/robots.txt", {}, b"")
    assert estado == 200
    assert b"Disallow: /" in cuerpo
    assert cabeceras["X-Robots-Tag"] == "noindex, nofollow"
