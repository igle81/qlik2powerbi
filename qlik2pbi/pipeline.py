"""Orquesta script, exportación, DAX, M y el PBIP."""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

from qlik2pbi.cobertura import REVISION_MANUAL, TRADUCIDAS
from qlik2pbi.dax import traducir
from qlik2pbi.exportar import Exportacion, ExpVisual, leer_exportacion
from qlik2pbi.modelo import Medida, Modelo, Pagina, Proyeccion, Visual
from qlik2pbi.notas import Notas
from qlik2pbi.pbip import escribir_pbip
from qlik2pbi.script import analizar_script
from qlik2pbi.validar import validar_directorio

_TIPOS = {
    "barchart": "clusteredBarChart",
    "bar": "clusteredBarChart",
    "barchartvertical": "clusteredColumnChart",
    "columnchart": "clusteredColumnChart",
    "column": "clusteredColumnChart",
    "combochart": "clusteredColumnChart",
    "linechart": "lineChart",
    "line": "lineChart",
    "piechart": "pieChart",
    "pie": "pieChart",
    "donut": "pieChart",
    "table": "tableEx",
    "straighttable": "tableEx",
    "pivottable": "tableEx",
    "pivot": "tableEx",
    "kpi": "card",
    "card": "card",
    "filterpane": "slicer",
    "filter": "slicer",
    "listbox": "slicer",
}


@dataclass
class Resultado:
    carpeta: Path
    proyecto: Path
    nombre: str
    notas: Notas
    modelo: Modelo


def slug(nombre: str) -> str:
    texto = unicodedata.normalize("NFKD", nombre or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^A-Za-z0-9]+", "_", texto).strip("_")
    return (texto or "Qlik")[:80]


def generar(script: str, exportacion: bytes, nombre_export: str, carpeta: Path, nombre: str | None = None) -> Resultado:
    notas = Notas()
    if not (script or "").strip():
        raise ValueError("El script de carga está vacío.")
    modelo, _ = analizar_script(script, notas)
    if not modelo.tablas:
        raise ValueError("El script no contiene ninguna tabla LOAD, SQL o INLINE.")
    datos = leer_exportacion(exportacion, nombre_export, notas)
    modelo.nombre_app = datos.nombre or "Qlik"
    resolver = _resolver(modelo)
    _medidas(modelo, datos, resolver, notas)
    _paginas(modelo, datos, resolver, notas)
    proyecto = nombre or slug(modelo.nombre_app)
    carpeta = Path(carpeta)
    if carpeta.exists() and any(carpeta.iterdir()):
        # Se escribe dentro de la carpeta pedida, sin borrar otros ficheros ajenos.
        pass
    ruta = escribir_pbip(modelo, carpeta, proyecto)
    _informe(carpeta / "INFORME.md", modelo, notas, proyecto)
    errores = validar_directorio(carpeta)
    if errores:
        raise RuntimeError("El PBIP generado no supera la validación:\n- " + "\n- ".join(errores))
    return Resultado(carpeta, ruta, proyecto, notas, modelo)


def empaquetar(carpeta: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as paquete:
        for ruta in sorted(Path(carpeta).rglob("*")):
            if ruta.is_file():
                paquete.write(ruta, ruta.relative_to(carpeta).as_posix())
    return buf.getvalue()


def _resolver(modelo: Modelo):
    indice: dict[str, list[tuple[str, str, bool]]] = {}
    for tabla in modelo.tablas:
        if tabla.mapeo:
            continue
        for columna in tabla.columnas:
            indice.setdefault(columna.nombre.lower(), []).append((tabla.nombre, columna.nombre, tabla.hecho))

    def resolver(nombre: str):
        opciones = indice.get((nombre or "").lower(), [])
        if not opciones:
            return None
        propias = [op for op in opciones if op[0] == modelo.hecho]
        if propias:
            return propias[0][0], propias[0][1]
        dimensiones = [op for op in opciones if not op[2]]
        if len(dimensiones) == 1:
            return dimensiones[0][0], dimensiones[0][1]
        return opciones[0][0], opciones[0][1]

    return resolver


def _medidas(modelo: Modelo, datos: Exportacion, resolver, notas: Notas) -> None:
    vistos: set[str] = set()
    for item in datos.medidas:
        nombre = _nombre_unico(item.nombre or "Medida", vistos)
        dax = traducir(item.expresion, resolver, notas)
        modelo.medidas.append(Medida(nombre, item.expresion, dax, modelo.hecho, _formato(nombre, dax)))
    if not modelo.medidas and modelo.tablas:
        columna = _primera_numerica(modelo)
        if columna:
            tabla, nombre_col = columna
            expr = f"Sum({nombre_col})"
            dax = traducir(expr, resolver, notas)
            modelo.medidas.append(Medida(f"Suma de {nombre_col}", expr, dax, modelo.hecho, "#,0.00"))
            notas.info("MEDIDA", "La exportación no trae medidas. Se ha creado una suma de la primera columna numérica.")


def _paginas(modelo: Modelo, datos: Exportacion, resolver, notas: Notas) -> None:
    hojas = datos.hojas or []
    if not hojas:
        hojas_visuales = [_hoja_de_respaldo(modelo, datos)]
        notas.aviso("HOJAS", "La exportación no trae hojas. Se ha compuesto una página con las medidas y los campos.")
        modelo.paginas = [_enlazar_hoja(modelo, hojas_visuales[0], 1, resolver, notas)]
        return
    for indice, hoja in enumerate(hojas, start=1):
        if not any(v.explicito for v in hoja.visuales):
            for visual in hoja.visuales:
                _rellenar(visual, datos, modelo)
        pagina = _enlazar_hoja(modelo, hoja, indice, resolver, notas)
        if not pagina.visuales:
            notas.aviso("HOJAS", f"La hoja «{hoja.titulo}» no ha producido gráficos. Se compone una página de respaldo.")
            pagina = _enlazar_hoja(modelo, _hoja_de_respaldo(modelo, datos), indice, resolver, notas)
            pagina.titulo = hoja.titulo
        modelo.paginas.append(pagina)


def _enlazar_hoja(modelo: Modelo, hoja, indice: int, resolver, notas: Notas) -> Pagina:
    visuales: list[Visual] = []
    for objeto in hoja.visuales:
        tipo = _tipo(objeto.tipo, notas)
        if tipo == "slicer":
            for dimension in objeto.dimensiones or datos_dim_fallback(modelo):
                proy = _dimension(dimension, resolver, notas)
                if proy:
                    visuales.append(Visual("slicer", objeto.titulo or proy.nombre, {"Values": [proy]}))
            continue
        dims = [p for d in objeto.dimensiones if (p := _dimension(d, resolver, notas))]
        meds = [p for m in objeto.medidas if (p := _medida_visual(modelo, m, objeto.titulo, resolver, notas))]
        if tipo == "card":
            if meds:
                visuales.append(Visual("card", objeto.titulo, {"Values": meds}))
            continue
        if tipo == "tableEx":
            valores = dims + meds
            if valores:
                visuales.append(Visual("tableEx", objeto.titulo, {"Values": valores}))
            continue
        roles: dict[str, list[Proyeccion]] = {}
        if dims:
            roles["Category"] = [dims[0]]
        if len(dims) > 1 and tipo != "pieChart":
            roles["Series"] = [dims[1]]
        if meds:
            roles["Y"] = meds
        if "Category" in roles and "Y" in roles:
            visuales.append(Visual(tipo, objeto.titulo, roles))
        elif meds and not dims:
            visuales.append(Visual("card", objeto.titulo, {"Values": meds}))
    return Pagina(f"pagina{indice:02d}", hoja.titulo or f"Página {indice}", visuales)


def datos_dim_fallback(modelo: Modelo) -> list[str]:
    for tabla in modelo.tablas:
        if tabla.mapeo or tabla.hecho:
            continue
        for columna in tabla.columnas:
            if columna.tipo == "string":
                return [columna.nombre]
    return []


def _rellenar(visual: ExpVisual, datos: Exportacion, modelo: Modelo) -> None:
    if visual.explicito:
        return
    tipo = _tipo(visual.tipo, Notas())
    if tipo == "slicer" and datos.dimensiones:
        visual.dimensiones = datos.dimensiones[:1]
    elif tipo == "card" and datos.medidas:
        visual.medidas = [datos.medidas[0].nombre]
    elif tipo == "tableEx":
        visual.dimensiones = datos.dimensiones[:3]
        visual.medidas = [m.nombre for m in datos.medidas[:2]]
    else:
        if datos.dimensiones:
            visual.dimensiones = datos.dimensiones[:1]
        if datos.medidas:
            visual.medidas = [datos.medidas[0].nombre]
    visual.explicito = bool(visual.dimensiones or visual.medidas)


def _hoja_de_respaldo(modelo: Modelo, datos: Exportacion):
    from qlik2pbi.exportar import ExpHoja

    texto = next((c.nombre for t in modelo.tablas if not t.hecho and not t.mapeo for c in t.columnas if c.tipo == "string"), "")
    medidas = [m.nombre for m in modelo.medidas[:3]] or [m.nombre for m in datos.medidas[:3]]
    objetos = []
    for medida in medidas[:3]:
        objetos.append(ExpVisual("kpi", medida, medidas=[medida], explicito=True))
    if texto and medidas:
        objetos.append(ExpVisual("barchart", f"{medidas[0]} por {texto}", dimensiones=[texto], medidas=[medidas[0]], explicito=True))
        objetos.append(ExpVisual("table", "Detalle", dimensiones=[texto], medidas=medidas[:2], explicito=True))
    if texto:
        objetos.append(ExpVisual("filterpane", texto, dimensiones=[texto], explicito=True))
    return ExpHoja("Resumen", objetos)


def _dimension(texto: str, resolver, notas: Notas) -> Proyeccion | None:
    elegido = resolver(texto)
    if not elegido:
        notas.aviso("CAMPO", f"La dimensión «{texto}» no está en el modelo. El gráfico que la usa puede quedar incompleto.")
        return None
    return Proyeccion("columna", elegido[0], elegido[1])


def _medida_visual(modelo: Modelo, texto: str, titulo: str, resolver, notas: Notas) -> Proyeccion | None:
    for medida in modelo.medidas:
        if medida.nombre.casefold() == texto.casefold():
            return Proyeccion("medida", medida.tabla, medida.nombre)
    if "(" not in texto and not any(op in texto for op in "+-*/"):
        notas.aviso("MEDIDA", f"No existe la medida «{texto}».")
        return None
    nombre = _nombre_unico(titulo or "Medida", {m.nombre.casefold() for m in modelo.medidas})
    dax = traducir(texto, resolver, notas)
    modelo.medidas.append(Medida(nombre, texto, dax, modelo.hecho, _formato(nombre, dax)))
    return Proyeccion("medida", modelo.hecho, nombre)


def _tipo(tipo: str, notas: Notas) -> str:
    clave = (tipo or "table").strip().lower()
    if clave in _TIPOS:
        if clave in {"pivot", "pivottable"}:
            notas.aviso("GRAFICO", "Una tabla dinámica se exporta como tabla plana.")
        return _TIPOS[clave]
    compacto = re.sub(r"[\s_-]+", "", clave)
    for candidato, destino in _TIPOS.items():
        if re.sub(r"[\s_-]+", "", candidato) == compacto:
            return destino
    notas.aviso("GRAFICO", f"El gráfico «{tipo}» no tiene equivalente directo. Se exporta como tabla.")
    return "tableEx"


def _formato(nombre: str, dax: str) -> str:
    if dax.startswith("DISTINCTCOUNT") or dax.startswith("COUNT("):
        return "#,0"
    return "#,0.00"


def _nombre_unico(nombre: str, vistos: set[str]) -> str:
    base = re.sub(r"\s+", " ", (nombre or "Medida").strip()) or "Medida"
    if base.casefold() not in vistos:
        vistos.add(base.casefold())
        return base
    n = 2
    while f"{base} {n}".casefold() in vistos:
        n += 1
    final = f"{base} {n}"
    vistos.add(final.casefold())
    return final


def _primera_numerica(modelo: Modelo):
    for tabla in modelo.tablas:
        if tabla.nombre != modelo.hecho:
            continue
        for columna in tabla.columnas:
            if columna.tipo in {"double", "int64"} and not columna.nombre.lower().endswith("id"):
                return tabla.nombre, columna.nombre
    return None


def _informe(ruta: Path, modelo: Modelo, notas: Notas, proyecto: str) -> None:
    lineas = [
        f"# Informe de conversión — {proyecto}",
        "",
        "Generado en local por `qlik2pbi`. No se ha enviado el script ni la exportación a ningún servicio.",
        "",
        "## Qué se ha generado",
        "",
        f"- Tablas: {len(modelo.tablas)}",
        f"- Relaciones: {len(modelo.relaciones)}",
        f"- Medidas: {len(modelo.medidas)}",
        f"- Páginas: {len(modelo.paginas)}",
        f"- Gráficos: {sum(len(p.visuales) for p in modelo.paginas)}",
        "",
        "Al abrir el PBIP en Power BI Desktop, ajusta el parámetro `CarpetaDatos` a la carpeta de los CSV si el script leía ficheros.",
        "",
        "## Avisos para revisar a mano",
        "",
    ]
    avisos = notas.avisos()
    if not avisos:
        lineas.append("No hay avisos. Aun así, abre el modelo y comprueba las relaciones.")
    else:
        for nota in avisos:
            lineas.append(f"- {nota.mensaje}")
    lineas += ["", "## Transformaciones aplicadas", ""]
    infos = notas.infos()
    if not infos:
        lineas.append("Sin notas adicionales.")
    else:
        for nota in infos:
            lineas.append(f"- {nota.mensaje}")
    lineas += ["", "## Medidas", "", "| Medida | Expresión Qlik | DAX |", "| --- | --- | --- |"]
    for medida in modelo.medidas:
        lineas.append(
            "| "
            + " | ".join(
                [
                    _md(medida.nombre),
                    _md(medida.expresion_qlik),
                    _md(medida.dax),
                ]
            )
            + " |"
        )
    lineas += ["", "## Relaciones", ""]
    if not modelo.relaciones:
        lineas.append("No se han inferido relaciones.")
    else:
        for relacion in modelo.relaciones:
            lineas.append(
                f"- `{relacion.desde_tabla}[{relacion.desde_columna}]` → "
                f"`{relacion.hasta_tabla}[{relacion.hasta_columna}]` (varios a uno)"
            )
    lineas += ["", "## Cobertura de esta herramienta", "", "### Se traduce", ""]
    for funcion, resultado, nota in TRADUCIDAS:
        extra = f" {nota}" if nota else ""
        lineas.append(f"- **{funcion}** → {resultado}.{extra}")
    lineas += ["", "### Queda como aviso", ""]
    for funcion, motivo in REVISION_MANUAL:
        lineas.append(f"- **{funcion}.** {motivo}")
    lineas.append("")
    ruta.write_text("\n".join(lineas), encoding="utf-8")


def _md(texto: str) -> str:
    return (texto or "").replace("|", "\\|").replace("\n", " ")
