"""Lectura de un .qvf de Qlik Sense (FileFormat 12000).

El fichero no es un ZIP. Tras una cabecera de texto van registros marcados
con {"format":"gzjson"} o {"format":"binary"}. Cada registro trae dos
enteros de 32 bits en orden little-endian (tamaño declarado y tamaño
almacenado) y después la carga. En gzjson la carga es zlib y, al
descomprimir, un JSON de metadatos: script, hojas, objetos, medidas,
dimensiones y variables. Los registros binary son datos o imágenes y no
se leen.
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

MARCADOR_GZ = b'{"format":"gzjson"}'
MARCADOR_BIN = b'{"format":"binary"}'
SOBRE = "metadatos-qvf"
MENSAJE_QVF_DANADO = (
    "No se ha podido leer este .qvf. Si el fichero está dañado, como último recurso "
    "exporta la app a JSON (en Qlik Cloud, con qlik app unbuild) y conviértela desde "
    "la línea de comandos pasando ese JSON junto al .qvs."
)

_TOPE_ALMACENADO = 16 * 1024 * 1024
_TOPE_DECLARADO = 32 * 1024 * 1024
_DECORATIVOS = {
    "text",
    "text-image",
    "textimage",
    "snapshot",
    "shape",
    "image",
    "button",
    "action-button",
    "actionbutton",
    "nav",
}
_CONTENEDORES = {"filterpane", "container", "layoutcontainer"}

# El navegador usa esta función para no subir las tablas. DecompressionStream
# con formato "deflate" es zlib (RFC 1950), el mismo envoltorio que el .qvf.
JS_EXTRAER = r"""
async function extraerObjetosQvf(bytes) {
  const gz = new TextEncoder().encode('{"format":"gzjson"}');
  const objetos = [];
  let desde = 0;
  while (desde < bytes.length && objetos.length < 5000) {
    const pos = indiceDe(bytes, gz, desde);
    if (pos < 0) break;
    const leido = await leerGzjson(bytes, pos, gz.length);
    if (!leido) {
      desde = pos + 1;
      continue;
    }
    objetos.push(leido.objeto);
    desde = leido.fin;
  }
  return objetos;
}

function indiceDe(bytes, aguja, desde) {
  const ultimo = bytes.length - aguja.length;
  for (let i = desde; i <= ultimo; i++) {
    if (bytes[i] !== aguja[0]) continue;
    let ok = true;
    for (let k = 1; k < aguja.length; k++) {
      if (bytes[i + k] !== aguja[k]) { ok = false; break; }
    }
    if (ok) return i;
  }
  return -1;
}

function u32(bytes, offset) {
  return (
    bytes[offset] |
    (bytes[offset + 1] << 8) |
    (bytes[offset + 2] << 16) |
    (bytes[offset + 3] << 24)
  ) >>> 0;
}

async function leerGzjson(bytes, pos, marca) {
  const base = pos + marca;
  if (base + 9 > bytes.length || bytes[base] !== 0) return null;
  const declarado = u32(bytes, base + 1);
  const almacenado = u32(bytes, base + 5);
  const inicio = base + 9;
  if (
    declarado === 0 ||
    declarado > 33554432 ||
    almacenado === 0 ||
    almacenado > 16777216 ||
    inicio + almacenado > bytes.length
  ) {
    return null;
  }
  let plano;
  try {
    plano = await descomprimir(bytes.subarray(inicio, inicio + almacenado));
  } catch (error) {
    return null;
  }
  if (plano.length !== declarado) return null;
  let texto = new TextDecoder("utf-8", { fatal: false }).decode(plano).replace(/\u0000+$/g, "");
  let objeto = null;
  try {
    objeto = JSON.parse(texto);
  } catch (error) {
    const fin = texto.lastIndexOf("}");
    if (fin > 0) {
      try { objeto = JSON.parse(texto.slice(0, fin + 1)); } catch (otro) { objeto = null; }
    }
  }
  if (!objeto || typeof objeto !== "object" || Array.isArray(objeto)) return null;
  return { objeto: objeto, fin: inicio + almacenado };
}

async function descomprimir(chunk) {
  if (typeof DecompressionStream !== "function") {
    throw new Error("sin DecompressionStream");
  }
  const flujo = new Blob([chunk]).stream().pipeThrough(new DecompressionStream("deflate"));
  return new Uint8Array(await new Response(flujo).arrayBuffer());
}
"""


def parece_qvf(data: bytes) -> bool:
    if not data or data[:1] in (b"{", b"[", b"<") or data[:2] == b"PK":
        return False
    if MARCADOR_GZ in data:
        return True
    return b'"FileFormat"' in data[:4_000_000]


def es_sobre(data: bytes) -> bool:
    if not data:
        return False
    cabecera = data.lstrip()[:1]
    if cabecera not in (b"{", b"\xef"):
        return False
    return b'"qlik2pbi"' in data[:160] and b"metadatos-qvf" in data[:240]


def extraer_objetos(data: bytes) -> list[dict]:
    objetos: list[dict] = []
    desde = 0
    while desde < len(data) and len(objetos) < 5000:
        pos = data.find(MARCADOR_GZ, desde)
        if pos < 0:
            break
        leido = _leer_gzjson(data, pos)
        if leido is None:
            desde = pos + 1
            continue
        objetos.append(leido[0])
        desde = leido[1]
    if objetos:
        return objetos
    if b'"FileFormat"' in data[:4_000_000]:
        return _objetos_por_zlib(data)
    return []


def leer_qvf(data: bytes, notas, nombre_fichero: str = ""):
    objetos = extraer_objetos(data)
    if not objetos:
        return None
    return mapear(objetos, notas, nombre_fichero)


def leer_sobre(data: bytes, notas, nombre_fichero: str = ""):
    try:
        dato = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(MENSAJE_QVF_DANADO) from exc
    if not isinstance(dato, dict) or dato.get("qlik2pbi") != SOBRE or not isinstance(dato.get("objetos"), list):
        raise ValueError(MENSAJE_QVF_DANADO)
    fichero = str(dato.get("fichero") or nombre_fichero or "")
    if not dato["objetos"]:
        raise ValueError(MENSAJE_QVF_DANADO)
    return mapear(dato["objetos"], notas, fichero)


def mapear(objetos: list, notas, nombre_fichero: str = ""):
    from qlik2pbi.exportar import Exportacion, ExpHoja, ExpMedida, ExpVariable, ExpVisual

    exportacion = Exportacion(nombre=_nombre_inicial(nombre_fichero))
    medidas_por_id: dict[str, ExpMedida] = {}
    dimensiones_por_id: dict[str, str] = {}
    hojas_brutas: list[dict] = []
    for objeto in objetos:
        if not isinstance(objeto, dict):
            notas.aviso(
                "QVF",
                "Hay objetos del .qvf que no son un diccionario. Se omiten y se sigue con el resto.",
            )
            continue
        try:
            script = _script_de(objeto)
            if script and not exportacion.script:
                exportacion.script = script
            titulo = objeto.get("qTitle")
            if isinstance(titulo, str) and titulo.strip() and "qLastReloadTime" in objeto:
                exportacion.nombre = titulo.strip()
            elif isinstance(titulo, str) and titulo.strip() and exportacion.nombre in {"", "Qlik"}:
                exportacion.nombre = titulo.strip()
            for variable in _variables(objeto):
                if any(ya.nombre == variable.nombre for ya in exportacion.variables):
                    continue
                exportacion.variables.append(variable)
            medida = _medida(objeto)
            if medida:
                ident, item = medida
                medidas_por_id[ident or item.nombre] = item
                continue
            dimension = _dimension(objeto)
            if dimension:
                ident, campo = dimension
                if ident:
                    dimensiones_por_id[ident] = campo
                if campo and campo not in exportacion.dimensiones:
                    exportacion.dimensiones.append(campo)
                continue
            if _es_hoja(objeto):
                hojas_brutas.append(objeto)
        except Exception as exc:
            notas.aviso(
                "QVF",
                f"No se ha podido leer un objeto del .qvf ({exc}). Se sigue con el resto.",
            )
    for item in medidas_por_id.values():
        exportacion.medidas.append(item)
    biblioteca = _Biblioteca(medidas_por_id, dimensiones_por_id)
    for objeto in hojas_brutas:
        try:
            hoja = _hoja(objeto, biblioteca, ExpHoja, ExpVisual)
        except Exception as exc:
            notas.aviso(
                "QVF",
                f"No se ha podido leer un objeto del .qvf ({exc}). Se sigue con el resto.",
            )
            continue
        if hoja.visuales or hoja.titulo:
            exportacion.hojas.append(hoja)
    if exportacion.variables:
        nombres = ", ".join(variable.nombre for variable in exportacion.variables[:40])
        resto = len(exportacion.variables) - 40
        cola = f" y {resto} más" if resto > 0 else ""
        notas.info(
            "VARIABLE",
            f"El .qvf declara {len(exportacion.variables)} variables ({nombres}{cola}). "
            "Las expresiones $(variable) quedan para revisar a mano.",
        )
    notas.info(
        "QVF",
        "Se han leído las hojas, los objetos y las medidas del .qvf. Los datos de las tablas no se copian.",
    )
    return exportacion


class _Biblioteca:
    def __init__(self, medidas: dict[str, object], dimensiones: dict[str, str]) -> None:
        self.medidas = medidas
        self.dimensiones = dimensiones


def _leer_gzjson(data: bytes, pos: int) -> tuple[dict, int] | None:
    base = pos + len(MARCADOR_GZ)
    if base + 9 > len(data) or data[base:base + 1] != b"\x00":
        return None
    declarado = int.from_bytes(data[base + 1:base + 5], "little")
    almacenado = int.from_bytes(data[base + 5:base + 9], "little")
    inicio = base + 9
    if (
        declarado <= 0
        or declarado > _TOPE_DECLARADO
        or almacenado <= 0
        or almacenado > _TOPE_ALMACENADO
        or inicio + almacenado > len(data)
    ):
        return None
    try:
        plano = zlib.decompress(data[inicio:inicio + almacenado])
    except zlib.error:
        return None
    if len(plano) != declarado:
        return None
    objeto = _json_objeto(plano)
    if objeto is None:
        return None
    return objeto, inicio + almacenado


def _json_objeto(plano: bytes) -> dict | None:
    texto = plano.rstrip(b"\x00")
    try:
        decodificado = texto.decode("utf-8-sig")
    except UnicodeDecodeError:
        decodificado = texto.decode("utf-8", "replace")
    try:
        valor, _fin = json.JSONDecoder().raw_decode(decodificado)
    except json.JSONDecodeError:
        return None
    if not isinstance(valor, dict):
        return None
    return valor


def _objetos_por_zlib(data: bytes) -> list[dict]:
    """Respaldo si un .qvf trae la cabecera de Qlik pero no los marcadores gzjson."""
    firmas = (b"\x78\x9c", b"\x78\x01", b"\x78\xda", b"\x78\x5e")
    objetos: list[dict] = []
    pos = 0
    intentos = 0
    limite = min(len(data), 12_000_000)
    while pos < limite - 2 and len(objetos) < 500 and intentos < 4000:
        if data[pos:pos + 2] not in firmas:
            pos += 1
            continue
        intentos += 1
        entrada = data[pos:pos + 4_000_000]
        descompresor = zlib.decompressobj()
        try:
            plano = descompresor.decompress(entrada) + descompresor.flush()
        except zlib.error:
            pos += 1
            continue
        usado = len(entrada) - len(descompresor.unused_data)
        if usado <= 0 or len(plano) > _TOPE_DECLARADO:
            pos += 1
            continue
        objeto = _json_objeto(plano)
        if objeto is not None and _parece_metadato(objeto):
            objetos.append(objeto)
        pos += usado
    return objetos


def _parece_metadato(objeto: dict) -> bool:
    return any(clave in objeto for clave in ("qScript", "qTitle", "qInfo", "qMetaData", "qMeasure", "qDim", "qRoot", "qEntryList"))


def _nombre_inicial(nombre_fichero: str) -> str:
    nombre = Path(nombre_fichero or "").name
    bajo = nombre.lower()
    if bajo.endswith(".metadatos.json"):
        nombre = nombre[: -len(".metadatos.json")]
        bajo = nombre.lower()
    if bajo.endswith(".qvf"):
        nombre = nombre[:-4]
    elif bajo.endswith(".json"):
        nombre = nombre[:-5]
    nombre = nombre.strip()
    return nombre or "Qlik"


def _script_de(objeto: dict) -> str:
    valor = objeto.get("qScript")
    if isinstance(valor, str) and valor.strip():
        return valor
    if isinstance(valor, list):
        partes: list[str] = []
        for item in valor:
            if isinstance(item, str) and item.strip():
                partes.append(item)
            elif isinstance(item, dict):
                texto = item.get("qScript")
                if isinstance(texto, str) and texto.strip():
                    partes.append(texto)
        return "\n".join(partes)
    return ""


def _cuerpo_maestro(objeto: dict) -> dict:
    for candidato in (objeto, objeto.get("qProperty") if isinstance(objeto.get("qProperty"), dict) else None):
        if not isinstance(candidato, dict):
            continue
        info = candidato.get("qInfo") if isinstance(candidato.get("qInfo"), dict) else {}
        if info.get("qType") in {"measure", "dimension"}:
            return candidato
    raiz = objeto.get("qRoot")
    if isinstance(raiz, dict) and isinstance(raiz.get("qProperty"), dict):
        info = raiz["qProperty"].get("qInfo") if isinstance(raiz["qProperty"].get("qInfo"), dict) else {}
        if info.get("qType") in {"measure", "dimension"}:
            return raiz["qProperty"]
    return objeto


def _medida(objeto: dict):
    from qlik2pbi.exportar import ExpMedida

    cuerpo = _cuerpo_maestro(objeto)
    info = cuerpo.get("qInfo") if isinstance(cuerpo.get("qInfo"), dict) else {}
    if info.get("qType") != "measure":
        return None
    medida = cuerpo.get("qMeasure") if isinstance(cuerpo.get("qMeasure"), dict) else {}
    expresion = medida.get("qDef")
    if not isinstance(expresion, str) or not expresion.strip():
        return None
    meta = cuerpo.get("qMetaDef") if isinstance(cuerpo.get("qMetaDef"), dict) else {}
    nombre = meta.get("title") or medida.get("qLabel") or expresion
    if not isinstance(nombre, str) or not nombre.strip():
        nombre = expresion
    ident = str(info.get("qId") or "")
    return ident, ExpMedida(nombre.strip(), expresion.strip())


def _dimension(objeto: dict) -> tuple[str, str] | None:
    cuerpo = _cuerpo_maestro(objeto)
    info = cuerpo.get("qInfo") if isinstance(cuerpo.get("qInfo"), dict) else {}
    if info.get("qType") != "dimension":
        return None
    qdim = cuerpo.get("qDim") if isinstance(cuerpo.get("qDim"), dict) else {}
    campo = _primer_campo(qdim.get("qFieldDefs"))
    if not campo:
        return None
    return str(info.get("qId") or ""), campo


def _variables(objeto: dict):
    from qlik2pbi.exportar import ExpVariable

    entradas = objeto.get("qEntryList")
    if not isinstance(entradas, list) or not entradas:
        return []
    ident = str(objeto.get("qId") or "")
    parece = ident in {"qvapp_variablelist", "user_variablelist"}
    if not parece:
        primera = entradas[0] if isinstance(entradas[0], dict) else {}
        props = primera.get("qProperties") if isinstance(primera.get("qProperties"), dict) else {}
        info = props.get("qInfo") if isinstance(props.get("qInfo"), dict) else {}
        parece = info.get("qType") == "variable"
    if not parece:
        return []
    salida = []
    vistos: set[str] = set()
    for entrada in entradas:
        if not isinstance(entrada, dict):
            continue
        props = entrada.get("qProperties") if isinstance(entrada.get("qProperties"), dict) else {}
        nombre = props.get("qName") or entrada.get("qName")
        definicion = props.get("qDefinition")
        if definicion is None:
            definicion = entrada.get("qDefinition")
        if not isinstance(nombre, str) or not nombre.strip():
            continue
        if nombre in vistos:
            continue
        vistos.add(nombre)
        texto = definicion if isinstance(definicion, str) else ""
        salida.append(ExpVariable(nombre.strip(), texto.strip()))
    return salida


def _es_hoja(objeto: dict) -> bool:
    meta = objeto.get("qMetaData") if isinstance(objeto.get("qMetaData"), dict) else {}
    if meta.get("qType") == "sheet":
        return True
    raiz = objeto.get("qRoot") if isinstance(objeto.get("qRoot"), dict) else None
    if not raiz:
        return False
    prop = raiz.get("qProperty") if isinstance(raiz.get("qProperty"), dict) else {}
    info = prop.get("qInfo") if isinstance(prop.get("qInfo"), dict) else {}
    return info.get("qType") == "sheet"


def _hoja(objeto: dict, biblioteca: _Biblioteca, cls_hoja, cls_visual):
    raiz = objeto.get("qRoot") if isinstance(objeto.get("qRoot"), dict) else objeto
    prop = raiz.get("qProperty") if isinstance(raiz.get("qProperty"), dict) else {}
    titulo = _titulo(prop, "Hoja")
    visuales = []
    hijos = raiz.get("qChildren") if isinstance(raiz.get("qChildren"), list) else []
    for hijo in hijos:
        for propiedades in _visuales(hijo):
            visual = _visual(propiedades, biblioteca, cls_visual, len(visuales) + 1)
            if visual:
                visuales.append(visual)
    return cls_hoja(titulo, visuales)


def _visuales(nodo: dict):
    if not isinstance(nodo, dict):
        return
    prop = nodo.get("qProperty") if isinstance(nodo.get("qProperty"), dict) else nodo
    tipo = _tipo_qlik(prop)
    hijos = nodo.get("qChildren") if isinstance(nodo.get("qChildren"), list) else []
    if tipo in _CONTENEDORES and hijos:
        for hijo in hijos:
            yield from _visuales(hijo)
        return
    if tipo and tipo not in _DECORATIVOS and tipo not in {"sheet", "story", "loadmodel", "appprops", "masterobject"}:
        yield prop
        return
    for hijo in hijos:
        yield from _visuales(hijo)


def _visual(prop: dict, biblioteca: _Biblioteca, cls_visual, indice: int):
    tipo = _tipo_qlik(prop) or "table"
    if tipo in _DECORATIVOS:
        return None
    titulo = _titulo(prop, f"{tipo} {indice}")
    dimensiones = _dimensiones_visual(prop, biblioteca)
    medidas = _medidas_visual(prop, biblioteca)
    if not titulo:
        titulo = tipo
    return cls_visual(tipo, titulo, dimensiones, medidas, explicito=True)


def _dimensiones_visual(prop: dict, biblioteca: _Biblioteca) -> list[str]:
    salida: list[str] = []
    cubo = _cubo(prop)
    if isinstance(cubo, dict):
        for item in _lista(cubo.get("qDimensions")):
            campo = _dimension_de_item(item, biblioteca)
            if campo and campo not in salida:
                salida.append(campo)
    lista = prop.get("qListObjectDef") if isinstance(prop.get("qListObjectDef"), dict) else None
    if isinstance(lista, dict):
        campo = _dimension_de_item(lista, biblioteca)
        if campo and campo not in salida:
            salida.append(campo)
    return salida


def _medidas_visual(prop: dict, biblioteca: _Biblioteca) -> list[str]:
    salida: list[str] = []
    cubo = _cubo(prop)
    if not isinstance(cubo, dict):
        return salida
    for item in _lista(cubo.get("qMeasures")):
        texto = _medida_de_item(item, biblioteca)
        if texto and texto not in salida:
            salida.append(texto)
    return salida


def _dimension_de_item(item: dict, biblioteca: _Biblioteca) -> str:
    if not isinstance(item, dict):
        return ""
    ident = item.get("qLibraryId")
    if isinstance(ident, str) and ident in biblioteca.dimensiones:
        return biblioteca.dimensiones[ident]
    qdef = item.get("qDef") if isinstance(item.get("qDef"), dict) else {}
    campo = _primer_campo(qdef.get("qFieldDefs")) or _primer_campo(item.get("qFieldDefs"))
    if campo:
        return campo
    for clave in ("qLabel", "title", "qFieldLabels"):
        valor = qdef.get(clave)
        if isinstance(valor, str) and valor.strip():
            return _campo(valor)
        if isinstance(valor, list):
            campo = _primer_campo(valor)
            if campo:
                return campo
    return ""


def _medida_de_item(item: dict, biblioteca: _Biblioteca) -> str:
    if not isinstance(item, dict):
        return ""
    ident = item.get("qLibraryId")
    if isinstance(ident, str) and ident in biblioteca.medidas:
        return biblioteca.medidas[ident].nombre
    qdef = item.get("qDef") if isinstance(item.get("qDef"), dict) else {}
    expresion = qdef.get("qDef")
    if isinstance(expresion, str) and expresion.strip():
        return expresion.strip()
    etiqueta = qdef.get("qLabel") or item.get("qLabel")
    if isinstance(etiqueta, str) and etiqueta.strip():
        return etiqueta.strip()
    return ""


def _cubo(prop: dict) -> dict | None:
    for clave in ("qHyperCubeDef", "qHyperCube"):
        if isinstance(prop.get(clave), dict):
            return prop[clave]
    return None


def _tipo_qlik(prop: dict) -> str:
    if not isinstance(prop, dict):
        return ""
    visual = prop.get("visualization")
    if isinstance(visual, str) and visual.strip():
        return visual.strip().lower()
    info = prop.get("qInfo") if isinstance(prop.get("qInfo"), dict) else {}
    tipo = info.get("qType")
    if isinstance(tipo, str):
        return tipo.strip().lower()
    return ""


def _titulo(prop: dict, respaldo: str) -> str:
    for candidato in (prop.get("title"), (prop.get("qMetaDef") or {}).get("title") if isinstance(prop.get("qMetaDef"), dict) else None):
        if isinstance(candidato, str) and candidato.strip():
            return candidato.strip()[:120]
    return respaldo


def _primer_campo(valores) -> str:
    if isinstance(valores, str):
        return _campo(valores)
    if not isinstance(valores, list):
        return ""
    for valor in valores:
        if isinstance(valor, str) and valor.strip():
            return _campo(valor)
    return ""


def _campo(texto: str) -> str:
    limpio = texto.strip()
    if limpio.startswith("="):
        limpio = limpio[1:].strip()
    if len(limpio) >= 2 and limpio[0] == "[" and limpio[-1] == "]":
        limpio = limpio[1:-1]
    return limpio.strip()


def _lista(valor) -> list:
    if isinstance(valor, list):
        return valor
    if valor is None:
        return []
    return [valor]
