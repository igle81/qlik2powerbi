"""Traducción de expresiones de gráfico Qlik a DAX."""

from __future__ import annotations

import re

from qlik2pbi.notas import Notas
from qlik2pbi.scan import ScanError, Scanner

# nombre en minúsculas → función DAX de un argumento o más, en el mismo orden
_SIMPLES = {
    "upper": "UPPER",
    "lower": "LOWER",
    "trim": "TRIM",
    "len": "LEN",
    "round": "ROUND",
    "abs": "ABS",
    "fabs": "ABS",
    "ceil": "CEILING",
    "floor": "FLOOR",
    "sqrt": "SQRT",
    "mod": "MOD",
    "year": "YEAR",
    "month": "MONTH",
    "day": "DAY",
    "today": "TODAY",
    "now": "NOW",
    "isnull": "ISBLANK",
    "null": "BLANK",
    "num": "VALUE",
    "text": "FORMAT",
}

_AGREGADOS = {"sum": "SUM", "avg": "AVERAGE", "min": "MIN", "max": "MAX", "only": "SELECTEDVALUE"}

_REVISION = {
    "above",
    "below",
    "before",
    "after",
    "top",
    "bottom",
    "peek",
    "previous",
    "exists",
    "rangesum",
    "rangeavg",
    "rangemin",
    "rangemax",
    "rangecount",
    "dual",
    "class",
    "firstsortedvalue",
    "rank",
    "rowno",
    "recno",
    "correl",
    "fractile",
    "stdev",
    "median",
    "mode",
    "applymap",
    "intervalmatch",
    "networkdays",
    "age",
    "match",
    "mixmatch",
    "wildmatch",
    "pick",
    "subfield",
    "alt",
    "fieldvalue",
    "getfieldselections",
    "getcurrentselections",
    "date#",
    "num#",
    "timestamp",
    "timestamp#",
}

_PALABRAS = {"and", "or", "not", "distinct", "true", "false"}


def _dax_texto(valor: str) -> str:
    return '"' + valor.replace('"', '""') + '"'


def _campo(resolver, nombre: str, notas: Notas) -> str:
    elegido = resolver(nombre) if resolver else None
    if not elegido:
        if nombre.lower() not in _PALABRAS:
            notas.aviso(
                "CAMPO",
                f"El campo «{nombre}» no está en el modelo generado. Revisa la medida a mano.",
            )
        return nombre
    tabla, columna = elegido
    return f"'{tabla}'[{columna}]"


class _Dax:
    def __init__(self, texto: str, resolver, notas: Notas):
        self.sc = Scanner(texto)
        self.resolver = resolver
        self.notas = notas
        self._aggr_nivel = 0

    def traducir(self) -> str:
        try:
            expresion = self._or()
        except ScanError as exc:
            self.notas.aviso("EXPRESION", f"No se pudo leer la expresión ({exc}). Se deja en BLANK.")
            return "BLANK()"
        return expresion

    def _or(self) -> str:
        izquierda = self._and()
        while self.sc.kw("or"):
            derecha = self._and()
            izquierda = f"{izquierda} || {derecha}"
        return izquierda

    def _and(self) -> str:
        izquierda = self._not()
        while self.sc.kw("and"):
            derecha = self._not()
            izquierda = f"{izquierda} && {derecha}"
        return izquierda

    def _not(self) -> str:
        if self.sc.kw("not"):
            return f"NOT {self._not()}"
        return self._cmp()

    def _cmp(self) -> str:
        izquierda = self._add()
        operador = self.sc.op2()
        if not operador:
            return izquierda
        derecha = self._add()
        return f"{izquierda} {operador} {derecha}"

    def _add(self) -> str:
        izquierda = self._mul()
        while len(self.sc.peek()) == 1 and self.sc.peek() in "+-&":
            operador = self.sc.peek()
            self.sc.consume(operador)
            derecha = self._mul()
            izquierda = f"{izquierda} {operador} {derecha}"
        return izquierda

    def _mul(self) -> str:
        izquierda = self._unario()
        while len(self.sc.peek()) == 1 and self.sc.peek() in "*/":
            operador = self.sc.peek()
            self.sc.consume(operador)
            derecha = self._unario()
            izquierda = f"{izquierda} {operador} {derecha}"
        return izquierda

    def _unario(self) -> str:
        if self.sc.peek() == "-":
            self.sc.consume("-")
            return f"-{self._unario()}"
        return self._primario()

    def _primario(self) -> str:
        if self.sc.peek() == "(":
            self.sc.consume("(")
            expresion = self._or()
            self.sc.consume(")")
            return f"({expresion})"
        texto = self.sc.string()
        if texto is not None:
            return _dax_texto(texto)
        numero = self.sc.number()
        if numero is not None:
            return numero
        if self.sc.kw("true"):
            return "TRUE()"
        if self.sc.kw("false"):
            return "FALSE()"
        nombre = self.sc.ident()
        if nombre is None:
            ch = self.sc.peek()
            raise ScanError(f"token inesperado {ch!r}")
        if self.sc.peek() == "(":
            return self._llamada(nombre)
        return _campo(self.resolver, nombre, self.notas)

    def _llamada(self, nombre: str) -> str:
        clave = nombre.lower()
        self.sc.consume("(")
        if clave in _AGREGADOS or clave == "count":
            return self._agregado(clave)
        if clave == "aggr":
            return self._aggr()
        if clave == "if":
            return self._if()
        if clave == "concat":
            return self._concat()
        if clave == "makedate":
            args = self._argumentos()
            self.sc.consume(")")
            return f"DATE({', '.join(args)})" if len(args) == 3 else "BLANK()"
        if clave in _SIMPLES:
            args = self._argumentos()
            self.sc.consume(")")
            funcion = _SIMPLES[clave]
            if clave == "num" and len(args) != 1:
                self.notas.aviso("NUM", "Num con formato no se traduce. Se usa VALUE del primer argumento.")
                return f"VALUE({args[0]})" if args else "BLANK()"
            if clave in {"today", "now", "null"} and not args:
                return f"{funcion}()"
            return f"{funcion}({', '.join(args)})"
        if clave in _REVISION or clave.endswith("#"):
            crudo = self._resto_parentesis()
            self.notas.aviso(
                "FUNCION",
                f"{nombre} no tiene traducción automática. Revisa la medida a mano. Original: {nombre}{crudo}",
            )
            return f"/* REVISAR: {nombre}{crudo} */ BLANK()"
        crudo = self._resto_parentesis()
        self.notas.aviso(
            "FUNCION",
            f"La función {nombre} no está en el catálogo. Original: {nombre}{crudo}",
        )
        return f"/* REVISAR: {nombre}{crudo} */ BLANK()"

    def _resto_parentesis(self) -> str:
        """Lee hasta el paréntesis que cierra la llamada, sin traducirlo."""
        inicio = self.sc.i
        profundidad = 1
        en_cadena = None
        while self.sc.i < self.sc.n and profundidad:
            ch = self.sc.s[self.sc.i]
            if en_cadena:
                if ch == en_cadena:
                    en_cadena = None
            elif ch in "'\"":
                en_cadena = ch
            elif ch == "(":
                profundidad += 1
            elif ch == ")":
                profundidad -= 1
                if profundidad == 0:
                    texto = self.sc.s[inicio : self.sc.i]
                    self.sc.i += 1
                    limpio = re.sub(r"\s+", " ", texto).strip()
                    limpio = limpio.replace("*/", "* /")
                    return f"({limpio})"
            self.sc.i += 1
        raise ScanError("paréntesis sin cerrar")

    def _argumentos(self) -> list[str]:
        if self.sc.peek() == ")":
            return []
        args = [self._or()]
        while self.sc.peek() == ",":
            self.sc.consume(",")
            args.append(self._or())
        return args

    def _agregado(self, clave: str) -> str:
        conjunto = None
        if self.sc.peek() == "{":
            conjunto = self._llave()
        distinto = self.sc.kw("distinct")
        if clave == "count" and self.sc.peek() == ")":
            self.sc.consume(")")
            self.notas.aviso("COUNT", "Count() sin campo no se puede traducir.")
            return "BLANK()"
        # Sum(Aggr(...)) es la suma del vector de Aggr, no un SUM extra.
        if clave == "sum" and self.sc.peek() != ")" and self._mira_aggr():
            interno = self._primario()
            self.sc.consume(")")
            return self._aplicar_conjunto(interno, conjunto)
        interno = self._or()
        self.sc.consume(")")
        if clave == "count" and distinto:
            funcion = f"DISTINCTCOUNT({interno})"
        elif clave == "count":
            funcion = f"COUNT({interno})"
        else:
            funcion = f"{_AGREGADOS[clave]}({interno})"
        return self._aplicar_conjunto(funcion, conjunto)

    def _mira_aggr(self) -> bool:
        marca = self.sc.i
        nombre = self.sc.ident()
        es = bool(nombre and nombre.lower() == "aggr" and self.sc.peek() == "(")
        self.sc.i = marca
        return es

    def _if(self) -> str:
        condicion = self._or()
        self.sc.consume(",")
        si = self._or()
        si_no = "BLANK()"
        if self.sc.peek() == ",":
            self.sc.consume(",")
            si_no = self._or()
        self.sc.consume(")")
        return f"IF({condicion}, {si}, {si_no})"

    def _aggr(self) -> str:
        self._aggr_nivel += 1
        if self._aggr_nivel > 1:
            self.notas.aviso("AGGR", "Aggr anidado: revisa la medida. Solo el primer nivel es fiable.")
        interno = self._or()
        dimensiones: list[str] = []
        while self.sc.peek() == ",":
            self.sc.consume(",")
            dimensiones.append(self._or())
        self.sc.consume(")")
        self._aggr_nivel -= 1
        if not dimensiones:
            self.notas.aviso("AGGR", "Aggr sin dimensiones. Se deja la expresión interior.")
            return interno
        if len(dimensiones) == 1:
            tabla = f"VALUES({dimensiones[0]})"
        else:
            tabla = "SUMMARIZE(" + ", ".join([self._tabla_de(dimensiones[0]), *dimensiones]) + ")"
        return f"SUMX({tabla}, CALCULATE({interno}))"

    def _tabla_de(self, campo_dax: str) -> str:
        match = re.match(r"'([^']+)'\[", campo_dax)
        if match:
            return f"'{match.group(1)}'"
        return campo_dax

    def _concat(self) -> str:
        campo = self._or()
        separador = '", "'
        if self.sc.peek() == ",":
            self.sc.consume(",")
            separador = self._or()
        if self.sc.peek() == ",":
            self.notas.aviso("CONCAT", "Concat con orden o DISTINCT no se traduce del todo.")
            self._resto_parentesis()
        else:
            self.sc.consume(")")
        return f"CONCATENATEX(VALUES({campo}), {campo}, {separador})"

    def _llave(self) -> str:
        self.sc.consume("{")
        profundidad = 1
        inicio = self.sc.i
        en_cadena = None
        while self.sc.i < self.sc.n and profundidad:
            ch = self.sc.s[self.sc.i]
            if en_cadena:
                if ch == en_cadena:
                    en_cadena = None
            elif ch in "'\"":
                en_cadena = ch
            elif ch == "{":
                profundidad += 1
            elif ch == "}":
                profundidad -= 1
                if profundidad == 0:
                    texto = self.sc.s[inicio : self.sc.i]
                    self.sc.i += 1
                    return texto.strip()
            self.sc.i += 1
        raise ScanError("llave de análisis de conjuntos sin cerrar")

    def _aplicar_conjunto(self, funcion: str, conjunto: str | None) -> str:
        if conjunto is None:
            return funcion
        cuerpo = conjunto.strip()
        if cuerpo in {"$", ""}:
            return funcion
        if cuerpo == "1":
            tabla = self._tabla_de_funcion(funcion)
            self.notas.info("SET", "El identificador de conjunto {1} ignora los filtros del informe (ALL).")
            if not tabla:
                self.notas.aviso("SET", "No se ha localizado la tabla para aplicar {1}. Revisa la medida.")
                return funcion
            return f"CALCULATE({funcion}, ALL({tabla}))"
        if not cuerpo.startswith("<") or ">" not in cuerpo:
            self.notas.aviso("SET", f"Análisis de conjuntos no reconocido: {{{conjunto}}}.")
            return funcion
        interior = cuerpo[1 : cuerpo.rfind(">")].strip()
        if re.search(r"\b[PE]\s*\(", interior) or "::" in interior or re.search(r"[+*]\s*\{", interior):
            self.notas.aviso(
                "SET",
                "El análisis de conjuntos usa P(), E(), un estado alterno o una unión de conjuntos. Revísalo a mano.",
            )
            return f"CALCULATE({funcion}) /* REVISAR set analysis */"
        filtros = self._modificadores(interior)
        if filtros is None:
            return f"CALCULATE({funcion}) /* REVISAR set analysis */"
        if not filtros:
            return funcion
        return f"CALCULATE({funcion}, {', '.join(filtros)})"

    def _tabla_de_funcion(self, funcion: str) -> str:
        match = re.search(r"'([^']+)'\[", funcion)
        if match:
            return f"'{match.group(1)}'"
        return ""

    def _modificadores(self, interior: str) -> list[str] | None:
        partes = _split_comas(interior)
        filtros: list[str] = []
        for parte in partes:
            parte = parte.strip()
            if not parte:
                continue
            match = re.match(
                r"^(\[[^\]]+\]|[A-Za-z_\u00C0-\u024F][\w\u00C0-\u024F]*)\s*([-=])?\s*=\s*(.*)$",
                parte,
                re.S,
            )
            if not match:
                self.notas.aviso("SET", f"Modificador de conjunto no reconocido: {parte}")
                return None
            campo = match.group(1).strip("[]")
            exclusion = match.group(2) == "-"
            valores_txt = match.group(3).strip()
            ref = _campo(self.resolver, campo, self.notas)
            if valores_txt == "":
                filtros.append(f"REMOVEFILTERS({ref})")
                continue
            if "$(" in valores_txt:
                self.notas.aviso(
                    "SET",
                    f"El modificador de «{campo}» usa una variable $(). Sustitúyela a mano.",
                )
                continue
            valores = _valores_conjunto(valores_txt)
            if valores is None:
                self.notas.aviso("SET", f"Valores de conjunto no reconocidos en «{campo}»: {valores_txt}")
                return None
            if any(v.startswith("*") or v.endswith("*") for v in valores if isinstance(v, str) and not v[:1].isdigit()):
                self.notas.aviso("SET", f"Búsqueda con comodín en «{campo}». Revísala a mano.")
            literales = ", ".join(valores)
            if exclusion:
                if len(valores) == 1:
                    filtros.append(f"{ref} <> {valores[0]}")
                else:
                    filtros.append(f"NOT {ref} IN {{{literales}}}")
            elif len(valores) == 1:
                filtros.append(f"{ref} = {valores[0]}")
            else:
                filtros.append(f"{ref} IN {{{literales}}}")
        return filtros


def _split_comas(texto: str) -> list[str]:
    partes: list[str] = []
    buf: list[str] = []
    profundidad = 0
    en_cadena = None
    for ch in texto:
        if en_cadena:
            buf.append(ch)
            if ch == en_cadena:
                en_cadena = None
            continue
        if ch in "'\"":
            en_cadena = ch
            buf.append(ch)
            continue
        if ch in "{([":
            profundidad += 1
        elif ch in "})]":
            profundidad = max(0, profundidad - 1)
        elif ch == "," and profundidad == 0:
            partes.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        partes.append("".join(buf))
    return partes


def _valores_conjunto(texto: str) -> list[str] | None:
    texto = texto.strip()
    if texto.startswith("{") and texto.endswith("}"):
        texto = texto[1:-1].strip()
    if not texto:
        return []
    salida: list[str] = []
    for parte in _split_comas(texto):
        parte = parte.strip()
        if not parte:
            continue
        if (parte.startswith("'") and parte.endswith("'")) or (parte.startswith('"') and parte.endswith('"')):
            contenido = parte[1:-1].replace("''", "'")
            if contenido.startswith(">=") or contenido.startswith("<=") or contenido.startswith(">") or contenido.startswith("<"):
                return None
            if "*" in contenido:
                return None
            salida.append(_dax_texto(contenido))
            continue
        if re.fullmatch(r"-?\d+(?:\.\d+)?", parte):
            salida.append(parte)
            continue
        return None
    return salida


def traducir(expresion: str, resolver, notas: Notas) -> str:
    """Traduce una expresión Qlik. `resolver(nombre) -> (tabla, columna) | None`."""
    if not (expresion or "").strip():
        notas.aviso("EXPRESION", "Hay una medida sin expresión.")
        return "BLANK()"
    return _Dax(expresion.strip(), resolver, notas).traducir()
