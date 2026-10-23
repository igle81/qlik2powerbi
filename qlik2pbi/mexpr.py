"""Expresiones de script Qlik a Power Query M, en contexto de fila."""

from __future__ import annotations

import re

from qlik2pbi.notas import Notas
from qlik2pbi.scan import ScanError, Scanner

_FUNCIONES = {
    "if",
    "year",
    "month",
    "day",
    "monthname",
    "weekday",
    "upper",
    "lower",
    "trim",
    "len",
    "round",
    "abs",
    "date",
    "today",
    "now",
    "and",
    "or",
    "not",
}


def m_texto(valor: str) -> str:
    return '"' + str(valor).replace('"', '""') + '"'


def m_ident(nombre: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", nombre or ""):
        return nombre
    return '#"' + str(nombre).replace('"', '""') + '"'


class _M:
    def __init__(self, texto: str, notas: Notas):
        self.sc = Scanner(texto)
        self.notas = notas

    def traducir(self) -> str:
        try:
            return self._or()
        except ScanError as exc:
            self.notas.aviso("M", f"Expresión de script no leída ({exc}). Se deja null.")
            return "null"

    def _or(self) -> str:
        izquierda = self._and()
        while self.sc.kw("or"):
            izquierda = f"{izquierda} or {self._and()}"
        return izquierda

    def _and(self) -> str:
        izquierda = self._not()
        while self.sc.kw("and"):
            izquierda = f"{izquierda} and {self._not()}"
        return izquierda

    def _not(self) -> str:
        if self.sc.kw("not"):
            return f"not {self._not()}"
        return self._cmp()

    def _cmp(self) -> str:
        izquierda = self._add()
        operador = self.sc.op2()
        if not operador:
            return izquierda
        return f"{izquierda} {operador} {self._add()}"

    def _add(self) -> str:
        izquierda = self._mul()
        while len(self.sc.peek()) == 1 and self.sc.peek() in "+-&":
            operador = self.sc.peek()
            self.sc.consume(operador)
            izquierda = f"{izquierda} {operador} {self._mul()}"
        return izquierda

    def _mul(self) -> str:
        izquierda = self._unario()
        while len(self.sc.peek()) == 1 and self.sc.peek() in "*/":
            operador = self.sc.peek()
            self.sc.consume(operador)
            izquierda = f"{izquierda} {operador} {self._unario()}"
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
            return m_texto(texto)
        numero = self.sc.number()
        if numero is not None:
            return numero
        nombre = self.sc.ident()
        if nombre is None:
            raise ScanError(f"token inesperado {self.sc.peek()!r}")
        if self.sc.peek() == "(":
            return self._llamada(nombre)
        return f"[{nombre}]"

    def _llamada(self, nombre: str) -> str:
        clave = nombre.lower()
        self.sc.consume("(")
        args = self._argumentos()
        self.sc.consume(")")
        if clave == "if" and len(args) >= 2:
            si_no = args[2] if len(args) > 2 else "null"
            return f"if {args[0]} then {args[1]} else {si_no}"
        if clave == "year" and args:
            return f"Date.Year({args[0]})"
        if clave == "month" and args:
            return f"Date.Month({args[0]})"
        if clave == "day" and args:
            return f"Date.Day({args[0]})"
        if clave == "monthname" and args:
            return f"Date.MonthName({args[0]})"
        if clave == "weekday" and args:
            return f"Date.DayOfWeek({args[0]})"
        if clave == "upper" and args:
            return f"Text.Upper({args[0]})"
        if clave == "lower" and args:
            return f"Text.Lower({args[0]})"
        if clave == "trim" and args:
            return f"Text.Trim({args[0]})"
        if clave == "len" and args:
            return f"Text.Length({args[0]})"
        if clave == "round" and args:
            extra = f", {args[1]}" if len(args) > 1 else ""
            return f"Number.Round({args[0]}{extra})"
        if clave == "abs" and args:
            return f"Number.Abs({args[0]})"
        if clave == "date" and args:
            return _fecha_m(args[0])
        if clave == "today":
            return "Date.From(DateTime.LocalNow())"
        if clave == "now":
            return "DateTime.LocalNow()"
        self.notas.aviso("M", f"La función de script {nombre} no se traduce en M. Revisa el paso.")
        return "null"

    def _argumentos(self) -> list[str]:
        if self.sc.peek() == ")":
            return []
        args = [self._or()]
        while self.sc.peek() == ",":
            self.sc.consume(",")
            args.append(self._or())
        return args


def _fecha_m(argumento: str) -> str:
    match = re.fullmatch(r'"(\d{4})-(\d{2})-(\d{2})"', argumento.strip())
    if match:
        return f"#date({int(match.group(1))}, {int(match.group(2))}, {int(match.group(3))})"
    return f"Date.From({argumento})"


def a_m(expresion: str, notas: Notas) -> str:
    return _M(expresion.strip(), notas).traducir()


def referencias(expresion: str) -> list[str]:
    """Nombres de campo citados en una expresión, sin funciones ni palabras clave."""
    nombres: list[str] = []
    for corchete, suelto in re.findall(
        r"\[([^\]]+)\]|([A-Za-z_\u00C0-\u024F][A-Za-z0-9_\u00C0-\u024F]*)",
        expresion or "",
    ):
        nombre = corchete or suelto
        if nombre.lower() in _FUNCIONES:
            continue
        nombres.append(nombre)
    return nombres
