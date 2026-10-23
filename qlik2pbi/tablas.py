"""TABLAS.md: qué hay que importar y en qué orden, sacado del script."""

from __future__ import annotations

from qlik2pbi.modelo import Modelo, Tabla

_TIPOS = {
    "string": "texto",
    "int64": "número entero",
    "double": "número decimal",
    "dateTime": "fecha",
    "boolean": "booleano",
}


def documento_tablas(modelo: Modelo) -> str:
    tablas = [tabla for tabla in modelo.tablas if not tabla.nombre.startswith("_")]
    lineas = [
        "# Tablas que hay que importar",
        "",
        "Estas son las tablas del script de carga. El PBIP no incluye las filas: "
        "en Power BI Desktop hay que dejar cada origen accesible y actualizar el modelo.",
        "",
        "## Qué cambiar en Power Query",
        "",
    ]
    lineas.extend(_cambios(tablas, modelo.usa_carpeta))
    lineas += ["", "## Orden de carga sugerido", ""]
    for indice, tabla in enumerate(_orden(tablas), start=1):
        lineas.append(f"{indice}. `{tabla.nombre}` — {_papel(tabla)}")
    for tabla in tablas:
        lineas += ["", f"## {tabla.nombre}", ""]
        lineas.append(f"- Nombre en Power BI: `{tabla.nombre}`")
        lineas.append(f"- Papel: {_papel(tabla)}")
        lineas.append(f"- Origen: {tabla.ficha.texto}")
        if tabla.ficha.sql:
            lineas.append(f"- Consulta: `{tabla.ficha.sql}`")
        lineas.append("- Campos:")
        if tabla.columnas:
            for columna in tabla.columnas:
                tipo = _TIPOS.get(columna.tipo, columna.tipo)
                lineas.append(f"  - `{columna.nombre}` — {tipo}")
        else:
            lineas.append("  - El script no declara columnas.")
        claves = _claves(tabla, modelo)
        lineas.append("- Claves de relación:")
        if claves:
            for clave in claves:
                lineas.append(f"  - {clave}")
        else:
            lineas.append("  - Ninguna.")
        lineas.append("- Transformaciones:")
        if tabla.ficha.transformaciones:
            for nota in tabla.ficha.transformaciones:
                lineas.append(f"  - {nota}")
        else:
            lineas.append("  - Ninguna aparte del origen.")
    lineas.append("")
    return "\n".join(lineas)


def _papel(tabla: Tabla) -> str:
    if tabla.mapeo:
        return "tabla de mapeo (oculta en el modelo)"
    if tabla.hecho:
        return "hechos"
    return "dimensión"


def _claves(tabla: Tabla, modelo: Modelo) -> list[str]:
    salida: list[str] = []
    for relacion in modelo.relaciones:
        if relacion.desde_tabla == tabla.nombre:
            salida.append(
                f"`{relacion.desde_columna}` → `{relacion.hasta_tabla}.{relacion.hasta_columna}` (varios a uno)"
            )
        elif relacion.hasta_tabla == tabla.nombre:
            salida.append(
                f"`{relacion.hasta_columna}` ← `{relacion.desde_tabla}.{relacion.desde_columna}` (varios a uno)"
            )
    return salida


def _cambios(tablas: list[Tabla], usa_carpeta: bool) -> list[str]:
    lineas: list[str] = []
    ficheros: list[tuple[str, str, str]] = []
    vistos: set[str] = set()
    for tabla in tablas:
        propios = list(tabla.ficha.ficheros)
        if tabla.ficha.fichero and tabla.ficha.fichero not in {item[0] for item in propios}:
            propios.insert(0, (tabla.ficha.fichero, tabla.ficha.ruta, tabla.ficha.clase))
        for fichero, ruta, clase in propios:
            if not fichero or fichero in vistos:
                continue
            vistos.add(fichero)
            ficheros.append((fichero, ruta, clase))
    if usa_carpeta and ficheros:
        lineas.append(
            "- Cambia el parámetro `CarpetaDatos` (ahora vale `datos`) a la carpeta donde estén estos ficheros:"
        )
        for fichero, ruta, clase in ficheros:
            origen = f", ruta del script `{ruta}`" if ruta else ""
            aviso = " Power Query no lee el QVD: deja un CSV con este nombre." if clase == "qvd" else ""
            lineas.append(f"  - `{fichero}`{origen}.{aviso}")
    elif not ficheros:
        lineas.append("- No hay ficheros que recolocar: las tablas salen de INLINE, de SQL o de otras consultas.")
    consultas = [tabla for tabla in tablas if tabla.ficha.clase == "sql"]
    if consultas:
        for tabla in consultas:
            lineas.append(
                f"- En `{tabla.nombre}`, la consulta usa el servidor `{tabla.ficha.servidor}` "
                f"y la base `{tabla.ficha.base}`. Sustituye esos dos valores por la conexión real antes de actualizar."
            )
    if not lineas:
        lineas.append("- Revisa el origen de cada tabla en el editor de Power Query antes de actualizar.")
    return lineas


def _orden(tablas: list[Tabla]) -> list[Tabla]:
    indice = {tabla.nombre: posicion for posicion, tabla in enumerate(tablas)}
    nombres = set(indice)
    pendientes = {
        tabla.nombre: [dep for dep in tabla.ficha.depende_de if dep in nombres and dep != tabla.nombre]
        for tabla in tablas
    }
    salida: list[Tabla] = []
    vistos: set[str] = set()

    def peso(tabla: Tabla) -> int:
        if tabla.mapeo:
            return 0
        if tabla.hecho:
            return 2
        return 1

    while len(salida) < len(tablas):
        listos = [
            tabla
            for tabla in tablas
            if tabla.nombre not in vistos and all(dep in vistos for dep in pendientes[tabla.nombre])
        ]
        if not listos:
            listos = [tabla for tabla in tablas if tabla.nombre not in vistos]
        listos.sort(key=lambda tabla: (peso(tabla), indice[tabla.nombre]))
        elegido = listos[0]
        vistos.add(elegido.nombre)
        salida.append(elegido)
    return salida
