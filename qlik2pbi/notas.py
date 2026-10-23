"""Avisos e informaciones que acompañan a una conversión."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Nota:
    nivel: str  # "aviso" o "info"
    codigo: str
    mensaje: str

    def linea(self) -> str:
        prefijo = "AVISO" if self.nivel == "aviso" else "INFO"
        return f"[{prefijo}] {self.mensaje}"


@dataclass
class Notas:
    items: list[Nota] = field(default_factory=list)

    def aviso(self, codigo: str, mensaje: str) -> None:
        if any(n.codigo == codigo and n.mensaje == mensaje for n in self.items):
            return
        self.items.append(Nota("aviso", codigo, mensaje))

    def info(self, codigo: str, mensaje: str) -> None:
        if any(n.codigo == codigo and n.mensaje == mensaje for n in self.items):
            return
        self.items.append(Nota("info", codigo, mensaje))

    def avisos(self) -> list[Nota]:
        return [n for n in self.items if n.nivel == "aviso"]

    def infos(self) -> list[Nota]:
        return [n for n in self.items if n.nivel == "info"]
