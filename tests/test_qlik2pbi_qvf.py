"""El .qvf de Qlik Sense se lee tal cual, sin exportar la app a JSON."""

from __future__ import annotations

import io
import json
import struct
import urllib.request
import zipfile
import zlib
from pathlib import Path

import pytest

from qlik2pbi.exportar import leer_exportacion
from qlik2pbi.notas import Notas
from qlik2pbi.pagina import html_convertidor
from qlik2pbi.pipeline import generar
from qlik2pbi.qvf import JS_EXTRAER, MENSAJE_QVF_DANADO, SOBRE, extraer_objetos
from qlik2pbi.validar import validar_directorio

SECRETO = b"FILA_SECRETA_NO_ENVIAR"
MUESTRA = Path("/tmp/qvf-samples/qlik-dev-exec-dashboard.qvf")
URL_MUESTRA = (
    "https://raw.githubusercontent.com/qlik-oss/qlik-cloud-examples/main/"
    "qlik.dev/sample-apps/qlik-dev-exec-dashboard.qvf"
)
SCRIPT = """
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


def _registro(tipo: str, carga: bytes, declarado: int | None = None) -> bytes:
    marcador = ('{"format":"%s"}' % tipo).encode()
    return marcador + b"\x00" + struct.pack("<II", declarado if declarado is not None else len(carga), len(carga)) + carga


def _gz(objeto: dict) -> bytes:
    plano = json.dumps(objeto, ensure_ascii=False).encode()
    return _registro("gzjson", zlib.compress(plano), len(plano))


def qvf_de_prueba() -> bytes:
    script = "Ventas:\nLOAD ClienteID, Importe INLINE [\nClienteID, Importe\n1, 100\n];\n"
    hoja = {
        "qMetaData": {"qType": "sheet"},
        "qRoot": {
            "qProperty": {
                "qInfo": {"qId": "hoja1", "qType": "sheet"},
                "qMetaDef": {"title": "Ventas"},
            },
            "qChildren": [
                {
                    "qProperty": {
                        "qInfo": {"qId": "barra", "qType": "barchart"},
                        "visualization": "barchart",
                        "title": "Importe por cliente",
                        "qHyperCubeDef": {
                            "qDimensions": [{"qLibraryId": "dimNombre", "qDef": {"qFieldDefs": []}}],
                            "qMeasures": [{"qLibraryId": "medIngresos", "qDef": {}}],
                        },
                    },
                    "qChildren": [],
                },
                {
                    "qProperty": {
                        "qInfo": {"qId": "filtro", "qType": "filterpane"},
                        "visualization": "filterpane",
                        "qMetaDef": {"title": "Filtros"},
                    },
                    "qChildren": [
                        {
                            "qProperty": {
                                "qInfo": {"qId": "lista", "qType": "listbox"},
                                "visualization": "listbox",
                                "qListObjectDef": {"qLibraryId": "dimNombre", "qDef": {"qFieldDefs": []}},
                            },
                            "qChildren": [],
                        }
                    ],
                },
                {
                    "qProperty": {
                        "visualization": "text",
                        "title": "Un texto que no es un gráfico",
                    },
                    "qChildren": [],
                },
            ],
        },
    }
    binario = _registro("binary", b"\x00\x01" + SECRETO + b'{"format":"gzjson"}\x00' + b"\xff" * 3000)
    piezas = [
        b"\x00" * 16 + b'{"FileFormat":12000,"FileType":1}\x00' + SECRETO,
        _gz({"qScript": script}),
        binario,
        _gz({"qTitle": "Libreria", "qLastReloadTime": "2024-01-01T00:00:00.000Z"}),
        _gz(
            {
                "qInfo": {"qId": "medIngresos", "qType": "measure"},
                "qMeasure": {"qLabel": "Ingresos", "qDef": "Sum(Importe)"},
                "qMetaDef": {"title": "Ingresos"},
            }
        ),
        _gz(
            {
                "qInfo": {"qId": "dimNombre", "qType": "dimension"},
                "qDim": {"qFieldDefs": ["Nombre"], "qFieldLabels": ["Cliente"]},
                "qMetaDef": {"title": "Cliente"},
            }
        ),
        _gz(
            {
                "qId": "user_variablelist",
                "qEntryList": [
                    {"qProperties": {"qInfo": {"qType": "variable"}, "qName": "vAnio", "qDefinition": "2024"}}
                ],
            }
        ),
        _gz(hoja),
        _gz(
            {
                "qInfo": {"qId": "kpi", "qType": "kpi"},
                "visualization": "kpi",
                "title": "Ingresos",
                "qHyperCubeDef": {"qMeasures": [{"qDef": {"qDef": "Sum(Importe)"}}]},
            }
        ),
    ]
    return b"".join(piezas)


def _con_node(qvf: bytes, tmp_path: Path) -> list[dict]:
    destino = tmp_path / "app.qvf"
    destino.write_bytes(qvf)
    programa = tmp_path / "extraer.js"
    programa.write_text(
        JS_EXTRAER
        + """
const fs = require("fs");
(async () => {
  const bytes = new Uint8Array(fs.readFileSync(process.argv[2]));
  const objetos = await extraerObjetosQvf(bytes);
  process.stdout.write(JSON.stringify(objetos));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
""",
        encoding="utf-8",
    )
    import subprocess

    corrida = subprocess.run(
        ["node", str(programa), str(destino)],
        check=False,
        capture_output=True,
    )
    assert corrida.returncode == 0, corrida.stderr.decode("utf-8", "replace")
    return json.loads(corrida.stdout)


def _muestra() -> bytes:
    if MUESTRA.is_file() and MUESTRA.stat().st_size > 1_000_000:
        return MUESTRA.read_bytes()
    MUESTRA.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(URL_MUESTRA, MUESTRA)
    return MUESTRA.read_bytes()


def test_qvf_sintetico_trae_hojas_medidas_y_no_los_datos():
    datos = qvf_de_prueba()
    objetos = extraer_objetos(datos)
    texto = json.dumps(objetos)
    assert SECRETO.decode() not in texto
    assert "Sum(Importe)" in texto
    leido = leer_exportacion(datos, "Libreria.qvf", Notas())
    assert leido.nombre == "Libreria"
    assert "LOAD ClienteID" in leido.script
    assert [medida.nombre for medida in leido.medidas] == ["Ingresos"]
    assert leido.medidas[0].expresion == "Sum(Importe)"
    assert leido.dimensiones == ["Nombre"]
    assert [variable.nombre for variable in leido.variables] == ["vAnio"]
    assert len(leido.hojas) == 1
    visuales = leido.hojas[0].visuales
    assert [visual.tipo for visual in visuales] == ["barchart", "listbox"]
    assert visuales[0].dimensiones == ["Nombre"]
    assert visuales[0].medidas == ["Ingresos"]
    assert visuales[1].dimensiones == ["Nombre"]
    assert all(visual.titulo != "Un texto que no es un gráfico" for visual in visuales)


def test_qvf_sintetico_genera_un_pbip(tmp_path: Path):
    resultado = generar(SCRIPT, qvf_de_prueba(), "Libreria.qvf", tmp_path)
    assert validar_directorio(tmp_path) == []
    assert resultado.modelo.relaciones
    tipos = {visual.tipo for pagina in resultado.modelo.paginas for visual in pagina.visuales}
    assert "clusteredBarChart" in tipos
    assert "slicer" in tipos
    assert any(medida.nombre == "Ingresos" for medida in resultado.modelo.medidas)
    informe = (tmp_path / "INFORME.md").read_text(encoding="utf-8")
    assert "vAnio" in informe
    assert "FILA_SECRETA" not in informe


def test_el_script_sale_del_qvf_si_no_hay_qvs(tmp_path: Path):
    qvf = _gz({"qScript": SCRIPT}) + _gz(
        {
            "qTitle": "Mini",
            "qLastReloadTime": "2024-01-01T00:00:00.000Z",
            "qMetaData": {"qType": "sheet"},
            "qRoot": {
                "qProperty": {"qInfo": {"qType": "sheet"}, "qMetaDef": {"title": "Uno"}},
                "qChildren": [
                    {
                        "qProperty": {
                            "visualization": "barchart",
                            "title": "Barras",
                            "qHyperCubeDef": {
                                "qDimensions": [{"qDef": {"qFieldDefs": ["Nombre"]}}],
                                "qMeasures": [{"qDef": {"qDef": "Sum(Importe)"}}],
                            },
                        }
                    },
                    {
                        "qProperty": {
                            "visualization": "kpi",
                            "title": "Total",
                            "qHyperCubeDef": {"qMeasures": [{"qDef": {"qDef": "Sum(Importe)"}}]},
                        }
                    },
                ],
            },
        }
    )
    qvf = b"\x00" * 8 + b'{"FileFormat":12000}\x00' + qvf
    resultado = generar("", qvf, "Mini.qvf", tmp_path)
    assert validar_directorio(tmp_path) == []
    assert any("script de carga se ha leído del .qvf" in nota.mensaje for nota in resultado.notas.infos())


def test_qvf_danado_pide_json_solo_como_ultimo_recurso():
    notas = Notas()
    with pytest.raises(ValueError) as exc:
        leer_exportacion(b"\x00\x01esto no es un qvf", "Visados.qvf", notas)
    assert str(exc.value) == MENSAJE_QVF_DANADO
    assert "ZIP" not in str(exc.value)
    assert "qlik app unbuild" in str(exc.value)


def test_el_navegador_extrae_los_mismos_objetos(tmp_path: Path):
    qvf = qvf_de_prueba()
    python = extraer_objetos(qvf)
    nodo = _con_node(qvf, tmp_path)
    assert len(nodo) == len(python)
    assert nodo[0]["qScript"] == python[0]["qScript"]
    assert SECRETO.decode() not in json.dumps(nodo)


def test_la_web_convierte_el_sobre_de_metadatos(monkeypatch: pytest.MonkeyPatch):
    from qlik2pbi.acceso import emitir
    from qlik2pbi.http_api import procesar_peticion

    monkeypatch.setenv("ACCESS_CODE", "codigo-de-prueba")
    monkeypatch.setenv("SESSION_SECRET", "secreto-de-sesion-de-prueba")
    cookie = "qlik2pbi_sesion=" + emitir("codigo-de-prueba", "secreto-de-sesion-de-prueba")
    objetos = extraer_objetos(qvf_de_prueba())
    sobre = json.dumps({"qlik2pbi": SOBRE, "fichero": "Libreria.qvf", "objetos": objetos}).encode()
    cuerpo, content_type = _multipart(
        [
            ("script", "carga.qvs", SCRIPT.encode()),
            ("export", "Libreria.qvf.metadatos.json", sobre),
        ]
    )
    status, cabeceras, carga = procesar_peticion(
        "POST",
        "/api/convert",
        {"Content-Type": content_type, "Cookie": cookie},
        cuerpo,
    )
    assert status == 200, carga
    assert cabeceras["Content-Type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(carga)) as paquete:
        assert "INFORME.md" in paquete.namelist()
    pagina = procesar_peticion("GET", "/", {"Cookie": cookie}, b"")
    texto = pagina[2].decode()
    assert 'accept=".qvf"' in texto
    assert "App de Qlik Sense" in texto
    assert "El .qvf no es un ZIP" not in texto
    assert "extraerObjetosQvf" in texto
    assert MENSAJE_QVF_DANADO in texto


def test_qvf_real_de_qlik_sense(tmp_path: Path):
    datos = _muestra()
    assert datos[:2] != b"PK"
    objetos = extraer_objetos(datos)
    assert len(objetos) >= 50
    leido = leer_exportacion(datos, "qlik-dev-exec-dashboard.qvf", Notas())
    assert "qlik-dev" in leido.nombre
    assert "SET ThousandSep" in leido.script
    assert len(leido.medidas) >= 20
    assert any("Sum(" in medida.expresion for medida in leido.medidas)
    assert len(leido.hojas) >= 3
    assert any(visual.medidas for hoja in leido.hojas for visual in hoja.visuales)
    assert any(visual.dimensiones for hoja in leido.hojas for visual in hoja.visuales)
    assert any(variable.nombre == "vShowChart" for variable in leido.variables)
    tipos = {visual.tipo for hoja in leido.hojas for visual in hoja.visuales}
    assert {"barchart", "linechart", "piechart", "listbox"} <= tipos
    nodo = _con_node(datos, tmp_path)
    assert len(nodo) == len(objetos)
    assert any(isinstance(objeto.get("qScript"), str) and "SET ThousandSep" in objeto["qScript"] for objeto in nodo)
    resultado = generar(leido.script, datos, "qlik-dev-exec-dashboard.qvf", tmp_path / "pbip")
    assert validar_directorio(tmp_path / "pbip") == []
    assert resultado.modelo.relaciones
    assert len(resultado.modelo.paginas) >= 3
    assert len(resultado.modelo.medidas) >= 20


def test_la_pagina_no_pide_un_json():
    texto = html_convertidor()
    assert "App de Qlik Sense (.qvf)" in texto
    assert 'accept=".json' not in texto
    assert "Exportación de la app" not in texto


def _multipart(campos: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    frontera = "----qlik2pbiQvf"
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
