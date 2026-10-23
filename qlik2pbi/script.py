"""Parser del script de carga Qlik y construcción de consultas M."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from qlik2pbi.mexpr import a_m, m_ident, m_texto, referencias
from qlik2pbi.modelo import Columna, Modelo, Relacion, Tabla, render_let
from qlik2pbi.notas import Notas

_HECHOS = {
    "importe",
    "cantidad",
    "amount",
    "revenue",
    "quantity",
    "qty",
    "totalamount",
    "netamount",
    "salesamount",
    "neto",
    "unidades",
}
_ENTEROS = {"anio", "año", "ano", "mes", "year", "month", "day", "dia", "día", "cantidad", "quantity", "qty"}
_DECIMALES = ("importe", "precio", "amount", "price", "revenue", "total", "neto", "coste", "cost")


@dataclass
class Campo:
    alias: str
    expr: str
    clase: str  # columna, calc, applymap, estrella, agregado
    origen: str = ""
    mapa: str = ""
    clave_mapa: str = ""
    defecto: str = ""
    agregado: str = ""
    campo_agregado: str = ""


@dataclass
class Carga:
    etiqueta: str
    campos: list[Campo]
    fuente: str  # csv, xlsx, qvd, inline, resident, sql, ninguna
    ruta: str = ""
    delimitador: str = ","
    codificacion: int = 65001
    hoja: str = "Sheet1"
    residente: str = ""
    donde: str = ""
    agrupar: list[str] = field(default_factory=list)
    inline_cabeceras: list[str] = field(default_factory=list)
    inline_filas: list[list[str]] = field(default_factory=list)
    sql: str = ""
    mapeo: bool = False
    unir: tuple[str, str] | None = None  # (tipo, tabla)
    concatenar: str | None = None
    servidor: str = "SERVIDOR"
    base: str = "BASEDATOS"


def decodificar_script(data: bytes) -> str:
    """Lee un .qvs tal como lo guarda Qlik: UTF-8, UTF-16 con BOM o Windows-1252."""
    bruto = data or b""
    texto: str | None = None
    if bruto.startswith(b"\xff\xfe") or bruto.startswith(b"\xfe\xff"):
        texto = _decodificar(bruto, "utf-16")
    elif bruto.startswith(b"\xef\xbb\xbf"):
        texto = _decodificar(bruto, "utf-8-sig")
    elif _parece_utf16(bruto, impar=False):
        texto = _decodificar(bruto, "utf-16-le")
    elif _parece_utf16(bruto, impar=True):
        texto = _decodificar(bruto, "utf-16-be")
    if texto is None:
        texto = _decodificar(bruto, "utf-8")
    if texto is None:
        texto = bruto.decode("cp1252")
    return texto.replace("\x00", "").lstrip("\ufeff")


def _decodificar(data: bytes, encoding: str) -> str | None:
    try:
        return data.decode(encoding)
    except UnicodeError:
        return None


def _parece_utf16(data: bytes, impar: bool) -> bool:
    if len(data) < 8 or len(data) % 2:
        return False
    muestra = data[:64]
    pares = len(muestra) // 2
    nulos = sum(1 for i in range(1 if not impar else 0, len(muestra), 2) if muestra[i] == 0)
    return nulos >= max(4, pares // 2)


def quitar_comentarios(texto: str) -> str:
    salida: list[str] = []
    i = 0
    n = len(texto)
    en_cadena = None
    corchetes = 0
    while i < n:
        if en_cadena:
            salida.append(texto[i])
            if texto[i] == en_cadena:
                if en_cadena == "'" and i + 1 < n and texto[i + 1] == "'":
                    salida.append(texto[i + 1])
                    i += 2
                    continue
                en_cadena = None
            i += 1
            continue
        if texto[i] in "'\"":
            en_cadena = texto[i]
            salida.append(texto[i])
            i += 1
            continue
        if texto[i] == "[":
            corchetes += 1
        elif texto[i] == "]" and corchetes:
            corchetes -= 1
        if corchetes == 0 and texto.startswith("//", i):
            while i < n and texto[i] not in "\r\n":
                i += 1
            continue
        if corchetes == 0 and texto.startswith("/*", i):
            fin = texto.find("*/", i + 2)
            i = n if fin < 0 else fin + 2
            salida.append(" ")
            continue
        salida.append(texto[i])
        i += 1
    return "".join(salida)


def partir_sentencias(texto: str) -> list[str]:
    partes: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(texto)
    paren = corchete = llave = 0
    en_cadena = None
    while i < n:
        ch = texto[i]
        if en_cadena:
            buf.append(ch)
            if ch == en_cadena:
                if en_cadena == "'" and i + 1 < n and texto[i + 1] == "'":
                    buf.append(texto[i + 1])
                    i += 2
                    continue
                en_cadena = None
            i += 1
            continue
        if ch in "'\"":
            en_cadena = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren = max(0, paren - 1)
        elif ch == "[":
            corchete += 1
        elif ch == "]":
            corchete = max(0, corchete - 1)
        elif ch == "{":
            llave += 1
        elif ch == "}":
            llave = max(0, llave - 1)
        elif ch == ";" and paren == corchete == llave == 0:
            partes.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    cola = "".join(buf).strip()
    if cola:
        partes.append(cola)
    return [p for p in partes if p]


def _comas(texto: str) -> list[str]:
    partes: list[str] = []
    buf: list[str] = []
    paren = corchete = 0
    en_cadena = None
    for i, ch in enumerate(texto):
        if en_cadena:
            buf.append(ch)
            if ch == en_cadena:
                en_cadena = None
            continue
        if ch in "'\"":
            en_cadena = ch
            buf.append(ch)
            continue
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren = max(0, paren - 1)
        elif ch == "[":
            corchete += 1
        elif ch == "]":
            corchete = max(0, corchete - 1)
        elif ch == "," and paren == corchete == 0:
            partes.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    cola = "".join(buf).strip()
    if cola:
        partes.append(cola)
    return [p for p in partes if p]


def _sin_corchetes(texto: str) -> str:
    texto = texto.strip()
    if texto.startswith("[") and texto.endswith("]") and texto.count("[") == 1:
        texto = texto[1:-1].strip()
    if len(texto) >= 2 and texto[0] == texto[-1] == '"':
        texto = texto[1:-1]
    return texto


def _partir_as(campo: str) -> tuple[str, str]:
    profundidad = 0
    en_cadena = None
    ultimo = None
    i = 0
    while i < len(campo):
        ch = campo[i]
        if en_cadena:
            if ch == en_cadena:
                en_cadena = None
            i += 1
            continue
        if ch in "'\"":
            en_cadena = ch
            i += 1
            continue
        if ch == "(":
            profundidad += 1
        elif ch == ")":
            profundidad = max(0, profundidad - 1)
        elif profundidad == 0 and campo[i : i + 4].lower() == " as ":
            ultimo = i
        i += 1
    if ultimo is None:
        nombre = _sin_corchetes(campo.strip())
        return campo.strip(), nombre
    expr = campo[:ultimo].strip()
    alias = _sin_corchetes(campo[ultimo + 4 :].strip())
    return expr, alias or "Campo"


def _es_campo_simple(expr: str) -> bool:
    limpio = _sin_corchetes(expr.strip())
    return bool(re.fullmatch(r"[A-Za-z_\u00C0-\u024F][\w\u00C0-\u024F]*", limpio)) and "(" not in expr


def _parse_applymap(expr: str) -> tuple[str, str, str] | None:
    if not re.match(r"(?i)applymap\s*\(", expr.strip()):
        return None
    dentro = expr.strip()[expr.strip().lower().find("(") + 1 :]
    if dentro.endswith(")"):
        dentro = dentro[:-1]
    args = _comas(dentro)
    if len(args) < 2:
        return None
    mapa = args[0].strip().strip("'\"")
    clave = args[1].strip()
    defecto = args[2].strip() if len(args) > 2 else ""
    return mapa, clave, defecto


def _parse_agregado(expr: str) -> tuple[str, str] | None:
    match = re.fullmatch(
        r"(?i)(sum|avg|count|min|max)\s*\(\s*(?:distinct\s+)?(.+)\s*\)",
        expr.strip(),
    )
    if not match:
        return None
    return match.group(1).lower(), _sin_corchetes(match.group(2).strip())


def _campos_de_lista(lista: str) -> list[Campo]:
    if not lista.strip() or lista.strip() == "*":
        return [Campo(alias="*", expr="*", clase="estrella")]
    campos: list[Campo] = []
    for trozo in _comas(lista):
        if trozo.strip() == "*":
            campos.append(Campo(alias="*", expr="*", clase="estrella"))
            continue
        expr, alias = _partir_as(trozo)
        apply = _parse_applymap(expr)
        if apply:
            mapa, clave, defecto = apply
            campos.append(
                Campo(
                    alias=alias,
                    expr=expr,
                    clase="applymap",
                    mapa=mapa,
                    clave_mapa=_sin_corchetes(clave),
                    defecto=defecto,
                )
            )
            continue
        agregado = _parse_agregado(expr)
        if agregado and not _es_campo_simple(expr):
            funcion, origen = agregado
            campos.append(
                Campo(
                    alias=alias,
                    expr=expr,
                    clase="agregado",
                    agregado=funcion,
                    campo_agregado=origen,
                )
            )
            continue
        if _es_campo_simple(expr):
            campos.append(Campo(alias=alias, expr=expr, clase="columna", origen=_sin_corchetes(expr)))
            continue
        campos.append(Campo(alias=alias, expr=expr, clase="calc"))
    return campos


def _especificar(spec: str) -> tuple[str, str, int, str]:
    clase = "csv"
    delimitador = ","
    codificacion = 65001
    hoja = "Sheet1"
    if re.search(r"(?i)ooxml|biff|xlsx", spec):
        clase = "xlsx"
    if re.search(r"(?i)\bqvd\b", spec):
        clase = "qvd"
    match = re.search(r"(?i)delimiter is '(.)'", spec)
    if match:
        delimitador = match.group(1)
    elif re.search(r'(?i)delimiter is ";"', spec) or re.search(r"(?i)delimiter is ';'", spec):
        delimitador = ";"
    if re.search(r"(?i)codepage is 1252|ansi", spec):
        codificacion = 1252
    hoja_m = re.search(r"(?i)table is\s+([^,)]+)", spec)
    if hoja_m:
        hoja = _sin_corchetes(hoja_m.group(1).strip())
    return clase, delimitador, codificacion, hoja


def _nombre_fichero(ruta: str) -> str:
    ruta = ruta.strip().strip("[]").replace("\\", "/")
    return ruta.split("/")[-1] or "datos.csv"


def _buscar_fuente(texto: str) -> tuple[str, int]:
    """Devuelve la palabra clave de origen y su posición, a profundidad 0."""
    claves = ("INLINE", "RESIDENT", "FROM", "AUTOGENERATE")
    i = 0
    paren = corchete = 0
    en_cadena = None
    while i < len(texto):
        ch = texto[i]
        if en_cadena:
            if ch == en_cadena:
                en_cadena = None
            i += 1
            continue
        if ch in "'\"":
            en_cadena = ch
            i += 1
            continue
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren = max(0, paren - 1)
        elif ch == "[":
            corchete += 1
        elif ch == "]":
            corchete = max(0, corchete - 1)
        elif paren == 0 and corchete == 0:
            for clave in claves:
                if texto[i : i + len(clave)].upper() == clave and (
                    i == 0 or not texto[i - 1].isalnum()
                ):
                    despues = i + len(clave)
                    if despues >= len(texto) or not texto[despues].isalnum():
                        return clave, i
        i += 1
    return "", -1


def _parse_inline(bloque: str) -> tuple[list[str], list[list[str]]]:
    lineas = [ln.strip().rstrip(",") for ln in bloque.splitlines() if ln.strip()]
    if not lineas:
        return [], []
    cabeceras = _comas(lineas[0])
    filas = [_comas(ln) for ln in lineas[1:]]
    return cabeceras, filas


def _parse_sql_columnas(sql: str) -> list[str]:
    match = re.search(r"(?is)\bselect\b(.*?)\bfrom\b", sql)
    if not match:
        return []
    lista = match.group(1).strip()
    if lista.strip() == "*":
        return ["*"]
    nombres: list[str] = []
    for trozo in _comas(lista):
        trozo = re.sub(r"(?i)\s+as\s+", " as ", trozo)
        if re.search(r"(?i)\sas\s", trozo):
            nombres.append(_sin_corchetes(re.split(r"(?i)\sas\s", trozo)[-1].strip()))
        else:
            token = trozo.strip().split()[-1]
            token = token.split(".")[-1]
            nombres.append(_sin_corchetes(token))
    return nombres


def _tipo_por_nombre(nombre: str) -> tuple[str, str]:
    clave = nombre.lower()
    if clave.endswith("id") or clave in _ENTEROS:
        return "int64", "Int64.Type"
    if any(token in clave for token in ("fecha", "date", "time")):
        return "dateTime", "type date"
    if any(token in clave for token in _DECIMALES):
        return "double", "type number"
    return "string", "type text"


def _tipo_expresion(campo: Campo) -> tuple[str, str]:
    if campo.clase == "applymap":
        return "string", "type text"
    expr = campo.expr.strip()
    if re.match(r"(?i)year\s*\(|month\s*\(|day\s*\(", expr):
        return "int64", "Int64.Type"
    if re.match(r"(?i)if\s*\(", expr) and re.search(r"'[^']*'|\"[^\"]*\"", expr):
        # Si las ramas son texto, el resultado es texto. Una comparación con texto
        # también contiene comillas: miramos si hay un literal fuera de la condición
        # de forma aproximada contando literales. Con 2 o más, lo tratamos como texto.
        if len(re.findall(r"'[^']*'|\"[^\"]*\"", expr)) >= 2:
            return "string", "type text"
    if re.search(r"[*/]", expr):
        return "double", "type number"
    if campo.clase == "columna":
        return _tipo_por_nombre(campo.origen or campo.alias)
    return _tipo_por_nombre(campo.alias)


def _literal_m(valor: str) -> str:
    valor = valor.strip()
    if re.fullmatch(r"-?\d+", valor) or re.fullmatch(r"-?\d+\.\d+", valor):
        return valor
    return m_texto(valor)


def _tabla_m(cabeceras: list[str], filas: list[list[str]]) -> str:
    cols = ", ".join(m_texto(c) for c in cabeceras)
    filas_m = ", ".join("{" + ", ".join(_literal_m(c) for c in fila) + "}" for fila in filas)
    return f"#table({{{cols}}}, {{{filas_m}}})"


def _paso(pasos: list[tuple[str, str]], base: str) -> str:
    usados = {nombre for nombre, _ in pasos}
    if base not in usados:
        return base
    n = 2
    while f"{base}{n}" in usados:
        n += 1
    return f"{base}{n}"


def _add(pasos: list[tuple[str, str]], base: str, expresion: str) -> str:
    nombre = _paso(pasos, base)
    pasos.append((nombre, expresion))
    return nombre


def _ultimo(pasos: list[tuple[str, str]]) -> str:
    return pasos[-1][0]


class Constructor:
    def __init__(self, notas: Notas):
        self.notas = notas
        self.tablas: dict[str, Tabla] = {}
        self.orden: list[str] = []
        self.usa_carpeta = False
        self._lib_avisado = False
        self.referenciadas: set[str] = set()
        self.conexion = ("SERVIDOR", "BASEDATOS", False)
        self.pendientes: list[Carga] = []
        self.etiqueta_pendiente = ""
        self.en_section = False
        self._section_avisado = False

    def convertir(self, script: str) -> Modelo:
        texto = quitar_comentarios((script or "").replace("\x00", "").lstrip("\ufeff"))
        for sentencia in partir_sentencias(texto):
            try:
                self._sentencia(sentencia)
            except Exception as exc:
                muestra = " ".join(sentencia.split())[:80]
                self.notas.aviso(
                    "SCRIPT",
                    f"No se ha podido leer una sentencia ({exc}). Se sigue con el resto: {muestra}",
                )
        if self.pendientes:
            self.notas.aviso("PRECEDING", "Hay un LOAD precedente sin origen. No se ha generado.")
        modelo = Modelo(usa_carpeta=self.usa_carpeta)
        for nombre in self.orden:
            tabla = self.tablas[nombre]
            tabla.hecho = _es_hecho(tabla)
            modelo.tablas.append(tabla)
        hechos = [t for t in modelo.tablas if t.hecho and not t.mapeo]
        for preferido in ("Ventas", "Sales", "Hechos", "Fact"):
            for tabla in hechos:
                if tabla.nombre.lower() == preferido.lower():
                    modelo.hecho = tabla.nombre
                    break
            if modelo.hecho:
                break
        if not modelo.hecho and hechos:
            modelo.hecho = max(hechos, key=lambda t: sum(1 for c in t.columnas if c.nombre.lower() in _HECHOS)).nombre
        if not modelo.hecho and modelo.tablas:
            modelo.hecho = modelo.tablas[0].nombre
        modelo.relaciones = _relaciones(modelo.tablas, self.notas)
        return modelo

    def _sentencia(self, sentencia: str) -> None:
        bruto = sentencia.strip()
        if not bruto:
            return
        if re.match(r"(?i)^section\s+access\b", bruto):
            self.en_section = True
            if not self._section_avisado:
                self.notas.aviso("SECTION", "Section Access no genera roles de Power BI. Hay que definir la seguridad a mano.")
                self._section_avisado = True
            return
        if re.match(r"(?i)^section\s+application\b", bruto):
            self.en_section = False
            return
        if self.en_section:
            return
        if re.match(r"(?i)^(set|let)\b", bruto):
            return
        if re.match(r"(?i)^binary\b", bruto):
            self.notas.aviso("BINARY", "BINARY no se traduce. Incluye el script de la otra app por separado.")
            return
        if re.match(r"(?i)^(store|call|sub|end\s*sub|for|next|loop|switch|case|default|trace|sleep|disconnect|execute|qualify|unqualify|drop\s+field|rename\s+field)\b", bruto):
            self.notas.aviso("CONTROL", f"Sentencia de script no ejecutada: {bruto.split()[0]}. Revísala a mano si cambia los datos.")
            return
        drop = re.match(r"(?i)^drop\s+tables?\s+(.+)$", bruto)
        if drop:
            for nombre in _comas(drop.group(1)):
                self._eliminar(nombre.strip())
            return
        etiqueta, resto = _etiqueta(bruto)
        if etiqueta and not resto:
            self.etiqueta_pendiente = etiqueta
            return
        if not etiqueta and self.etiqueta_pendiente:
            etiqueta = self.etiqueta_pendiente
        self.etiqueta_pendiente = ""
        carga = _parse_carga(resto or bruto, etiqueta, self.notas, self.conexion)
        if carga is None:
            if re.match(r"(?i)^(connect|lib\s+connect)\b", resto or bruto):
                self.conexion = _leer_conexion(resto or bruto, self.notas)
            return
        if carga.fuente == "ninguna":
            self.pendientes.append(carga)
            return
        if carga.unir:
            self._unir(carga)
            return
        if carga.concatenar:
            self._concatenar(carga)
            return
        self._nueva_tabla(carga)

    def _eliminar(self, nombre: str) -> None:
        if nombre in self.tablas:
            del self.tablas[nombre]
            self.orden = [n for n in self.orden if n != nombre]

    def _marcar(self, nombre: str) -> None:
        self.referenciadas.add(nombre)

    def _tocar(self, nombre: str) -> None:
        if nombre in self.referenciadas:
            self.notas.aviso(
                "RESIDENT",
                f"«{nombre}» se modifica después de haberse usado con RESIDENT o JOIN. "
                "En Power Query la consulta apunta a la definición final.",
            )

    def _nueva_tabla(self, carga: Carga) -> None:
        nombre = self._nombre_libre(carga.etiqueta or _nombre_por_defecto(carga))
        pasos, columnas = self._pasos_de_carga(carga, None)
        if carga.mapeo:
            self.notas.info("APPLYMAP", f"Tabla de mapeo «{nombre}» lista para ApplyMap. Queda oculta en el modelo.")
        tabla = Tabla(
            nombre=nombre,
            columnas=columnas,
            pasos=pasos,
            oculta=carga.mapeo,
            mapeo=carga.mapeo,
        )
        self.tablas[nombre] = tabla
        self.orden.append(nombre)
        self._aplicar_precedentes(tabla)

    def _aplicar_precedentes(self, tabla: Tabla) -> None:
        for previo in self.pendientes:
            pasos, columnas = self._transformar(tabla.pasos, tabla.columnas, previo.campos, previo.donde, previo.agrupar)
            tabla.pasos = pasos
            tabla.columnas = columnas
        self.pendientes = []

    def _unir(self, carga: Carga) -> None:
        tipo, destino = carga.unir or ("LEFT", "")
        tabla = self.tablas.get(destino)
        self._tocar(destino)
        if tabla is None:
            self.notas.aviso("JOIN", f"JOIN sobre «{destino}», que no existe todavía. Se ignora.")
            return
        pasos_origen, columnas_origen = self._pasos_de_carga(carga, None)
        if carga.residente:
            self._marcar(carga.residente)
        claves = [c.nombre for c in columnas_origen if any(x.nombre.lower() == c.nombre.lower() for x in tabla.columnas)]
        if not claves:
            self.notas.aviso("JOIN", f"El JOIN con «{destino}» no comparte campos. No se ha combinado.")
            return
        nuevas = [c for c in columnas_origen if c.nombre.lower() not in {x.nombre.lower() for x in tabla.columnas}]
        anidado = "(\n" + render_let(pasos_origen) + "\n)"
        join = {"LEFT": "LeftOuter", "INNER": "Inner", "RIGHT": "RightOuter", "OUTER": "FullOuter", "FULL": "FullOuter"}.get(tipo, "LeftOuter")
        prev = _ultimo(tabla.pasos)
        claves_m = ", ".join(m_texto(c) for c in claves)
        unido = _add(
            tabla.pasos,
            "Unir",
            "Table.NestedJoin("
            f"{m_ident(prev)}, {{{claves_m}}}, {anidado}, {{{claves_m}}}, \"union_qlik\", JoinKind.{join})",
        )
        if nuevas:
            origenes = ", ".join(m_texto(c.nombre) for c in nuevas)
            alias = ", ".join(m_texto(c.nombre) for c in nuevas)
            _add(
                tabla.pasos,
                "ExpandirUnion",
                f"Table.ExpandTableColumn({m_ident(unido)}, \"union_qlik\", {{{origenes}}}, {{{alias}}})",
            )
            tabla.columnas.extend(nuevas)
        else:
            _add(tabla.pasos, "QuitarUnion", f'Table.RemoveColumns({m_ident(unido)}, {{"union_qlik"}})')
        self.notas.info("JOIN", f"{tipo} JOIN combinado en «{destino}» por {', '.join(claves)}.")

    def _concatenar(self, carga: Carga) -> None:
        destino = carga.concatenar or ""
        if destino in {"", "*"}:
            destino = self.orden[-1] if self.orden else ""
        self._tocar(destino)
        tabla = self.tablas.get(destino)
        if tabla is None:
            self.notas.aviso("CONCAT", f"CONCATENATE sobre «{destino}», que no existe. Se crea una tabla nueva.")
            carga.concatenar = None
            self._nueva_tabla(carga)
            return
        pasos_extra, columnas_extra = self._pasos_de_carga(carga, None)
        anidado = "(\n" + render_let(pasos_extra) + "\n)"
        prev = _ultimo(tabla.pasos)
        _add(tabla.pasos, "Concatenar", f"Table.Combine({{{m_ident(prev)}, {anidado}}})")
        conocidos = {c.nombre.lower() for c in tabla.columnas}
        for columna in columnas_extra:
            if columna.nombre.lower() not in conocidos:
                tabla.columnas.append(columna)
        self.notas.info("CONCAT", f"CONCATENATE añadido a «{destino}» con Table.Combine.")

    def _nombre_libre(self, base: str) -> str:
        base = re.sub(r"\s+", " ", (base or "Tabla").strip()) or "Tabla"
        if base not in self.tablas:
            return base
        n = 2
        while f"{base}_{n}" in self.tablas:
            n += 1
        return f"{base}_{n}"

    def _pasos_de_carga(self, carga: Carga, _tabla: Tabla | None) -> tuple[list[tuple[str, str]], list[Columna]]:
        pasos: list[tuple[str, str]] = []
        if carga.fuente == "inline":
            _add(pasos, "Origen", _tabla_m(carga.inline_cabeceras, carga.inline_filas))
            columnas_origen = [Columna(c, *_tipo_inline(c, carga.inline_filas, i)) for i, c in enumerate(carga.inline_cabeceras)]
        elif carga.fuente == "resident":
            if carga.residente not in self.tablas:
                self.notas.aviso("RESIDENT", f"RESIDENT «{carga.residente}» no existe. La consulta queda vacía.")
                _add(pasos, "Origen", '#table({"Columna"}, {})')
                columnas_origen = [Columna("Columna", "string", "type text")]
            else:
                self._marcar(carga.residente)
                _add(pasos, "Origen", m_ident(carga.residente))
                columnas_origen = [
                    Columna(c.nombre, c.tipo, c.tipo_m) for c in self.tablas[carga.residente].columnas
                ]
        elif carga.fuente == "sql":
            if not self.conexion[2]:
                self.notas.aviso(
                    "SQL",
                    "Hay un SQL SELECT sin CONNECT con servidor y base de datos. "
                    f"La consulta usa el marcador {carga.servidor} / {carga.base}. Cámbialo antes de actualizar.",
                )
            sql = re.sub(r"\s+", " ", carga.sql).strip()
            _add(
                pasos,
                "Origen",
                f"Sql.Database({m_texto(carga.servidor)}, {m_texto(carga.base)}, [Query={m_texto(sql)}])",
            )
            nombres = _parse_sql_columnas(sql)
            if nombres == ["*"] or not nombres:
                self.notas.aviso("SQL", "SELECT * o sin lista de columnas: no se han podido declarar las columnas.")
                columnas_origen = []
            else:
                columnas_origen = [Columna(n, *_tipo_por_nombre(n)) for n in nombres]
        else:
            self.usa_carpeta = True
            if carga.ruta.lower().startswith("lib://") and not self._lib_avisado:
                self.notas.info(
                    "RUTA",
                    "Las rutas lib:// se reducen al nombre del fichero. Ajusta el parámetro CarpetaDatos al abrir el PBIP.",
                )
                self._lib_avisado = True
            fichero = _nombre_fichero(carga.ruta)
            if carga.fuente == "qvd":
                self.notas.aviso(
                    "QVD",
                    f"Power Query no lee QVD ({fichero}). Se espera un CSV con el mismo nombre.",
                )
                fichero = re.sub(r"(?i)\.qvd$", ".csv", fichero)
                clase = "csv"
            else:
                clase = carga.fuente
            if clase == "xlsx":
                origen = (
                    f"Excel.Workbook(File.Contents(CarpetaDatos & {m_texto('/' + fichero)}), null, true)"
                    f"{{[Item={m_texto(carga.hoja)}, Kind=\"Sheet\"]}}[Data]"
                )
            else:
                origen = (
                    "Csv.Document(File.Contents(CarpetaDatos & "
                    f"{m_texto('/' + fichero)}), [Delimiter={m_texto(carga.delimitador)}, "
                    f"Encoding={carga.codificacion}, QuoteStyle=QuoteStyle.Csv])"
                )
            _add(pasos, "Origen", origen)
            _add(pasos, "Encabezados", f"Table.PromoteHeaders({m_ident(_ultimo(pasos))}, [PromoteAllScalars=true])")
            columnas_origen = []
        return self._transformar(pasos, columnas_origen, carga.campos, carga.donde, carga.agrupar, carga)

    def _transformar(
        self,
        pasos: list[tuple[str, str]],
        columnas_origen: list[Columna],
        campos: list[Campo],
        donde: str,
        agrupar: list[str],
        carga: Carga | None = None,
    ) -> tuple[list[tuple[str, str]], list[Columna]]:
        if any(c.clase == "agregado" for c in campos) or agrupar:
            return self._agrupar(pasos, campos, donde)
        conocidos = {c.nombre.lower(): c for c in columnas_origen}
        if carga and carga.fuente in {"csv", "xlsx", "qvd"}:
            por_tipar: list[str] = []
            for campo in campos:
                if campo.clase == "columna" and campo.origen:
                    por_tipar.append(campo.origen)
                elif campo.clase == "applymap" and campo.clave_mapa:
                    por_tipar.append(campo.clave_mapa)
                elif campo.clase == "calc":
                    por_tipar.extend(referencias(campo.expr))
            vistos: set[str] = set()
            pares: list[tuple[str, str]] = []
            for nombre in por_tipar:
                if nombre.lower() in vistos:
                    continue
                vistos.add(nombre.lower())
                tipo, tipo_m = _tipo_por_nombre(nombre)
                pares.append((nombre, tipo_m))
                conocidos.setdefault(nombre.lower(), Columna(nombre, tipo, tipo_m))
            if pares:
                lista = ", ".join(f"{{{m_texto(n)}, {t}}}" for n, t in pares)
                _add(pasos, "Tipos", f"Table.TransformColumnTypes({m_ident(_ultimo(pasos))}, {{{lista}}})")
        if donde and _donde_usa(donde, set(conocidos)):
            _add(pasos, "Filtrar", f"Table.SelectRows({m_ident(_ultimo(pasos))}, each {a_m(donde, self.notas)})")
        estrella = any(c.clase == "estrella" for c in campos)
        salida: list[Columna] = []
        renombres: list[tuple[str, str]] = []
        for campo in campos:
            if campo.clase == "estrella":
                for columna in columnas_origen:
                    if columna.nombre.lower() not in {c.nombre.lower() for c in salida}:
                        salida.append(columna)
                continue
            if campo.clase == "columna":
                tipo = conocidos.get(campo.origen.lower())
                tipo_t, tipo_m = (tipo.tipo, tipo.tipo_m) if tipo else _tipo_por_nombre(campo.origen)
                salida.append(Columna(campo.alias, tipo_t, tipo_m))
                if campo.origen != campo.alias:
                    renombres.append((campo.origen, campo.alias))
                continue
            if campo.clase == "applymap":
                self._applymap(pasos, campo)
                salida.append(Columna(campo.alias, "string", "type text"))
                continue
            if campo.clase == "calc":
                tipo_t, tipo_m = _tipo_expresion(campo)
                expr = a_m(campo.expr, self.notas)
                _add(
                    pasos,
                    "Calcular",
                    f"Table.AddColumn({m_ident(_ultimo(pasos))}, {m_texto(campo.alias)}, each {expr}, {tipo_m})",
                )
                salida.append(Columna(campo.alias, tipo_t, tipo_m))
        if renombres:
            pares = ", ".join(f"{{{m_texto(a)}, {m_texto(b)}}}" for a, b in renombres)
            _add(pasos, "Renombrar", f"Table.RenameColumns({m_ident(_ultimo(pasos))}, {{{pares}}})")
        if not estrella and salida:
            lista = ", ".join(m_texto(c.nombre) for c in salida)
            _add(pasos, "Seleccionar", f"Table.SelectColumns({m_ident(_ultimo(pasos))}, {{{lista}}})")
        if donde and not _donde_usa(donde, set(conocidos)):
            _add(pasos, "Filtrar", f"Table.SelectRows({m_ident(_ultimo(pasos))}, each {a_m(donde, self.notas)})")
        if not salida and columnas_origen:
            salida = list(columnas_origen)
        return pasos, salida

    def _applymap(self, pasos: list[tuple[str, str]], campo: Campo) -> None:
        mapa = self.tablas.get(campo.mapa)
        if mapa is None or len(mapa.columnas) < 2:
            self.notas.aviso("APPLYMAP", f"ApplyMap('{campo.mapa}') no encuentra la tabla de mapeo. Revisa el script.")
            _add(
                pasos,
                "MapaFaltante",
                f"Table.AddColumn({m_ident(_ultimo(pasos))}, {m_texto(campo.alias)}, each null, type text)",
            )
            return
        clave = mapa.columnas[0].nombre
        valor = mapa.columnas[1].nombre
        self._marcar(campo.mapa)
        prev = _ultimo(pasos)
        unido = _add(
            pasos,
            "AplicarMapa",
            "Table.NestedJoin("
            f"{m_ident(prev)}, {{{m_texto(campo.clave_mapa)}}}, {m_ident(campo.mapa)}, "
            f"{{{m_texto(clave)}}}, \"mapa_qlik\", JoinKind.LeftOuter)",
        )
        expandido = _add(
            pasos,
            "ExpandirMapa",
            f"Table.ExpandTableColumn({m_ident(unido)}, \"mapa_qlik\", {{{m_texto(valor)}}}, {{{m_texto(campo.alias)}}})",
        )
        if campo.defecto:
            defecto = a_m(campo.defecto, self.notas)
            _add(
                pasos,
                "DefectoMapa",
                f"Table.ReplaceValue({m_ident(expandido)}, null, {defecto}, Replacer.ReplaceValue, {{{m_texto(campo.alias)}}})",
            )
        self.notas.info("APPLYMAP", f"ApplyMap('{campo.mapa}') convertido en combinación hacia «{campo.alias}».")

    def _agrupar(self, pasos, campos: list[Campo], donde: str) -> tuple[list[tuple[str, str]], list[Columna]]:
        if donde:
            _add(pasos, "Filtrar", f"Table.SelectRows({m_ident(_ultimo(pasos))}, each {a_m(donde, self.notas)})")
        claves = [c.origen or c.alias for c in campos if c.clase == "columna"]
        aggs = [c for c in campos if c.clase == "agregado"]
        if not aggs:
            self.notas.aviso("GROUP", "GROUP BY sin agregados reconocidos. Se deja la tabla sin agrupar.")
            return pasos, [Columna(c, *_tipo_por_nombre(c)) for c in claves]
        lista_claves = ", ".join(m_texto(c) for c in claves)
        trozos = []
        columnas = [Columna(c.alias, *_tipo_por_nombre(c.origen or c.alias)) for c in campos if c.clase == "columna"]
        funciones = {"sum": "List.Sum", "avg": "List.Average", "count": "List.Count", "min": "List.Min", "max": "List.Max"}
        for campo in aggs:
            funcion = funciones.get(campo.agregado, "List.Sum")
            tipo_m = "Int64.Type" if campo.agregado == "count" else "type number"
            tipo = "int64" if campo.agregado == "count" else "double"
            trozos.append(
                "{" + m_texto(campo.alias) + f", each {funcion}([{campo.campo_agregado}]), {tipo_m}" + "}"
            )
            columnas.append(Columna(campo.alias, tipo, tipo_m))
        _add(
            pasos,
            "Agrupar",
            f"Table.Group({m_ident(_ultimo(pasos))}, {{{lista_claves}}}, {{{', '.join(trozos)}}})",
        )
        self.notas.info("GROUP", "GROUP BY convertido en Table.Group.")
        if any(c.clase == "calc" for c in campos):
            self.notas.aviso("GROUP", "Hay cálculos junto a GROUP BY que no se han reescrito. Revísalos.")
        return pasos, columnas


def _donde_usa(donde: str, nombres: set[str]) -> bool:
    return all(ref.lower() in nombres for ref in referencias(donde))


def _tipo_inline(nombre: str, filas: list[list[str]], indice: int) -> tuple[str, str]:
    valores = [fila[indice].strip() for fila in filas if len(fila) > indice]
    if valores and all(re.fullmatch(r"-?\d+", v) for v in valores):
        return "int64", "Int64.Type"
    if valores and all(re.fullmatch(r"-?\d+(?:\.\d+)?", v) for v in valores):
        return "double", "type number"
    return _tipo_por_nombre(nombre)


def _es_hecho(tabla: Tabla) -> bool:
    if any(c.nombre.lower() in _HECHOS for c in tabla.columnas):
        return True
    # Qlik nombra la tabla de hechos Fact, FactTable, Facts… «Factura» no entra aquí.
    tokens = re.findall(r"[a-z0-9]+", tabla.nombre.lower())
    return any(token == "fact" or (token.startswith("fact") and not token.startswith("factura")) for token in tokens)


def _etiqueta(texto: str) -> tuple[str, str]:
    match = re.match(r"^([A-Za-z_\u00C0-\u024F][\w \u00C0-\u024F]*?)\s*:\s*(.*)$", texto, re.S)
    if not match:
        return "", texto
    nombre = match.group(1).strip()
    if re.match(r"(?i)^(load|sql|mapping|concatenate|left|right|inner|outer|join|section)$", nombre):
        return "", texto
    return nombre, match.group(2).strip()


def _leer_conexion(texto: str, notas: Notas) -> tuple[str, str, bool]:
    servidor = ""
    base = ""
    for patron, destino in (
        (r"(?i)data\s*source\s*=\s*([^;\]\"']+)", "servidor"),
        (r"(?i)initial\s*catalog\s*=\s*([^;\]\"']+)", "base"),
        (r"(?i)\bserver\s*=\s*([^;\]\"']+)", "servidor"),
        (r"(?i)\bdatabase\s*=\s*([^;\]\"']+)", "base"),
    ):
        match = re.search(patron, texto)
        if not match:
            continue
        if destino == "servidor":
            servidor = match.group(1).strip()
        else:
            base = match.group(1).strip()
    if servidor and base:
        notas.info("SQL", f"CONNECT detectado: servidor {servidor}, base {base}.")
        return servidor, base, True
    notas.aviso("SQL", "Hay un CONNECT, pero no se han podido leer el servidor y la base de datos.")
    return "SERVIDOR", "BASEDATOS", False


def _nombre_por_defecto(carga: Carga) -> str:
    if carga.fuente in {"csv", "xlsx", "qvd"} and carga.ruta:
        base = _nombre_fichero(carga.ruta)
        base = re.sub(r"\.[^.]+$", "", base)
        return base or "Tabla"
    if carga.fuente == "resident":
        return f"{carga.residente}_resident"
    if carga.fuente == "sql":
        return "ConsultaSQL"
    return "Tabla"


def _parse_carga(texto: str, etiqueta: str, notas: Notas, conexion: tuple[str, str, bool]) -> Carga | None:
    resto = texto.strip()
    unir = None
    concatenar = None
    match = re.match(r"(?is)^concatenate\s*(?:\(\s*([^)]*?)\s*\))?\s*(.*)$", resto)
    if match and re.match(r"(?i)concatenate\b", resto):
        concatenar = (match.group(1) or "*").strip() or "*"
        resto = match.group(2).strip()
    else:
        match = re.match(r"(?is)^(left|right|inner|outer|full)?\s*join\s*\(\s*([^)]+)\s*\)\s*(.*)$", resto)
        if match and re.search(r"(?i)\bjoin\b", resto.split("\n", 1)[0]):
            unir = ((match.group(1) or "LEFT").upper(), match.group(2).strip())
            resto = match.group(3).strip()
    mapeo = False
    match = re.match(r"(?is)^mapping\s+(.*)$", resto)
    if match:
        mapeo = True
        resto = match.group(1).strip()
    if re.match(r"(?is)^sql\b", resto):
        sql = re.sub(r"(?is)^sql\s+", "", resto).strip()
        return Carga(
            etiqueta=etiqueta,
            campos=[],
            fuente="sql",
            sql=sql,
            mapeo=mapeo,
            unir=unir,
            concatenar=concatenar,
            servidor=conexion[0],
            base=conexion[1],
        )
    if not re.match(r"(?is)^load\b", resto):
        if resto and not re.match(r"(?i)^(connect|lib\s+connect)\b", resto):
            notas.aviso("SCRIPT", f"Sentencia no reconocida y omitida: {resto.splitlines()[0][:80]}")
        return None
    cuerpo = re.sub(r"(?is)^load\s+", "", resto).strip()
    clave, pos = _buscar_fuente(cuerpo)
    if pos < 0:
        return Carga(etiqueta=etiqueta, campos=_campos_de_lista(cuerpo), fuente="ninguna", mapeo=mapeo, unir=unir, concatenar=concatenar)
    lista = cuerpo[:pos].strip().rstrip(",")
    despues = cuerpo[pos + len(clave) :].strip()
    donde, agrupar, despues = _donde_y_grupo(despues)
    carga = Carga(
        etiqueta=etiqueta,
        campos=_campos_de_lista(lista),
        fuente="ninguna",
        donde=donde,
        agrupar=agrupar,
        mapeo=mapeo,
        unir=unir,
        concatenar=concatenar,
        servidor=conexion[0],
        base=conexion[1],
    )
    if clave == "INLINE":
        bloque = _extraer_corchete(despues)
        cabeceras, filas = _parse_inline(bloque)
        carga.fuente = "inline"
        carga.inline_cabeceras = cabeceras
        carga.inline_filas = filas
        return carga
    if clave == "RESIDENT":
        nombre = re.match(r"([A-Za-z_\u00C0-\u024F][\w\u00C0-\u024F]*)", despues)
        carga.fuente = "resident"
        carga.residente = nombre.group(1) if nombre else ""
        return carga
    if clave == "AUTOGENERATE":
        notas.aviso("AUTOGENERATE", "AUTOGENERATE no se traduce. La tabla no se ha creado.")
        return None
    # FROM
    ruta, spec = _from(despues)
    clase, delimitador, codificacion, hoja = _especificar(spec)
    carga.fuente = clase
    carga.ruta = ruta
    carga.delimitador = delimitador
    carga.codificacion = codificacion
    carga.hoja = hoja
    return carga


def _donde_y_grupo(texto: str) -> tuple[str, list[str], str]:
    donde = ""
    grupo: list[str] = []
    resto = texto
    match = re.search(r"(?is)\bwhere\b", resto)
    grupo_m = re.search(r"(?is)\bgroup\s+by\b", resto)
    corte = len(resto)
    if match:
        corte = min(corte, match.start())
    if grupo_m:
        corte = min(corte, grupo_m.start())
    cabeza = resto[:corte]
    if match:
        fin = grupo_m.start() if grupo_m and grupo_m.start() > match.start() else len(resto)
        donde = resto[match.end() : fin].strip()
    if grupo_m:
        grupo = [_sin_corchetes(p) for p in _comas(resto[grupo_m.end() :])]
    return donde, grupo, cabeza


def _extraer_corchete(texto: str) -> str:
    inicio = texto.find("[")
    if inicio < 0:
        return ""
    profundidad = 0
    for i in range(inicio, len(texto)):
        if texto[i] == "[":
            profundidad += 1
        elif texto[i] == "]":
            profundidad -= 1
            if profundidad == 0:
                return texto[inicio + 1 : i]
    return texto[inicio + 1 :]


def _from(texto: str) -> tuple[str, str]:
    texto = texto.strip()
    if texto.startswith("["):
        fin = texto.find("]")
        ruta = texto[1:fin] if fin > 0 else texto
        resto = texto[fin + 1 :] if fin > 0 else ""
    else:
        match = re.match(r"(\S+)", texto)
        ruta = match.group(1) if match else ""
        resto = texto[len(ruta) :]
    spec = ""
    paren = resto.find("(")
    if paren >= 0:
        profundidad = 0
        for i in range(paren, len(resto)):
            if resto[i] == "(":
                profundidad += 1
            elif resto[i] == ")":
                profundidad -= 1
                if profundidad == 0:
                    spec = resto[paren + 1 : i]
                    break
    return ruta, spec


def _es_clave(nombre: str) -> bool:
    original = nombre.strip().strip('"').strip("[]")
    clave = original.lower()
    if clave == "id" or clave.endswith("id") or clave.endswith("_key"):
        return True
    # CustKey, MonthlyRegionKey, Item-Branch Key, CustKeyAR.
    separado = re.sub(r"([A-Za-z])([A-Z])", r"\1 \2", original)
    tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9]+", separado)]
    return "key" in tokens or "id" in tokens


def _relaciones(tablas: list[Tabla], notas: Notas) -> list[Relacion]:
    visibles = [t for t in tablas if not t.mapeo]
    por_clave: dict[str, list[tuple[Tabla, Columna]]] = {}
    for tabla in visibles:
        for columna in tabla.columnas:
            if _es_clave(columna.nombre):
                por_clave.setdefault(columna.nombre.lower(), []).append((tabla, columna))
    relaciones: list[Relacion] = []
    vistos: set[tuple[str, str, str, str]] = set()
    for clave, duenos in por_clave.items():
        hechos = [(t, c) for t, c in duenos if t.hecho]
        dimensiones = [(t, c) for t, c in duenos if not t.hecho]
        if len(hechos) >= 2 and not dimensiones:
            nombres = ", ".join(t.nombre for t, _ in hechos)
            notas.aviso(
                "SINTETICA",
                f"La clave {clave} está en varias tablas de hechos ({nombres}) y en ninguna dimensión. "
                "No se crea esa relación: podría ser una clave sintética.",
            )
            continue
        if not hechos or not dimensiones:
            continue
        if len(dimensiones) > 1:
            notas.aviso(
                "SINTETICA",
                f"La clave {clave} aparece en varias dimensiones. Revisa qué relación debe quedar.",
            )
        for tabla_h, col_h in hechos:
            for tabla_d, col_d in dimensiones:
                firma = (tabla_h.nombre, col_h.nombre, tabla_d.nombre, col_d.nombre)
                if firma in vistos:
                    continue
                vistos.add(firma)
                if col_h.tipo != col_d.tipo:
                    notas.aviso(
                        "TIPO",
                        f"La relación {tabla_h.nombre}.{col_h.nombre} → {tabla_d.nombre}.{col_d.nombre} "
                        "une tipos distintos. Revísala.",
                    )
                relaciones.append(
                    Relacion(tabla_h.nombre, col_h.nombre, tabla_d.nombre, col_d.nombre)
                )
    return relaciones


def analizar_script(script: str, notas: Notas | None = None) -> tuple[Modelo, Notas]:
    notas = notas or Notas()
    modelo = Constructor(notas).convertir(script or "")
    return modelo, notas
