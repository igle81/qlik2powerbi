"""Comprueba que un PBIP generado tiene TMDL y report.json utilizables."""

from __future__ import annotations

import json
import re
from pathlib import Path

_TIPOS = {
    "clusteredBarChart",
    "clusteredColumnChart",
    "lineChart",
    "pieChart",
    "donutChart",
    "tableEx",
    "card",
    "slicer",
    "pivotTable",
}


def validar_directorio(carpeta: Path) -> list[str]:
    errores: list[str] = []
    pbips = list(carpeta.glob("*.pbip"))
    if len(pbips) != 1:
        return [f"Se esperaba un .pbip en {carpeta} y hay {len(pbips)}."]
    try:
        pbip = json.loads(pbips[0].read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"El .pbip no es JSON válido: {exc}"]
    if pbip.get("version") != "1.0" or not pbip.get("artifacts"):
        errores.append("El .pbip no declara artifacts version 1.0.")
    nombre = pbips[0].stem
    informe = carpeta / f"{nombre}.Report"
    modelo = carpeta / f"{nombre}.SemanticModel"
    if not informe.is_dir() or not modelo.is_dir():
        errores.append("Faltan las carpetas .Report o .SemanticModel.")
        return errores
    _json_ok(informe / "definition.pbir", errores, "definition.pbir")
    try:
        pbir = json.loads((informe / "definition.pbir").read_text(encoding="utf-8"))
        ruta = pbir["datasetReference"]["byPath"]["path"]
        if nombre not in ruta or "SemanticModel" not in ruta:
            errores.append("definition.pbir no apunta al modelo semántico.")
        if pbir.get("version") != "4.0":
            errores.append("definition.pbir no está en version 4.0.")
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        errores.append(f"definition.pbir incompleto: {exc}")
    pbism = modelo / "definition.pbism"
    try:
        dato = json.loads(pbism.read_text(encoding="utf-8"))
        if dato.get("version") != "4.0":
            errores.append("definition.pbism no está en version 4.0.")
    except (OSError, json.JSONDecodeError) as exc:
        errores.append(f"definition.pbism no es válido: {exc}")
    for relativo in ("definition/database.tmdl", "definition/model.tmdl"):
        if not (modelo / relativo).is_file():
            errores.append(f"Falta {relativo}.")
    if (modelo / "definition" / "database.tmdl").is_file():
        database = (modelo / "definition" / "database.tmdl").read_text(encoding="utf-8")
        if not database.startswith("database"):
            errores.append("database.tmdl no declara el objeto database.")
        if "compatibilityLevel:" not in database:
            errores.append("database.tmdl no declara compatibilityLevel.")
    if (modelo / "definition" / "model.tmdl").is_file():
        model_tmdl = (modelo / "definition" / "model.tmdl").read_text(encoding="utf-8")
        if not model_tmdl.startswith("model "):
            errores.append("model.tmdl no declara el objeto model.")
        if "ref table " not in model_tmdl:
            errores.append("model.tmdl no referencia las tablas.")
    relaciones = modelo / "definition" / "relationships.tmdl"
    if relaciones.is_file() and "relationship " not in relaciones.read_text(encoding="utf-8"):
        errores.append("No hay relaciones en relationships.tmdl.")
    for ruta in (modelo / "definition").rglob("*.tmdl"):
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            if linea.lstrip().startswith("//"):
                errores.append(f"{ruta.name} tiene un comentario //, y TMDL no lo admite.")
                break
    tablas = _tablas(modelo)
    if not tablas:
        errores.append("El TMDL no declara tablas.")
    if not any(t["medidas"] for t in tablas.values()):
        errores.append("El TMDL no declara medidas.")
    for tabla, info in tablas.items():
        for medida, dax in info["medidas"]:
            if not _balance(_sin_comentarios(dax)):
                errores.append(f"La medida {tabla}.{medida} tiene paréntesis desequilibrados.")
        texto_m = info["m"]
        if "let" not in texto_m or not re.search(r"\bin\b", texto_m):
            errores.append(f"La consulta M de {tabla} no tiene let/in.")
        if not _balance(texto_m):
            errores.append(f"La consulta M de {tabla} tiene paréntesis desequilibrados.")
    _informe(informe, tablas, errores)
    return errores


def _informe(informe: Path, tablas: dict, errores: list[str]) -> None:
    report = informe / "definition" / "report.json"
    try:
        dato = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errores.append(f"report.json no es válido: {exc}")
        return
    if "$schema" not in dato or "settings" not in dato:
        errores.append("report.json no tiene $schema y settings.")
    paginas_meta = informe / "definition" / "pages" / "pages.json"
    try:
        meta = json.loads(paginas_meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errores.append(f"pages.json no es válido: {exc}")
        return
    orden = meta.get("pageOrder") or []
    if not orden:
        errores.append("El informe no tiene páginas.")
        return
    if meta.get("activePageName") not in orden:
        errores.append("activePageName no está en pageOrder.")
    tipos: set[str] = set()
    for nombre in orden:
        pagina = informe / "definition" / "pages" / nombre / "page.json"
        try:
            cuerpo = json.loads(pagina.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errores.append(f"page.json de {nombre} no es válido: {exc}")
            continue
        if cuerpo.get("name") != nombre or "displayName" not in cuerpo:
            errores.append(f"La página {nombre} no cuadra nombre y displayName.")
        if not isinstance(cuerpo.get("width"), int) or not isinstance(cuerpo.get("height"), int):
            errores.append(f"La página {nombre} no tiene tamaño numérico.")
        visuales = list((informe / "definition" / "pages" / nombre / "visuals").glob("*/visual.json"))
        if not visuales:
            errores.append(f"La página {nombre} no tiene gráficos.")
        for visual_path in visuales:
            try:
                visual = json.loads(visual_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errores.append(f"{visual_path.name} no es JSON: {exc}")
                continue
            tipo = (visual.get("visual") or {}).get("visualType")
            if tipo not in _TIPOS:
                errores.append(f"Tipo de gráfico no reconocido: {tipo}")
                continue
            tipos.add(tipo)
            posicion = visual.get("position") or {}
            for clave in ("x", "y", "width", "height"):
                if not isinstance(posicion.get(clave), int):
                    errores.append(f"Posición incompleta en {visual_path.parent.name}.")
                    break
            estado = ((visual.get("visual") or {}).get("query") or {}).get("queryState") or {}
            if not estado:
                errores.append(f"El gráfico {visual_path.parent.name} no tiene campos.")
            for rol, bloque in estado.items():
                for proyeccion in (bloque or {}).get("projections") or []:
                    campo = (proyeccion.get("field") or {})
                    nodo = campo.get("Column") or campo.get("Measure") or {}
                    entidad = ((nodo.get("Expression") or {}).get("SourceRef") or {}).get("Entity")
                    prop = nodo.get("Property")
                    if entidad not in tablas:
                        errores.append(f"El gráfico cita la tabla inexistente {entidad}.")
                        continue
                    nombres = tablas[entidad]["columnas"] | {m for m, _ in tablas[entidad]["medidas"]}
                    if prop not in nombres:
                        errores.append(f"{entidad}.{prop} no existe en el TMDL (rol {rol}).")
    if len(tipos) < 2:
        errores.append("El informe debería incluir más de un tipo de gráfico.")


def _tablas(modelo: Path) -> dict[str, dict]:
    salida: dict[str, dict] = {}
    for ruta in (modelo / "definition" / "tables").glob("*.tmdl"):
        texto = ruta.read_text(encoding="utf-8")
        match = re.match(r"table\s+(?:'([^']+)'|(\S+))", texto)
        if not match:
            continue
        nombre = match.group(1) or match.group(2)
        columnas = set()
        for col in re.finditer(r"(?m)^\tcolumn\s+(?:'([^']+)'|(\S+))", texto):
            columnas.add(col.group(1) or col.group(2))
        medidas = []
        for med in re.finditer(r"(?m)^\tmeasure\s+(?:'([^']*)'|(\S+))\s*=\s*(.+)$", texto):
            medidas.append((med.group(1) or med.group(2), med.group(3).strip()))
        fuente = ""
        origen = re.search(r"(?m)^\t\tsource\s*=\s*$", texto)
        if origen:
            fuente = texto[origen.end() :]
        salida[nombre] = {"columnas": columnas, "medidas": medidas, "m": fuente, "texto": texto}
    return salida


def _sin_comentarios(texto: str) -> str:
    return re.sub(r"/\*.*?\*/", "", texto, flags=re.S)


def _balance(texto: str) -> bool:
    profundidad = 0
    en_cadena = False
    i = 0
    while i < len(texto):
        ch = texto[i]
        if en_cadena:
            if ch == '"':
                if i + 1 < len(texto) and texto[i + 1] == '"':
                    i += 2
                    continue
                en_cadena = False
            i += 1
            continue
        if ch == '"':
            en_cadena = True
        elif ch == "(":
            profundidad += 1
        elif ch == ")":
            profundidad -= 1
            if profundidad < 0:
                return False
        i += 1
    return profundidad == 0


def _json_ok(ruta: Path, errores: list[str], etiqueta: str) -> None:
    try:
        json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errores.append(f"{etiqueta} no es JSON válido: {exc}")
