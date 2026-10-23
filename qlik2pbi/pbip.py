"""Escritura de un proyecto PBIP (TMDL + informe PBIR)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from qlik2pbi.modelo import Modelo, Pagina, Proyeccion, Tabla, Visual

_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "qlik2pbi.local")

_ESQUEMA_PBIP = "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json"
_ESQUEMA_PBISM = "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json"
_ESQUEMA_PBIR = "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json"
_ESQUEMA_PLATAFORMA = "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json"
_ESQUEMA_REPORT = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.1.0/schema.json"
_ESQUEMA_PAGINAS = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.0.0/schema.json"
_ESQUEMA_PAGINA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.0.0/schema.json"
_ESQUEMA_VISUAL = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.5.0/schema.json"
_ESQUEMA_VERSION = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json"

_ENTEROS_NO_SUMA = {"anio", "año", "ano", "mes", "year", "month", "day", "dia", "día"}


def ident(texto: str) -> str:
    return str(uuid.uuid5(_NS, texto))


def _json(ruta: Path, dato: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(dato, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _tmdl_nombre(nombre: str) -> str:
    if nombre and __import__("re").fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", nombre):
        return nombre
    return "'" + nombre.replace("'", "''") + "'"


def _literal(texto: str) -> str:
    return "'" + texto.replace("'", "''") + "'"


def escribir_pbip(modelo: Modelo, carpeta: Path, nombre: str) -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    _json(
        carpeta / f"{nombre}.pbip",
        {
            "$schema": _ESQUEMA_PBIP,
            "version": "1.0",
            "artifacts": [{"report": {"path": f"{nombre}.Report"}}],
            "settings": {"enableAutoRecovery": True},
        },
    )
    (carpeta / ".gitignore").write_text("**/.pbi/localSettings.json\n**/.pbi/cache.abf\n", encoding="utf-8")
    _modelo(modelo, carpeta / f"{nombre}.SemanticModel", nombre)
    _informe(modelo, carpeta / f"{nombre}.Report", nombre)
    return carpeta / f"{nombre}.pbip"


def _modelo(modelo: Modelo, raiz: Path, nombre: str) -> None:
    _plataforma(raiz / ".platform", "SemanticModel", nombre)
    _json(
        raiz / "definition.pbism",
        {"$schema": _ESQUEMA_PBISM, "version": "4.0", "settings": {}},
    )
    _json(
        raiz / "diagramLayout.json",
        {
            "version": "1.1.0",
            "diagrams": [
                {
                    "ordinal": 0,
                    "scrollPosition": {"x": 0, "y": 0},
                    "nodes": [],
                    "name": "Todas las tablas",
                    "zoomValue": 100,
                    "pinKeyFieldsToTop": False,
                    "showExtraHeaderInfo": False,
                    "hideKeyFieldsWhenCollapsed": False,
                    "tablesLocked": False,
                }
            ],
            "selectedDiagram": "Todas las tablas",
            "defaultDiagram": "Todas las tablas",
        },
    )
    definicion = raiz / "definition"
    (definicion / "database.tmdl").parent.mkdir(parents=True, exist_ok=True)
    (definicion / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1600\n", encoding="utf-8")
    (definicion / "model.tmdl").write_text(_model_tmdl(modelo), encoding="utf-8")
    ruta_rel = definicion / "relationships.tmdl"
    if modelo.relaciones:
        ruta_rel.write_text(_relaciones_tmdl(modelo), encoding="utf-8")
    elif ruta_rel.exists():
        ruta_rel.unlink()
    if modelo.usa_carpeta:
        (definicion / "expressions.tmdl").write_text(
            'expression CarpetaDatos = "datos" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=false]\n',
            encoding="utf-8",
        )
    for tabla in modelo.tablas:
        destino = definicion / "tables" / f"{_fichero(tabla.nombre)}.tmdl"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(_tabla_tmdl(tabla, modelo), encoding="utf-8")


def _model_tmdl(modelo: Modelo) -> str:
    orden = json.dumps([t.nombre for t in modelo.tablas], ensure_ascii=False)
    lineas = [
        "model Model",
        "\tculture: es-ES",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: es-ES",
        "\tdataAccessOptions",
        "\t\tlegacyRedirects",
        "\t\treturnErrorValuesAsNull",
        "",
        f"annotation PBI_QueryOrder = {orden}",
        "",
    ]
    for tabla in modelo.tablas:
        lineas.append(f"ref table {_tmdl_nombre(tabla.nombre)}")
    lineas.append("")
    if modelo.usa_carpeta:
        lineas.append("ref expression CarpetaDatos")
        lineas.append("")
    return "\n".join(lineas)


def _relaciones_tmdl(modelo: Modelo) -> str:
    bloques: list[str] = []
    for relacion in modelo.relaciones:
        rid = ident(
            f"rel:{relacion.desde_tabla}.{relacion.desde_columna}>{relacion.hasta_tabla}.{relacion.hasta_columna}"
        )
        desde = f"{_tmdl_nombre(relacion.desde_tabla)}.{_tmdl_nombre(relacion.desde_columna)}"
        hasta = f"{_tmdl_nombre(relacion.hasta_tabla)}.{_tmdl_nombre(relacion.hasta_columna)}"
        bloques.append(
            "\n".join(
                [
                    f"relationship {rid}",
                    f"\tfromColumn: {desde}",
                    f"\ttoColumn: {hasta}",
                    "\tcrossFilteringBehavior: oneDirection",
                    "",
                ]
            )
        )
    return "\n".join(bloques)


def _tabla_tmdl(tabla: Tabla, modelo: Modelo) -> str:
    lineas = [f"table {_tmdl_nombre(tabla.nombre)}"]
    if tabla.oculta:
        lineas.append("\tisHidden")
    lineas.append(f"\tlineageTag: {ident('tabla:' + tabla.nombre)}")
    lineas.append("")
    if tabla.nombre == modelo.hecho:
        for medida in modelo.medidas:
            lineas.append(f"\tmeasure {_tmdl_nombre(medida.nombre)} = {medida.dax}")
            lineas.append(f"\t\tformatString: {medida.formato}")
            lineas.append("\t\tdisplayFolder: Medidas")
            lineas.append(f"\t\tlineageTag: {ident('medida:' + medida.nombre)}")
            lineas.append("")
    for columna in tabla.columnas:
        lineas.append(f"\tcolumn {_tmdl_nombre(columna.nombre)}")
        lineas.append(f"\t\tdataType: {columna.tipo}")
        lineas.append(f"\t\tlineageTag: {ident('col:' + tabla.nombre + '.' + columna.nombre)}")
        lineas.append(f"\t\tsummarizeBy: {_resumen(columna.nombre, columna.tipo)}")
        lineas.append(f"\t\tsourceColumn: {columna.nombre}")
        lineas.append("")
        lineas.append("\t\tannotation SummarizationSetBy = Automatic")
        lineas.append("")
    m = "\n".join("\t\t\t" + linea if linea else "" for linea in tabla.m().split("\n"))
    lineas.append(f"\tpartition {_tmdl_nombre(tabla.nombre)} = m")
    lineas.append("\t\tmode: import")
    lineas.append("\t\tsource =")
    lineas.append(m)
    lineas.append("")
    lineas.append("\tannotation PBI_ResultType = Table")
    lineas.append("")
    return "\n".join(lineas)


def _resumen(nombre: str, tipo: str) -> str:
    if tipo in {"string", "dateTime", "boolean"}:
        return "none"
    if nombre.lower().endswith("id") or nombre.lower() in _ENTEROS_NO_SUMA:
        return "none"
    return "sum"


def _informe(modelo: Modelo, raiz: Path, nombre: str) -> None:
    _plataforma(raiz / ".platform", "Report", nombre)
    _json(
        raiz / "definition.pbir",
        {
            "$schema": _ESQUEMA_PBIR,
            "version": "4.0",
            "datasetReference": {"byPath": {"path": f"../{nombre}.SemanticModel"}},
        },
    )
    definicion = raiz / "definition"
    _json(
        definicion / "version.json",
        {"$schema": _ESQUEMA_VERSION, "version": "2.0.0"},
    )
    _json(
        definicion / "report.json",
        {
            "$schema": _ESQUEMA_REPORT,
            "themeCollection": {
                "baseTheme": {
                    "name": "CY24SU06",
                    "reportVersionAtImport": {"visual": "1.8.50", "report": "2.0.50", "page": "1.3.50"},
                    "type": "SharedResources",
                }
            },
            "resourcePackages": [
                {
                    "name": "SharedResources",
                    "type": "SharedResources",
                    "items": [{"name": "CY24SU06", "path": "BaseThemes/CY24SU06.json", "type": "BaseTheme"}],
                }
            ],
            "settings": {
                "useStylableVisualContainerHeader": True,
                "exportDataMode": "AllowSummarized",
                "defaultDrillFilterOtherVisuals": True,
                "allowChangeFilterTypes": True,
                "useEnhancedTooltips": True,
            },
        },
    )
    paginas = modelo.paginas or [_pagina_vacia()]
    orden = [p.nombre for p in paginas]
    _json(
        definicion / "pages" / "pages.json",
        {
            "$schema": _ESQUEMA_PAGINAS,
            "pageOrder": orden,
            "activePageName": orden[0],
        },
    )
    for pagina in paginas:
        colocar(pagina)
        base = definicion / "pages" / pagina.nombre
        alto = 720
        for visual in pagina.visuales:
            caja = getattr(visual, "caja", (0, 0, 100, 100))
            alto = max(alto, int(caja[1] + caja[3] + 24))
        _json(
            base / "page.json",
            {
                "$schema": _ESQUEMA_PAGINA,
                "name": pagina.nombre,
                "displayName": pagina.titulo,
                "displayOption": "FitToPage",
                "height": alto,
                "width": 1280,
            },
        )
        for indice, visual in enumerate(pagina.visuales):
            vid = ident(f"visual:{pagina.nombre}:{indice}:{visual.titulo}:{visual.tipo}")
            carpeta = vid.replace("-", "")[:20]
            x, y, ancho, alto_v = getattr(visual, "caja", (16, 16, 400, 280))
            _json(base / "visuals" / carpeta / "visual.json", _visual_json(visual, carpeta, x, y, ancho, alto_v, indice))


def _pagina_vacia() -> Pagina:
    return Pagina("pagina01", "Informe", [])


def colocar(pagina: Pagina) -> None:
    tarjetas = [v for v in pagina.visuales if v.tipo == "card"]
    filtros = [v for v in pagina.visuales if v.tipo == "slicer"]
    graficos = [v for v in pagina.visuales if v.tipo not in {"card", "slicer"}]
    reserva = 216 if filtros else 0
    ancho_util = 1280 - 32 - reserva
    y = 16
    if tarjetas:
        hueco = 12
        ancho = int((ancho_util - hueco * (len(tarjetas) - 1)) / len(tarjetas))
        for indice, visual in enumerate(tarjetas):
            visual.caja = (16 + indice * (ancho + hueco), y, ancho, 120)
        y += 136
    if graficos:
        columnas = 2 if len(graficos) > 1 else 1
        hueco = 12
        ancho = int((ancho_util - hueco * (columnas - 1)) / columnas)
        alto = 280
        for indice, visual in enumerate(graficos):
            col = indice % columnas
            fila = indice // columnas
            visual.caja = (16 + col * (ancho + hueco), y + fila * (alto + hueco), ancho, alto)
        filas = (len(graficos) + columnas - 1) // columnas
        y += filas * (alto + hueco)
    if filtros:
        alto_filtro = max(140, int((max(y, 720) - 16 - 8 * (len(filtros) - 1)) / len(filtros)))
        for indice, visual in enumerate(filtros):
            visual.caja = (1064, 16 + indice * (alto_filtro + 8), 200, alto_filtro)


def _visual_json(visual: Visual, nombre: str, x: int, y: int, ancho: int, alto: int, indice: int) -> dict:
    estado: dict = {}
    for rol, proyecciones in visual.roles.items():
        estado[rol] = {"projections": [_proyeccion(p) for p in proyecciones]}
    return {
        "$schema": _ESQUEMA_VISUAL,
        "name": nombre,
        "position": {
            "x": int(x),
            "y": int(y),
            "z": indice * 1000,
            "height": int(alto),
            "width": int(ancho),
            "tabOrder": indice * 1000,
        },
        "visual": {
            "visualType": visual.tipo,
            "drillFilterOtherVisuals": True,
            "query": {"queryState": estado},
            "objects": {
                "title": [
                    {
                        "properties": {
                            "text": {"expr": {"Literal": {"Value": _literal(visual.titulo)}}}
                        }
                    }
                ]
            },
        },
    }


def _proyeccion(item: Proyeccion) -> dict:
    clase = "Measure" if item.clase == "medida" else "Column"
    return {
        "field": {
            clase: {
                "Expression": {"SourceRef": {"Entity": item.tabla}},
                "Property": item.nombre,
            }
        },
        "queryRef": f"{item.tabla}.{item.nombre}",
        "active": True,
    }


def _plataforma(ruta: Path, tipo: str, nombre: str) -> None:
    _json(
        ruta,
        {
            "$schema": _ESQUEMA_PLATAFORMA,
            "metadata": {"type": tipo, "displayName": nombre},
            "config": {"version": "2.0", "logicalId": ident(f"plataforma:{tipo}:{nombre}")},
        },
    )


def _fichero(nombre: str) -> str:
    import re

    limpio = re.sub(r"[^\w.-]+", "_", nombre, flags=re.UNICODE).strip("._")
    return limpio or "Tabla"
