"""Lectura de la app Qlik: un .qvf de Qlik Sense, o un JSON/XML si ese .qvf está dañado."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree

from qlik2pbi.notas import Notas
from qlik2pbi.qvf import MENSAJE_QVF_DANADO, es_sobre, leer_qvf, leer_sobre, parece_qvf


@dataclass
class ExpMedida:
    nombre: str
    expresion: str


@dataclass
class ExpVisual:
    tipo: str
    titulo: str
    dimensiones: list[str] = field(default_factory=list)
    medidas: list[str] = field(default_factory=list)
    explicito: bool = False


@dataclass
class ExpVariable:
    nombre: str
    definicion: str


@dataclass
class ExpHoja:
    titulo: str
    visuales: list[ExpVisual] = field(default_factory=list)


@dataclass
class Exportacion:
    nombre: str = "Qlik"
    medidas: list[ExpMedida] = field(default_factory=list)
    dimensiones: list[str] = field(default_factory=list)
    hojas: list[ExpHoja] = field(default_factory=list)
    variables: list[ExpVariable] = field(default_factory=list)
    script: str = ""


def leer_exportacion(data: bytes, nombre_fichero: str, notas: Notas) -> Exportacion:
    bruto = data or b""
    nombre = (nombre_fichero or "").lower()
    if es_sobre(bruto):
        return leer_sobre(bruto, notas, nombre_fichero)
    if parece_qvf(bruto):
        leido = leer_qvf(bruto, notas, nombre_fichero)
        if leido is None:
            raise ValueError(MENSAJE_QVF_DANADO)
        return leido
    if nombre.endswith(".qvf"):
        if bruto[:2] == b"PK":
            extraido = _json_de_zip(bruto)
            if extraido is None:
                raise ValueError(MENSAJE_QVF_DANADO)
            notas.info("QVF", "Se ha leído el JSON de hojas que iba dentro del .qvf.")
            bruto = extraido
        else:
            texto = _decodificar(bruto)
            if texto.lstrip()[:1] in "{[<":
                return _desde_texto(texto)
            raise ValueError(MENSAJE_QVF_DANADO)
    elif bruto[:2] == b"PK":
        extraido = _json_de_zip(bruto)
        if extraido is None:
            raise ValueError(MENSAJE_QVF_DANADO)
        bruto = extraido
    return _desde_texto(_decodificar(bruto))


def _decodificar(bruto: bytes) -> str:
    try:
        return bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        return bruto.decode("cp1252", errors="replace")


def _desde_texto(texto: str) -> Exportacion:
    if not texto.strip():
        raise ValueError("La exportación está vacía.")
    if texto.lstrip().startswith("<"):
        return _desde_xml(texto)
    try:
        dato = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ValueError(f"La exportación no es JSON válido ({exc.msg}).") from exc
    if not isinstance(dato, dict):
        raise ValueError("La exportación JSON tiene que ser un objeto con hojas y medidas.")
    return _desde_json(dato)


def _json_de_zip(data: bytes) -> bytes | None:
    try:
        paquete = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    candidatos: list[tuple[int, bytes]] = []
    with paquete:
        for info in paquete.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".json"):
                continue
            contenido = paquete.read(info)
            try:
                dato = json.loads(contenido.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(dato, dict) and any(k in dato for k in ("sheets", "measures", "qSheets", "properties")):
                peso = (2 if "sheets" in dato or "qSheets" in dato else 0) + (1 if "measures" in dato else 0)
                candidatos.append((peso, contenido))
    if not candidatos:
        return None
    candidatos.sort(key=lambda par: par[0], reverse=True)
    return candidatos[0][1]


def _desde_json(dato: dict) -> Exportacion:
    meta = dato.get("metadata") if isinstance(dato.get("metadata"), dict) else {}
    propiedades = dato.get("properties") if isinstance(dato.get("properties"), dict) else {}
    nombre = (
        dato.get("app")
        or dato.get("name")
        or dato.get("qTitle")
        or meta.get("name")
        or "Qlik"
    )
    exportacion = Exportacion(nombre=str(nombre))
    for item in _lista(dato.get("measures")):
        medida = _medida(item)
        if medida:
            exportacion.medidas.append(medida)
    for item in _lista(propiedades.get("qMeasureList", {}).get("qItems") if isinstance(propiedades.get("qMeasureList"), dict) else None):
        medida = _medida_motor(item)
        if medida:
            exportacion.medidas.append(medida)
    for item in _lista(dato.get("dimensions")):
        dimension = _dimension(item)
        if dimension:
            exportacion.dimensiones.append(dimension)
    lista_dim = propiedades.get("qDimensionList")
    if isinstance(lista_dim, dict):
        for item in _lista(lista_dim.get("qItems")):
            dimension = _dimension_motor(item)
            if dimension:
                exportacion.dimensiones.append(dimension)
    hojas = dato.get("sheets") or dato.get("qSheets") or []
    for hoja in _lista(hojas):
        if isinstance(hoja, dict):
            exportacion.hojas.append(_hoja(hoja))
    return exportacion


def _desde_xml(texto: str) -> Exportacion:
    try:
        raiz = ElementTree.fromstring(texto)
    except ElementTree.ParseError as exc:
        raise ValueError(f"El XML de la exportación no es válido ({exc}).") from exc
    nombre = raiz.attrib.get("name") or raiz.attrib.get("title") or "Qlik"
    exportacion = Exportacion(nombre=nombre)
    for nodo in raiz.iter():
        if _tag(nodo) == "measure":
            expresion = (nodo.text or nodo.attrib.get("expression") or "").strip()
            titulo = nodo.attrib.get("name") or nodo.attrib.get("title") or expresion or "Medida"
            if expresion or titulo:
                exportacion.medidas.append(ExpMedida(titulo, expresion or titulo))
        elif _tag(nodo) == "dimension" and not list(nodo):
            texto_nodo = (nodo.text or nodo.attrib.get("field") or "").strip()
            if texto_nodo:
                exportacion.dimensiones.append(texto_nodo)
    for hoja in _hijos(raiz, "sheet"):
        visuales: list[ExpVisual] = []
        for objeto in _hijos(hoja, "object"):
            dims = [(n.text or "").strip() for n in _hijos(objeto, "dimension")]
            meds = []
            for nodo in _hijos(objeto, "measure"):
                expresion = (nodo.text or "").strip()
                meds.append(nodo.attrib.get("name") or expresion)
            visuales.append(
                ExpVisual(
                    tipo=objeto.attrib.get("type") or "table",
                    titulo=objeto.attrib.get("title") or objeto.attrib.get("name") or "Objeto",
                    dimensiones=[d for d in dims if d],
                    medidas=[m for m in meds if m],
                    explicito=True,
                )
            )
        exportacion.hojas.append(
            ExpHoja(hoja.attrib.get("name") or hoja.attrib.get("title") or "Hoja", visuales)
        )
    return exportacion


def _hoja(hoja: dict) -> ExpHoja:
    meta = {}
    propiedad = hoja.get("qProperty")
    if isinstance(propiedad, dict) and isinstance(propiedad.get("qMetaDef"), dict):
        meta = propiedad["qMetaDef"]
    titulo = str(hoja.get("name") or hoja.get("title") or meta.get("title") or "Hoja")
    objetos: list = []
    for clave in ("objects", "visualizations", "cells", "items"):
        if isinstance(hoja.get(clave), list):
            objetos = hoja[clave]
            break
    visuales: list[ExpVisual] = []
    for indice, objeto in enumerate(objetos, start=1):
        if not isinstance(objeto, dict):
            continue
        dimensiones = [_texto_dimension(d) for d in _recoger(objeto, ("dimensions", "dimension", "dims"))]
        medidas = [_texto_medida(m) for m in _recoger(objeto, ("measures", "measure", "expressions"))]
        dimensiones = [d for d in dimensiones if d]
        medidas = [m for m in medidas if m]
        visuales.append(
            ExpVisual(
                tipo=str(objeto.get("type") or objeto.get("visualization") or objeto.get("qType") or "table"),
                titulo=str(objeto.get("title") or objeto.get("label") or objeto.get("name") or f"Objeto {indice}"),
                dimensiones=dimensiones,
                medidas=medidas,
                explicito=bool(dimensiones or medidas),
            )
        )
    return ExpHoja(titulo, visuales)


def _recoger(objeto: dict, claves: tuple[str, ...]) -> list:
    salida: list = []
    for clave in claves:
        if clave in objeto:
            salida.extend(_lista(objeto.get(clave)))
    cubo = objeto.get("qHyperCube")
    if not isinstance(cubo, dict):
        propiedades = objeto.get("properties")
        cubo = propiedades.get("qHyperCube") if isinstance(propiedades, dict) else None
    if isinstance(cubo, dict):
        if "dimension" in claves or "dimensions" in claves:
            salida.extend(_lista(cubo.get("qDimensions")))
        if "measure" in claves or "measures" in claves:
            salida.extend(_lista(cubo.get("qMeasures")))
    return salida


def _texto_dimension(item) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""
    if item.get("field"):
        return str(item["field"]).strip()
    defs = item.get("qFieldDefs")
    if isinstance(defs, list) and defs:
        return str(defs[0])
    qdim = item.get("qDef")
    if isinstance(qdim, dict):
        defs = qdim.get("qFieldDefs")
        if isinstance(defs, list) and defs:
            return str(defs[0])
    for clave in ("expression", "name", "title", "label"):
        if item.get(clave):
            return str(item[clave]).strip()
    return ""


def _texto_medida(item) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""
    if item.get("name") and not item.get("expression"):
        return str(item["name"]).strip()
    for clave in ("expression", "def", "qDef"):
        valor = item.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
        if isinstance(valor, dict) and valor.get("qDef"):
            return str(valor["qDef"]).strip()
    qdef = item.get("qDef")
    if isinstance(qdef, dict):
        definicion = qdef.get("qDef") or qdef.get("qLabel")
        if definicion:
            return str(definicion).strip()
    if item.get("name"):
        return str(item["name"]).strip()
    return ""


def _medida(item) -> ExpMedida | None:
    if isinstance(item, str):
        return ExpMedida(item, item)
    if not isinstance(item, dict):
        return None
    expresion = str(item.get("expression") or item.get("def") or "").strip()
    nombre = str(item.get("name") or item.get("label") or item.get("title") or expresion or "Medida").strip()
    if not expresion:
        expresion = nombre
    return ExpMedida(nombre, expresion)


def _medida_motor(item) -> ExpMedida | None:
    if not isinstance(item, dict):
        return None
    meta = item.get("qMeta") if isinstance(item.get("qMeta"), dict) else {}
    medida = item.get("qMeasure") if isinstance(item.get("qMeasure"), dict) else {}
    expresion = str(medida.get("qDef") or "").strip()
    nombre = str(meta.get("title") or expresion or "Medida").strip()
    if not expresion:
        return None
    return ExpMedida(nombre, expresion)


def _dimension(item) -> str:
    return _texto_dimension(item)


def _dimension_motor(item) -> str:
    if not isinstance(item, dict):
        return ""
    qdim = item.get("qDim") if isinstance(item.get("qDim"), dict) else {}
    defs = qdim.get("qFieldDefs")
    if isinstance(defs, list) and defs:
        return str(defs[0])
    return ""


def _lista(valor) -> list:
    if valor is None:
        return []
    if isinstance(valor, list):
        return valor
    return [valor]


def _tag(nodo: ElementTree.Element) -> str:
    return nodo.tag.split("}")[-1].lower()


def _hijos(nodo: ElementTree.Element, nombre: str) -> list[ElementTree.Element]:
    return [hijo for hijo in list(nodo) if _tag(hijo) == nombre]
