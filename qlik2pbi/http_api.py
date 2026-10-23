"""API en memoria para la web: el código abre una sesión y el ZIP sale sin guardarse."""

from __future__ import annotations

import json
import logging
import re
import sys
import tempfile
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs

from qlik2pbi.acceso import (
    bloqueado,
    cabecera_cookie,
    coincide,
    configurado,
    emitir,
    ip_de,
    registrar_fallo,
    valida,
)
from qlik2pbi.pagina import html_acceso, html_convertidor
from qlik2pbi.pipeline import empaquetar, generar
from qlik2pbi.script import decodificar_script

LIMITE = 4 * 1024 * 1024
_LIMITE_CODIGO = 8 * 1024
_TOPE_ERROR = 2000
_ROBOTS = b"User-agent: *\nDisallow: /\n"
_MENSAJE_GENERICO = "No se ha podido generar el proyecto. Revisa el script y el .qvf."

class _StderrActual(logging.StreamHandler):
    """Escribe en el stderr vigente. Un StreamHandler fijo se queda en el de la importación."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stderr
        super().emit(record)


log = logging.getLogger("qlik2pbi.http")
if not any(isinstance(handler, _StderrActual) for handler in log.handlers):
    _salida = _StderrActual()
    _salida.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    log.addHandler(_salida)
    log.setLevel(logging.INFO)
    log.propagate = False


def procesar_peticion(method: str, path: str, headers, body: bytes) -> tuple[int, dict[str, str], bytes]:
    return _sellar(*_despachar(method, path, headers, body or b""))


def _despachar(method: str, path: str, headers, body: bytes) -> tuple[int, dict[str, str], bytes]:
    ruta = (path or "/").split("?", 1)[0]
    ruta = ruta.rstrip("/") or "/"
    if method == "GET" and ruta == "/robots.txt":
        return 200, {"Content-Type": "text/plain; charset=utf-8"}, _ROBOTS
    if method == "GET" and ruta in {"/", "/index.html", "/api/app"}:
        return _pagina_inicial(headers)
    if method == "POST" and ruta == "/api/acceso":
        return _entrar(headers, body)
    if method == "POST" and ruta == "/api/convert":
        return _convertir(headers, body)
    return _json(405, {"error": "Usa el formulario de la página."})


def _pagina_inicial(headers) -> tuple[int, dict[str, str], bytes]:
    cfg = configurado()
    if cfg is None:
        return _html(403, html_acceso(cerrada=True))
    codigo, secreto = cfg
    if valida(_token(headers), codigo, secreto):
        return _html(200, html_convertidor())
    return _html(200, html_acceso())


def _entrar(headers, body: bytes) -> tuple[int, dict[str, str], bytes]:
    cfg = configurado()
    if cfg is None:
        return _html(403, html_acceso(cerrada=True))
    ip = _ip(headers)
    if bloqueado(ip):
        return _html(429, html_acceso("Demasiados intentos. Espera unos minutos y vuelve a probar."))
    if len(body) > _LIMITE_CODIGO:
        return _html(413, html_acceso("El código enviado es demasiado largo."))
    try:
        codigo_dado = _codigo_enviado(headers, body).strip()
    except ValueError as exc:
        return _html(400, html_acceso(str(exc)))
    codigo, secreto = cfg
    if not coincide(codigo_dado, codigo):
        registrar_fallo(ip)
        return _html(401, html_acceso("El código no es válido."))
    token = emitir(codigo, secreto)
    pagina = (
        "<!DOCTYPE html><html lang=\"es\"><head><meta charset=\"utf-8\">"
        "<meta name=\"robots\" content=\"noindex, nofollow\">"
        "<title>Entrando</title></head><body><p><a href=\"/\">Continuar</a></p></body></html>"
    )
    return 303, {
        "Content-Type": "text/html; charset=utf-8",
        "Location": "/",
        "Set-Cookie": cabecera_cookie(token, _https(headers)),
        "Cache-Control": "no-store",
    }, pagina.encode("utf-8")


def _convertir(headers, body: bytes) -> tuple[int, dict[str, str], bytes]:
    cfg = configurado()
    if cfg is None:
        return _json(401, {"error": "El acceso está cerrado."})
    codigo, secreto = cfg
    if not valida(_token(headers), codigo, secreto):
        return _json(401, {"error": "Hace falta el código de acceso."})
    if len(body) > LIMITE * 3:
        return _json(413, {"error": "La petición supera el tamaño admitido (4 MB por fichero)."})
    try:
        campos = _partes(body, _cabecera(headers, "Content-Type"))
    except ValueError as exc:
        return _json(400, {"error": str(exc)})
    _script_nombre, script_bytes = campos.get("script", ("", b""))
    export_nombre, export_bytes = campos.get("export", ("", b""))
    if not script_bytes or not export_bytes:
        return _json(400, {"error": "Hay que subir el script de carga (.qvs) y la app (.qvf)."})
    if len(script_bytes) > LIMITE or len(export_bytes) > LIMITE:
        return _json(413, {"error": "Cada fichero puede pesar como máximo 4 MB."})
    try:
        script = decodificar_script(script_bytes)
        with tempfile.TemporaryDirectory(prefix="qlik2pbi-") as tmp:
            resultado = generar(script, export_bytes, export_nombre or "app.json", Path(tmp))
            paquete = empaquetar(resultado.carpeta)
            avisos = len(resultado.notas.avisos())
            nombre = resultado.nombre
    except ValueError as exc:
        return _json(400, {"error": _texto_error(exc)})
    except RuntimeError as exc:
        _registrar_fallo(exc, script_bytes, export_bytes, export_nombre)
        return _json(400, {"error": _texto_error(exc)})
    except Exception as exc:
        _registrar_fallo(exc, script_bytes, export_bytes, export_nombre)
        return _json(500, {"error": _texto_error(exc, prefijo=True)})
    return 200, {
        "Content-Type": "application/zip",
        "Content-Disposition": f'attachment; filename="{nombre}.zip"',
        "Cache-Control": "no-store",
        "X-Avisos": str(avisos),
    }, paquete


def _registrar_fallo(_exc: BaseException, script: bytes, exportacion: bytes, nombre: str) -> None:
    log.exception(
        "Fallo al generar el proyecto (script=%s bytes, export=%s bytes, fichero=%s)",
        len(script),
        len(exportacion),
        nombre or "",
    )


def _texto_error(exc: BaseException, prefijo: bool = False) -> str:
    texto = str(exc).strip()
    if not texto:
        return _MENSAJE_GENERICO
    if prefijo:
        texto = f"No se ha podido generar el proyecto: {texto}"
    if len(texto) > _TOPE_ERROR:
        texto = texto[: _TOPE_ERROR - 1].rstrip() + "…"
    return texto


def _codigo_enviado(headers, body: bytes) -> str:
    tipo = _cabecera(headers, "Content-Type").lower()
    if "application/json" in tipo:
        try:
            dato = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("El código no llega en un JSON válido.") from exc
        if not isinstance(dato, dict):
            raise ValueError("El código no llega en un JSON válido.")
        return str(dato.get("codigo", ""))
    if "application/x-www-form-urlencoded" in tipo or not tipo:
        pares = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True, max_num_fields=5)
        return pares.get("codigo", [""])[0]
    if "multipart/form-data" in tipo:
        partes = _partes(body, tipo)
        return partes.get("codigo", ("", b""))[1].decode("utf-8", "replace")
    raise ValueError("Envía el código en el formulario.")


def _token(headers) -> str:
    for parte in _cabecera(headers, "Cookie").split(";"):
        nombre, sep, valor = parte.strip().partition("=")
        if sep and nombre == "qlik2pbi_sesion":
            return valor.strip()
    return ""


def _ip(headers) -> str:
    return ip_de(_cabecera(headers, "X-Real-IP"), _cabecera(headers, "X-Forwarded-For"))


def _https(headers) -> bool:
    return _cabecera(headers, "X-Forwarded-Proto").split(",")[0].strip().lower() == "https"


def _html(status: int, pagina: str) -> tuple[int, dict[str, str], bytes]:
    return status, {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "Content-Security-Policy": _csp(),
        "X-Frame-Options": "DENY",
    }, pagina.encode("utf-8")


def _csp() -> str:
    return (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )


def _json(status: int, dato: dict) -> tuple[int, dict[str, str], bytes]:
    cuerpo = json.dumps(dato, ensure_ascii=False).encode("utf-8")
    return status, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}, cuerpo


def _sellar(status: int, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
    salida = dict(headers)
    salida["X-Robots-Tag"] = "noindex, nofollow"
    salida["X-Content-Type-Options"] = "nosniff"
    salida.setdefault("Cache-Control", "no-store")
    return status, salida, body


def _cabecera(headers, nombre: str) -> str:
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if getter is not None:
        for candidato in (nombre, nombre.lower(), nombre.title()):
            valor = getter(candidato)
            if valor:
                return str(valor)
    try:
        pares = headers.items()
    except Exception:
        return ""
    for clave, valor in pares:
        if str(clave).lower() == nombre.lower() and valor:
            return str(valor)
    return ""


def _partes(body: bytes, content_type: str) -> dict[str, tuple[str, bytes]]:
    match = re.search(r'boundary=("?)([^";]+)\1', content_type or "")
    if not match:
        raise ValueError("El formulario tiene que enviarse como multipart.")
    frontera = match.group(2).encode("utf-8", "replace")
    salida: dict[str, tuple[str, bytes]] = {}
    for trozo in body.split(b"--" + frontera):
        if b"Content-Disposition" not in trozo:
            continue
        cabecera, _, datos = trozo.partition(b"\r\n\r\n")
        if datos.endswith(b"\r\n"):
            datos = datos[:-2]
        texto = cabecera.decode("utf-8", "replace")
        nombre = re.search(r'name="([^"]+)"', texto)
        fichero = re.search(r'filename="([^"]*)"', texto)
        if not nombre:
            continue
        salida[nombre.group(1)] = (fichero.group(1) if fichero else "", datos)
    return salida


class handler(BaseHTTPRequestHandler):
    def _responder(self) -> None:
        longitud = int(self.headers.get("Content-Length") or 0)
        cuerpo = self.rfile.read(longitud) if longitud else b""
        status, cabeceras, carga = procesar_peticion(self.command, self.path, self.headers, cuerpo)
        self.send_response(status)
        for clave, valor in cabeceras.items():
            self.send_header(clave, valor)
        self.send_header("Content-Length", str(len(carga)))
        self.end_headers()
        self.wfile.write(carga)

    def do_GET(self) -> None:  # noqa: N802
        self._responder()

    def do_POST(self) -> None:  # noqa: N802
        self._responder()

    def log_message(self, fmt: str, *args) -> None:
        return
