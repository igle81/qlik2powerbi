"""El error de la web es el fallo real, y un .qvs o un .qvf incompleto no detienen la conversión."""

from __future__ import annotations

import json

import pytest

from qlik2pbi.exportar import leer_exportacion
from qlik2pbi.notas import Notas
from qlik2pbi.pagina import html_convertidor
from qlik2pbi.pipeline import empaquetar, generar
from qlik2pbi.qvf import SOBRE
from qlik2pbi.script import Constructor, analizar_script, decodificar_script

SCRIPT = "Visados:\nLOAD Código, Importe INLINE [\nCódigo, Importe\n1, 10\n];\n"
XML = """<app name="Visados">
  <sheet name="Panel">
    <object type="kpi" title="Total"><measure>Sum(Importe)</measure></object>
    <object type="barchart" title="Por código">
      <dimension>Código</dimension>
      <measure>Sum(Importe)</measure>
    </object>
  </sheet>
</app>"""


def test_script_utf16_le_con_bom_genera_el_pbip(tmp_path):
    datos = SCRIPT.encode("utf-16")
    assert datos.startswith(b"\xff\xfe")
    assert "Código" in decodificar_script(datos)
    resultado = generar(decodificar_script(datos), XML.encode(), "Visados.xml", tmp_path)
    texto = "\n".join(ruta.read_text(encoding="utf-8") for ruta in tmp_path.rglob("*.tmdl"))
    assert "Código" in texto
    assert resultado.modelo.nombre_app == "Visados"
    assert (tmp_path / "INFORME.md").read_text(encoding="utf-8").count("relaciones") >= 1


def test_script_cp1252_conserva_las_tildes(tmp_path):
    datos = SCRIPT.encode("cp1252")
    assert decodificar_script(datos) == SCRIPT
    resultado = generar(decodificar_script(datos), XML.encode(), "app.xml", tmp_path)
    assert any(col.nombre == "Código" for tabla in resultado.modelo.tablas for col in tabla.columnas)


def test_sin_relaciones_se_entrega_el_zip_con_aviso(tmp_path):
    resultado = generar(SCRIPT, XML.encode(), "app.xml", tmp_path)
    assert not resultado.modelo.relaciones
    informe = (tmp_path / "INFORME.md").read_text(encoding="utf-8")
    assert "No se han inferido relaciones" in informe
    assert "no siempre se llaman ID" in informe
    paquete = empaquetar(tmp_path)
    assert paquete.startswith(b"PK")


def test_qvf_con_objetos_parciales_sigue(tmp_path):
    objetos = [
        None,
        "basura",
        {"qTitle": "Visados", "qLastReloadTime": "2020-01-01"},
        {"qInfo": {"qType": "measure", "qId": "m1"}, "qMeasure": {"qDef": None}, "qMetaDef": {"title": "Rota"}},
        {
            "qInfo": {"qType": "measure", "qId": "m2"},
            "qMeasure": {"qDef": "Sum(Importe)"},
            "qMetaDef": {"title": "Total"},
        },
        {
            "qMetaData": {"qType": "sheet"},
            "qRoot": {
                "qProperty": {"qMetaDef": {"title": 12}, "qInfo": {"qType": "sheet"}},
                "qChildren": None,
            },
        },
        {
            "qMetaData": {"qType": "sheet"},
            "qRoot": {
                "qProperty": {"qMetaDef": {"title": "Detalle"}, "qInfo": {"qType": "sheet"}},
                "qChildren": [
                    {
                        "qProperty": {
                            "visualization": "table",
                            "title": "Años",
                            "qHyperCubeDef": {
                                "qDimensions": [None, {"qDef": {"qFieldDefs": ["Año"]}}],
                                "qMeasures": [None, {"qDef": {"qDef": "Sum(Importe)"}}],
                            },
                        }
                    }
                ],
            },
        },
    ]
    sobre = json.dumps({"qlik2pbi": SOBRE, "fichero": "Visados.qvf", "objetos": objetos}).encode()
    notas = Notas()
    datos = leer_exportacion(sobre, "Visados.qvf.metadatos.json", notas)
    assert datos.nombre == "Visados"
    assert any(medida.nombre == "Total" for medida in datos.medidas)
    assert any(hoja.titulo == "Detalle" for hoja in datos.hojas)
    assert any("no son un diccionario" in nota.mensaje for nota in notas.avisos())
    resultado = generar(SCRIPT, sobre, "Visados.qvf.metadatos.json", tmp_path)
    assert resultado.modelo.nombre_app == "Visados"
    titulos = [visual.titulo for pagina in resultado.modelo.paginas for visual in pagina.visuales]
    campos = [
        proy.nombre
        for pagina in resultado.modelo.paginas
        for visual in pagina.visuales
        for rol in visual.roles.values()
        for proy in rol
    ]
    assert "Año" in campos or any("Año" in nota.mensaje for nota in resultado.notas.avisos())
    assert titulos


def test_una_sentencia_rota_no_borra_el_resto(monkeypatch: pytest.MonkeyPatch):
    original = Constructor._nueva_tabla

    def romper(self, carga):
        if carga.etiqueta == "Rota":
            raise RuntimeError("columna inesperada")
        return original(self, carga)

    monkeypatch.setattr(Constructor, "_nueva_tabla", romper)
    modelo, notas = analizar_script(
        "Rota:\nLOAD A INLINE [\nA\n1\n];\n" + SCRIPT
    )
    assert any(tabla.nombre == "Visados" for tabla in modelo.tablas)
    assert any("columna inesperada" in nota.mensaje for nota in notas.avisos())


def test_la_web_devuelve_el_error_real(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    from qlik2pbi.acceso import emitir
    from qlik2pbi.http_api import procesar_peticion

    monkeypatch.setenv("ACCESS_CODE", "codigo-de-prueba")
    monkeypatch.setenv("SESSION_SECRET", "secreto-de-sesion-de-prueba")

    def fallar(*_args, **_kwargs):
        raise RuntimeError("La medida Ventas.Total tiene paréntesis desequilibrados.")

    monkeypatch.setattr("qlik2pbi.http_api.generar", fallar)
    cookie = "qlik2pbi_sesion=" + emitir("codigo-de-prueba", "secreto-de-sesion-de-prueba")
    cuerpo, content_type = _multipart(
        [
            ("script", "sciprt1.qvs", SCRIPT.encode("utf-16")),
            ("export", "Visados.qvf.metadatos.json", XML.encode()),
        ]
    )
    status, _, carga = procesar_peticion(
        "POST",
        "/api/convert",
        {"Content-Type": content_type, "Cookie": cookie},
        cuerpo,
    )
    assert status == 400
    error = json.loads(carga)["error"]
    assert "paréntesis" in error
    assert error != "No se ha podido generar el proyecto. Revisa el script y el .qvf."
    err = capsys.readouterr().err
    assert "Traceback" in err
    assert "paréntesis" in err
    assert "sciprt1.qvs" not in err
    assert "Código" not in err
    assert "Visados.qvf.metadatos.json" in err


def test_una_excepcion_vacia_conserva_el_texto_generico(monkeypatch: pytest.MonkeyPatch):
    from qlik2pbi.acceso import emitir
    from qlik2pbi.http_api import procesar_peticion

    monkeypatch.setenv("ACCESS_CODE", "codigo-de-prueba")
    monkeypatch.setenv("SESSION_SECRET", "secreto-de-sesion-de-prueba")

    class Vacia(RuntimeError):
        def __str__(self) -> str:
            return "  "

    def vacia(*_args, **_kwargs):
        raise Vacia()

    monkeypatch.setattr("qlik2pbi.http_api.generar", vacia)
    cookie = "qlik2pbi_sesion=" + emitir("codigo-de-prueba", "secreto-de-sesion-de-prueba")
    cuerpo, content_type = _multipart(
        [("script", "carga.qvs", b"X"), ("export", "app.xml", b"<app/>")]
    )
    status, _, carga = procesar_peticion(
        "POST",
        "/api/convert",
        {"Content-Type": content_type, "Cookie": cookie},
        cuerpo,
    )
    assert status == 400
    assert json.loads(carga)["error"] == "No se ha podido generar el proyecto. Revisa el script y el .qvf."


def test_la_pagina_muestra_el_error_en_varias_lineas():
    texto = html_convertidor()
    assert "white-space: pre-wrap" in texto
    assert "cuerpo.error" in texto
    assert "HTTP " in texto


def _multipart(campos: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    frontera = "----qlik2pbiErrores"
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
