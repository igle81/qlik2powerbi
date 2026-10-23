"""El SQL SELECT toma el nombre de la etiqueta o de la tabla del FROM."""

from __future__ import annotations

from qlik2pbi.pipeline import generar
from qlik2pbi.script import analizar_script

XML = """<app name="Visados">
  <sheet name="Panel">
    <object type="kpi" title="Total"><measure>Sum(Importe)</measure></object>
    <object type="table" title="Detalle">
      <dimension>Codigo</dimension>
      <measure>Sum(Importe)</measure>
    </object>
  </sheet>
</app>"""


def _nombres(script: str) -> list[str]:
    modelo, _notas = analizar_script(script)
    return [tabla.nombre for tabla in modelo.tablas]


def test_sin_etiqueta_usa_la_tabla_del_from():
    nombres = _nombres(
        """
        SQL SELECT Codigo, Importe
        FROM dbo.[Visados];
        """
    )
    assert nombres == ["Visados"]
    modelo, _notas = analizar_script("SQL SELECT Codigo FROM dbo.[Visados];")
    assert "dbo.Visados" in modelo.tablas[0].ficha.texto
    assert "ConsultaSQL" not in modelo.tablas[0].ficha.texto


def test_la_etiqueta_tiene_prioridad_sobre_el_from():
    nombres = _nombres(
        """
        Pedidos:
        SQL SELECT * FROM dbo.[Visados];
        """
    )
    assert nombres == ["Pedidos"]


def test_con_join_se_queda_la_primera_tabla(tmp_path):
    script = """
    SQL SELECT
        Codigo, -- clave
        Importe
    /* FROM dbo.Falsa */
    FROM
        dbo.[Visados] v
    INNER JOIN dbo.Estados e ON e.Id = v.EstadoId
    WHERE Importe > 0;
    """
    assert _nombres(script) == ["Visados"]
    resultado = generar(script, XML.encode(), "app.xml", tmp_path)
    assert resultado.modelo.tablas[0].nombre == "Visados"
    texto = "\n".join(ruta.read_text(encoding="utf-8") for ruta in tmp_path.rglob("*.tmdl"))
    assert "table Visados" in texto
    assert "partition Visados = m" in texto
    assert "ref table Visados" in texto
    assert "ConsultaSQL" not in texto
    tablas = (tmp_path / "TABLAS.md").read_text(encoding="utf-8")
    assert "`Visados`" in tablas
    assert "`dbo.Visados`" in tablas
    assert "ConsultaSQL" not in tablas


def test_sql_duplicado_anade_sufijo():
    nombres = _nombres(
        """
        SQL SELECT a FROM dbo.Visados;
        SQL SELECT b FROM [dbo].[Visados];
        """
    )
    assert nombres == ["Visados", "Visados_2"]


def test_sanea_el_nombre_para_tmdl():
    nombres = _nombres(
        """
        SQL SELECT * FROM [dbo].[Visados 2024];
        Año raro:
        SQL SELECT * FROM dbo.Otra;
        """
    )
    assert nombres[0] == "Visados_2024"
    assert nombres[1] == "Año_raro"
    modelo, _notas = analizar_script('SQL SELECT * FROM [dbo].[Visados 2024];')
    assert "dbo.Visados 2024" in modelo.tablas[0].ficha.texto


def test_sin_from_sigue_siendo_consulta_sql():
    assert _nombres("SQL SELECT 1 AS Valor;") == ["ConsultaSQL"]
