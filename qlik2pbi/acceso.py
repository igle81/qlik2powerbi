"""Código de acceso de la web. La CLI no usa este módulo.

ACCESS_CODE y SESSION_SECRET salen del entorno. No hay valor por defecto
y ninguno de los dos se escribe en el repositorio. La cookie solo lleva
una caducidad y un HMAC: el código no viaja en ella.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time

COOKIE = "qlik2pbi_sesion"
DURACION = 12 * 60 * 60
VENTANA = 15 * 60
MAX_FALLOS = 5
_MAX_CODIGO = 256

_candado = threading.Lock()
_fallos: dict[str, list[float]] = {}


def configurado() -> tuple[str, str] | None:
    codigo = os.environ.get("ACCESS_CODE", "").strip()
    secreto = os.environ.get("SESSION_SECRET", "").strip()
    if not codigo or not secreto:
        return None
    return codigo, secreto


def coincide(dado: str, esperado: str) -> bool:
    """Compara en tiempo constante, también si las longitudes difieren."""
    if len(dado) > _MAX_CODIGO:
        dado = ""
    izquierda = hashlib.sha256(dado.encode("utf-8")).digest()
    derecha = hashlib.sha256(esperado.encode("utf-8")).digest()
    return hmac.compare_digest(izquierda, derecha)


def emitir(codigo: str, secreto: str, ahora: float | None = None) -> str:
    expira = int((ahora if ahora is not None else time.time()) + DURACION)
    cuerpo = f"{expira}.{_huella(codigo, secreto)}"
    firma = _firma(secreto, cuerpo)
    return f"{cuerpo}.{firma}"


def valida(token: str, codigo: str, secreto: str, ahora: float | None = None) -> bool:
    expira_s, huella, firma = _partes(token)
    cuerpo = f"{expira_s}.{huella}"
    firma_ok = hmac.compare_digest(_firma(secreto, cuerpo), _firma_presentada(secreto, firma))
    huella_ok = hmac.compare_digest(_huella(codigo, secreto), _huella_presentada(huella))
    momento = ahora if ahora is not None else time.time()
    try:
        vigente = expira_s.isdigit() and int(expira_s) > momento
    except ValueError:
        vigente = False
    return bool(firma_ok and huella_ok and vigente)


def cabecera_cookie(token: str, segura: bool) -> str:
    trozos = [
        f"{COOKIE}={token}",
        "HttpOnly",
        "Path=/",
        "SameSite=Lax",
        f"Max-Age={DURACION}",
    ]
    if segura:
        trozos.append("Secure")
    return "; ".join(trozos)


def bloqueado(ip: str, ahora: float | None = None) -> bool:
    with _candado:
        return len(_vigentes(ip, ahora)) >= MAX_FALLOS


def registrar_fallo(ip: str, ahora: float | None = None) -> None:
    momento = ahora if ahora is not None else time.time()
    with _candado:
        marcas = _vigentes(ip, momento)
        marcas.append(momento)
        _fallos[ip] = marcas


def reiniciar_intentos() -> None:
    with _candado:
        _fallos.clear()


def ip_de(cabecera_real: str, cabecera_reenviada: str) -> str:
    real = (cabecera_real or "").strip()
    if real:
        return real[:80]
    saltos = [trozo.strip() for trozo in (cabecera_reenviada or "").split(",") if trozo.strip()]
    if saltos:
        return saltos[-1][:80]
    return "local"


def _vigentes(ip: str, ahora: float | None) -> list[float]:
    momento = ahora if ahora is not None else time.time()
    return [marca for marca in _fallos.get(ip, []) if momento - marca < VENTANA]


def _huella(codigo: str, secreto: str) -> str:
    mac = hmac.new(secreto.encode("utf-8"), b"codigo:" + codigo.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()[:32]


def _firma(secreto: str, cuerpo: str) -> str:
    return hmac.new(secreto.encode("utf-8"), cuerpo.encode("ascii", "replace"), hashlib.sha256).hexdigest()


def _partes(token: str) -> tuple[str, str, str]:
    partes = (token or "").split(".")
    if len(partes) != 3:
        return "", "", ""
    return partes[0], partes[1], partes[2]


def _firma_presentada(secreto: str, firma: str) -> str:
    if len(firma) == 64 and all(c in "0123456789abcdef" for c in firma):
        return firma
    return _firma(secreto, "invalida")


def _huella_presentada(huella: str) -> str:
    if len(huella) == 32 and all(c in "0123456789abcdef" for c in huella):
        return huella
    return "0" * 32
