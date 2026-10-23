"""Escáner compartido para expresiones de script y de gráfico."""

from __future__ import annotations

import re


class ScanError(ValueError):
    pass


class Scanner:
    def __init__(self, text: str):
        self.s = text or ""
        self.n = len(self.s)
        self.i = 0

    def skip(self) -> None:
        while self.i < self.n and self.s[self.i].isspace():
            self.i += 1

    def eof(self) -> bool:
        self.skip()
        return self.i >= self.n

    def peek(self) -> str:
        self.skip()
        return self.s[self.i] if self.i < self.n else ""

    def kw(self, word: str) -> bool:
        self.skip()
        match = re.match(rf"(?i){re.escape(word)}\b", self.s[self.i :])
        if not match:
            return False
        self.i += match.end()
        return True

    def ident(self) -> str | None:
        self.skip()
        if self.peek() == "[":
            self.i += 1
            fin = self.s.find("]", self.i)
            if fin < 0:
                raise ScanError("falta el cierre de un corchete")
            nombre = self.s[self.i : fin]
            self.i = fin + 1
            return nombre
        match = re.match(r"[A-Za-z_\u00C0-\u024F][A-Za-z0-9_\u00C0-\u024F]*", self.s[self.i :])
        if not match:
            return None
        self.i += match.end()
        return match.group(0)

    def number(self) -> str | None:
        self.skip()
        match = re.match(r"\d+(?:\.\d+)?", self.s[self.i :])
        if not match:
            return None
        self.i += match.end()
        return match.group(0)

    def string(self) -> str | None:
        self.skip()
        cita = self.peek()
        if cita not in "'\"":
            return None
        self.i += 1
        chars: list[str] = []
        while self.i < self.n:
            ch = self.s[self.i]
            if ch == cita:
                if cita == "'" and self.i + 1 < self.n and self.s[self.i + 1] == "'":
                    chars.append("'")
                    self.i += 2
                    continue
                self.i += 1
                return "".join(chars)
            chars.append(ch)
            self.i += 1
        raise ScanError("falta el cierre de una cadena")

    def consume(self, token: str) -> None:
        self.skip()
        if not self.s.startswith(token, self.i):
            raise ScanError(f"se esperaba {token!r}")
        self.i += len(token)

    def op2(self) -> str | None:
        self.skip()
        for operador in ("<>", "<=", ">=", "!="):
            if self.s.startswith(operador, self.i):
                self.i += len(operador)
                return "<>" if operador == "!=" else operador
        ch = self.peek()
        if len(ch) == 1 and ch in "=<>":
            self.i += 1
            return ch
        return None
