"""Línea de comandos: qlik2pbi --script carga.qvs --export app.json --out ./salida"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qlik2pbi import __version__
from qlik2pbi.pipeline import empaquetar, generar


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qlik2pbi",
        description=(
            "Genera un proyecto de Power BI (PBIP) a partir del script de carga de Qlik "
            "y de la exportación de hojas y objetos. Todo ocurre en local."
        ),
    )
    parser.add_argument("--script", required=True, help="Script de carga (.qvs o texto).")
    parser.add_argument("--export", required=True, help="Exportación de la app (.json, .xml o .qvf).")
    parser.add_argument("--out", required=True, help="Carpeta de salida del PBIP.")
    parser.add_argument("--nombre", help="Nombre del proyecto. Por defecto sale del título de la app.")
    parser.add_argument("--zip", action="store_true", help="Escribe también un ZIP del PBIP dentro de la carpeta de salida.")
    parser.add_argument("--version", action="version", version=f"qlik2pbi {__version__}")
    args = parser.parse_args(argv)

    script_path = Path(args.script)
    export_path = Path(args.export)
    if not script_path.is_file():
        print(f"No se encuentra el script: {script_path}", file=sys.stderr)
        return 2
    if not export_path.is_file():
        print(f"No se encuentra la exportación: {export_path}", file=sys.stderr)
        return 2
    try:
        script = script_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        script = script_path.read_text(encoding="cp1252")
    try:
        resultado = generar(
            script,
            export_path.read_bytes(),
            export_path.name,
            Path(args.out),
            nombre=args.nombre,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.zip:
        destino = resultado.carpeta / f"{resultado.nombre}.zip"
        destino.write_bytes(empaquetar(resultado.carpeta))
        # El zip no debe incluirse a sí mismo. Se reescribe sin el propio archivo.
        destino.write_bytes(_zip_sin_propio(resultado.carpeta, destino.name))

    print(f"Proyecto: {resultado.proyecto}")
    print(f"Tablas: {len(resultado.modelo.tablas)}")
    print(f"Relaciones: {len(resultado.modelo.relaciones)}")
    print(f"Medidas: {len(resultado.modelo.medidas)}")
    print(f"Páginas: {len(resultado.modelo.paginas)}")
    print(f"Gráficos: {sum(len(p.visuales) for p in resultado.modelo.paginas)}")
    print(f"Avisos: {len(resultado.notas.avisos())}")
    print(f"Informe: {resultado.carpeta / 'INFORME.md'}")
    for nota in resultado.notas.avisos():
        print(f"  - {nota.mensaje}")
    return 0


def _zip_sin_propio(carpeta: Path, propio: str) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as paquete:
        for ruta in sorted(carpeta.rglob("*")):
            if ruta.is_file() and ruta.name != propio:
                paquete.write(ruta, ruta.relative_to(carpeta).as_posix())
    return buf.getvalue()


if __name__ == "__main__":
    raise SystemExit(main())
