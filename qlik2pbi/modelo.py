"""Modelo intermedio entre el script Qlik y el PBIP."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Columna:
    nombre: str
    tipo: str
    tipo_m: str


@dataclass
class FichaTabla:
    """De dónde sale la tabla en el script, para explicarlo en TABLAS.md."""

    clase: str = "desconocido"
    texto: str = "Origen no registrado en el script."
    ruta: str = ""
    fichero: str = ""
    servidor: str = ""
    base: str = ""
    sql: str = ""
    transformaciones: list[str] = field(default_factory=list)
    depende_de: list[str] = field(default_factory=list)
    ficheros: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class Tabla:
    nombre: str
    columnas: list[Columna]
    pasos: list[tuple[str, str]]
    oculta: bool = False
    mapeo: bool = False
    hecho: bool = False
    ficha: FichaTabla = field(default_factory=FichaTabla)

    def m(self) -> str:
        return render_let(self.pasos)


@dataclass
class Relacion:
    desde_tabla: str
    desde_columna: str
    hasta_tabla: str
    hasta_columna: str


@dataclass
class Medida:
    nombre: str
    expresion_qlik: str
    dax: str
    tabla: str
    formato: str


@dataclass
class Proyeccion:
    clase: str  # "columna" o "medida"
    tabla: str
    nombre: str


@dataclass
class Visual:
    tipo: str
    titulo: str
    roles: dict[str, list[Proyeccion]]


@dataclass
class Pagina:
    nombre: str
    titulo: str
    visuales: list[Visual]


@dataclass
class Modelo:
    tablas: list[Tabla] = field(default_factory=list)
    relaciones: list[Relacion] = field(default_factory=list)
    hecho: str = ""
    medidas: list[Medida] = field(default_factory=list)
    paginas: list[Pagina] = field(default_factory=list)
    usa_carpeta: bool = False
    nombre_app: str = "Qlik"


def render_let(pasos: list[tuple[str, str]]) -> str:
    from qlik2pbi.mexpr import m_ident

    lineas = ["let"]
    for indice, (nombre, expresion) in enumerate(pasos):
        coma = "," if indice < len(pasos) - 1 else ""
        trozos = expresion.split("\n")
        lineas.append(f"    {m_ident(nombre)} = {trozos[0]}")
        for extra in trozos[1:]:
            lineas.append(f"    {extra}" if extra.strip() else extra)
        lineas[-1] = lineas[-1] + coma
    lineas.append("in")
    lineas.append(f"    {m_ident(pasos[-1][0])}")
    return "\n".join(lineas)
