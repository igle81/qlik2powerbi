"""El ejemplo inventado de la librería produce un PBIP válido, en local."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from qlik2pbi.cli import main
from qlik2pbi.http_api import procesar_peticion
from qlik2pbi.script import analizar_script
from qlik2pbi.validar import validar_directorio

RAIZ = Path(__file__).resolve().parents[1]
EJEMPLO = RAIZ / "ejemplos" / "ventas"
PBIP = EJEMPLO / "pbip"


def _texto(carpeta: Path) -> str:
    return "\n".join(
        ruta.read_text(encoding="utf-8")
        for ruta in sorted(carpeta.rglob("*"))
        if ruta.is_file() and ruta.suffix.lower() in {".tmdl", ".json", ".md", ".pbir", ".pbism", ".pbip"}
    )


def _arbol(carpeta: Path) -> dict[str, str]:
    return {
        ruta.relative_to(carpeta).as_posix(): ruta.read_text(encoding="utf-8")
        for ruta in sorted(carpeta.rglob("*"))
        if ruta.is_file()
    }


def test_pbip_de_ejemplo_es_valido():
    assert validar_directorio(PBIP) == []


def test_tmdl_traduce_carga_y_medidas():
    texto = _texto(PBIP / "Ventas_demo.SemanticModel")
    for fragmento in (
        "Table.NestedJoin",
        "Table.Combine",
        "Csv.Document",
        "#table",
        "Date.Year",
        'Sql.Database("SERVIDOR", "BASEDATOS"',
        "isHidden",
        "CALCULATE(SUM('Ventas'[Importe]), 'Ventas'[Anio] = 2024)",
        "SUMX(VALUES('Clientes'[Cliente])",
        "DISTINCTCOUNT('Ventas'[PedidoID])",
        "REMOVEFILTERS('Ventas'[Anio])",
        "/* REVISAR: Above(Sum(Importe)) */ BLANK()",
    ):
        assert fragmento in texto, fragmento


def test_informe_tiene_pagina_con_varios_graficos():
    paginas = json.loads(
        (PBIP / "Ventas_demo.Report" / "definition" / "pages" / "pages.json").read_text(encoding="utf-8")
    )
    assert paginas["pageOrder"]
    tipos = set()
    for visual in (PBIP / "Ventas_demo.Report").rglob("visual.json"):
        dato = json.loads(visual.read_text(encoding="utf-8"))
        tipos.add(dato["visual"]["visualType"])
        posicion = dato["position"]
        assert isinstance(posicion["x"], int)
        assert isinstance(posicion["y"], int)
    assert tipos == {"card", "clusteredBarChart", "lineChart", "pieChart", "tableEx", "slicer"}
    informe = json.loads((PBIP / "Ventas_demo.Report" / "definition" / "report.json").read_text(encoding="utf-8"))
    assert "report" in informe["$schema"]


def test_informe_lista_los_avisos_del_ejemplo():
    texto = (PBIP / "INFORME.md").read_text(encoding="utf-8")
    assert "Above" in texto
    assert "SERVIDOR" in texto
    assert "variable $()" in texto
    assert "ApplyMap" in texto
    assert "Sum(Aggr" in texto or "Aggr" in texto


def test_tablas_md_describe_origen_orden_y_power_query(tmp_path: Path):
    codigo = main(
        [
            "--script",
            str(EJEMPLO / "carga.qvs"),
            "--export",
            str(EJEMPLO / "app.json"),
            "--out",
            str(tmp_path),
        ]
    )
    assert codigo == 0
    texto = (tmp_path / "TABLAS.md").read_text(encoding="utf-8")
    assert "Tablas que hay que importar" in texto
    assert "CarpetaDatos" in texto
    assert "lib://Datos/clientes.csv" in texto
    assert "lib://Datos/historico.csv" in texto
    assert "ApplyMap('MapaRegion')" in texto
    assert "WHERE Cantidad > 0" in texto
    assert "LEFT JOIN por PedidoID" in texto
    assert "`historico.csv`" in texto
    assert "CONCATENATE" in texto
    assert "SELECT" in texto
    assert "SERVIDOR" in texto
    assert "PedidoID" in texto
    assert "hechos" in texto
    assert "dimensión" in texto
    orden = [linea for linea in texto.splitlines() if linea[:2].rstrip(".").isdigit() and linea.startswith(tuple("123456789"))]
    nombres = [linea.split("`")[1] for linea in orden]
    assert nombres.index("MapaRegion") < nombres.index("Clientes")
    assert nombres.index("VentasSQL") < nombres.index("Ventas")
    assert nombres.index("Ventas") < nombres.index("Historico")
    assert "TABLAS.md" in _arbol(tmp_path)


def test_cli_regenera_el_mismo_proyecto(tmp_path: Path):
    codigo = main(
        [
            "--script",
            str(EJEMPLO / "carga.qvs"),
            "--export",
            str(EJEMPLO / "app.json"),
            "--out",
            str(tmp_path),
        ]
    )
    assert codigo == 0
    assert validar_directorio(tmp_path) == []
    assert _arbol(tmp_path) == _arbol(PBIP)


def test_group_by_pasa_a_table_group():
    modelo, notas = analizar_script(
        """
        T:
        LOAD A, B INLINE [
        A, B
        1, 10
        1, 20
        ];
        G:
        LOAD A, Sum(B) as Total
        RESIDENT T
        GROUP BY A;
        """
    )
    consulta = next(tabla.m() for tabla in modelo.tablas if tabla.nombre == "G")
    assert 'Table.Group(Origen, {"A"}, {{"Total", each List.Sum([B]), type number}})' in consulta
    assert any("GROUP BY" in nota.mensaje for nota in notas.infos())


def test_exportacion_xml_genera_pbip(tmp_path: Path):
    from qlik2pbi.pipeline import generar

    script = """
    Clientes:
    LOAD ClienteID, Nombre INLINE [
    ClienteID, Nombre
    1, Ana
    ];
    Ventas:
    LOAD PedidoID, ClienteID, Importe INLINE [
    PedidoID, ClienteID, Importe
    10, 1, 100
    ];
    """
    xml = """<app name="Mini">
      <sheet name="Uno">
        <object type="barchart">
          <dimension>Nombre</dimension>
          <measure>Sum(Importe)</measure>
        </object>
        <object type="kpi">
          <measure>Sum(Importe)</measure>
        </object>
      </sheet>
    </app>"""
    resultado = generar(script, xml.encode(), "app.xml", tmp_path)
    assert validar_directorio(tmp_path) == []
    assert resultado.modelo.relaciones
    tipos = {visual.tipo for pagina in resultado.modelo.paginas for visual in pagina.visuales}
    assert "clusteredBarChart" in tipos
    assert "card" in tipos


def test_web_devuelve_zip_en_memoria(monkeypatch: pytest.MonkeyPatch):
    from qlik2pbi.acceso import reiniciar_intentos

    monkeypatch.setenv("ACCESS_CODE", "codigo-de-prueba")
    monkeypatch.setenv("SESSION_SECRET", "secreto-de-sesion-de-prueba")
    reiniciar_intentos()
    entrada = procesar_peticion(
        "POST",
        "/api/acceso",
        {"Content-Type": "application/x-www-form-urlencoded", "X-Real-IP": "198.51.100.20"},
        b"codigo=codigo-de-prueba",
    )
    assert entrada[0] == 303
    cookie = entrada[1]["Set-Cookie"].split(";", 1)[0]
    assert "codigo-de-prueba" not in entrada[1]["Set-Cookie"]
    cuerpo, content_type = _multipart(
        [
            ("script", "carga.qvs", (EJEMPLO / "carga.qvs").read_bytes()),
            ("export", "app.json", (EJEMPLO / "app.json").read_bytes()),
        ]
    )
    status, cabeceras, carga = procesar_peticion(
        "POST",
        "/api/convert",
        {"Content-Type": content_type, "Cookie": cookie},
        cuerpo,
    )
    assert status == 200
    assert cabeceras["Content-Type"] == "application/zip"
    assert cabeceras["Cache-Control"] == "no-store"
    assert cabeceras["X-Robots-Tag"] == "noindex, nofollow"
    assert cabeceras["X-Avisos"] == "3"
    assert cabeceras["Content-Disposition"].startswith('attachment; filename="')
    with zipfile.ZipFile(io.BytesIO(carga)) as paquete:
        nombres = set(paquete.namelist())
    assert "Ventas_demo.pbip" in nombres
    assert any(nombre.endswith("report.json") for nombre in nombres)
    assert any(nombre.endswith("Ventas.tmdl") for nombre in nombres)
    assert "INFORME.md" in nombres
    pagina = procesar_peticion("GET", "/", {"Cookie": cookie}, b"")
    assert pagina[0] == 200
    texto = pagina[2].decode("utf-8")
    assert "Power BI" in texto
    assert "Generar y descargar ZIP" in texto
    assert 'name="robots" content="noindex, nofollow"' in texto


def test_readme_avisa_de_la_licencia_y_la_cobertura():
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    for fragmento in (
        "cyphou/Qlik-To-PowerBI",
        "sin licencia",
        "uso comercial",
        "Sum",
        "ApplyMap",
        "Above",
        "Aggr",
        "Set Analysis",
        "Join",
        "Concatenate",
        "Resident",
        "qlik2pbi --script",
        "ACCESS_CODE",
        "SESSION_SECRET",
        "powerbi.avilesia.es",
        "noindex",
        "12 horas",
    ):
        assert fragmento in readme


def _multipart(campos: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    frontera = "----qlik2pbiEjemplo"
    trozos: list[bytes] = []
    for nombre, fichero, datos in campos:
        trozos.append(
            (
                f"--{frontera}\r\n"
                f'Content-Disposition: form-data; name="{nombre}"; filename="{fichero}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
            + datos
            + b"\r\n"
        )
    trozos.append(f"--{frontera}--\r\n".encode())
    return b"".join(trozos), f"multipart/form-data; boundary={frontera}"


def test_peticion_sin_ficheros_responde_400(monkeypatch: pytest.MonkeyPatch):
    from qlik2pbi.acceso import emitir

    monkeypatch.setenv("ACCESS_CODE", "codigo-de-prueba")
    monkeypatch.setenv("SESSION_SECRET", "secreto-de-sesion-de-prueba")
    cookie = "qlik2pbi_sesion=" + emitir("codigo-de-prueba", "secreto-de-sesion-de-prueba")
    status, _, carga = procesar_peticion(
        "POST",
        "/api/convert",
        {"Content-Type": "text/plain", "Cookie": cookie},
        b"",
    )
    assert status == 400
    assert "error" in json.loads(carga)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
