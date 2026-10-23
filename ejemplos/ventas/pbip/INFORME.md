# Informe de conversión — Ventas_demo

Generado en local por `qlik2pbi`. No se ha enviado el script ni la exportación a ningún servicio.

## Qué se ha generado

- Tablas: 6
- Relaciones: 6
- Medidas: 8
- Páginas: 1
- Gráficos: 9

Al abrir el PBIP en Power BI Desktop, ajusta el parámetro `CarpetaDatos` a la carpeta de los CSV si el script leía ficheros.

## Avisos para revisar a mano

- Hay un SQL SELECT sin CONNECT con servidor y base de datos. La consulta usa el marcador SERVIDOR / BASEDATOS. Cámbialo antes de actualizar.
- El modificador de «Anio» usa una variable $(). Sustitúyela a mano.
- Above no tiene traducción automática. Revisa la medida a mano. Original: Above(Sum(Importe))

## Transformaciones aplicadas

- Tabla de mapeo «MapaRegion» lista para ApplyMap. Queda oculta en el modelo.
- Las rutas lib:// se reducen al nombre del fichero. Ajusta el parámetro CarpetaDatos al abrir el PBIP.
- ApplyMap('MapaRegion') convertido en combinación hacia «Region».
- LEFT JOIN combinado en «Ventas» por PedidoID.
- CONCATENATE añadido a «Historico» con Table.Combine.

## Medidas

| Medida | Expresión Qlik | DAX |
| --- | --- | --- |
| Importe total | Sum(Importe) | SUM('Ventas'[Importe]) |
| Pedidos | Count(DISTINCT PedidoID) | DISTINCTCOUNT('Ventas'[PedidoID]) |
| Importe 2024 | Sum({<Anio={2024}>} Importe) | CALCULATE(SUM('Ventas'[Importe]), 'Ventas'[Anio] = 2024) |
| Ticket medio | If(Count(DISTINCT PedidoID) > 0, Sum(Importe) / Count(DISTINCT PedidoID), 0) | IF(DISTINCTCOUNT('Ventas'[PedidoID]) > 0, SUM('Ventas'[Importe]) / DISTINCTCOUNT('Ventas'[PedidoID]), 0) |
| Importe por cliente | Sum(Aggr(Sum(Importe), Cliente)) | SUMX(VALUES('Clientes'[Cliente]), CALCULATE(SUM('Ventas'[Importe]))) |
| Importe sin año | Sum({<Anio=>} Importe) | CALCULATE(SUM('Ventas'[Importe]), REMOVEFILTERS('Ventas'[Anio])) |
| Importe del variable | Sum({<Anio={$(vAnio)}>} Importe) | SUM('Ventas'[Importe]) |
| Mes anterior | Above(Sum(Importe)) | /* REVISAR: Above(Sum(Importe)) */ BLANK() |

## Relaciones

- `Ventas[ClienteID]` → `Clientes[ClienteID]` (varios a uno)
- `Historico[ClienteID]` → `Clientes[ClienteID]` (varios a uno)
- `Ventas[ProductoID]` → `Productos[ProductoID]` (varios a uno)
- `Historico[ProductoID]` → `Productos[ProductoID]` (varios a uno)
- `Ventas[PedidoID]` → `VentasSQL[PedidoID]` (varios a uno)
- `Historico[PedidoID]` → `VentasSQL[PedidoID]` (varios a uno)

## Cobertura de esta herramienta

### Se traduce

- **Sum, Count, Avg, Min, Max** → SUM, COUNT, AVERAGE, MIN, MAX. Count(DISTINCT campo) pasa a DISTINCTCOUNT.
- **Only** → SELECTEDVALUE. Un valor visible en el contexto actual.
- **If, and, or, not** → IF, &&, ||, NOT. If anidado incluido.
- **Aggr** → SUMX / VALUES o SUMMARIZE. Un nivel. Sum(Aggr(...)) no se suma dos veces. Aggr anidado queda avisado.
- **Set Analysis básico** → CALCULATE. {<Campo={valor}>}, listas, exclusión {<Campo-={v}>}, filtro vacío {<Campo=>}, {1} y {$}.
- **Year, Month, Day, Today, Now** → YEAR, MONTH, DAY, TODAY, NOW en DAX; Date.Year y similares en M. En el script de carga se aplican fila a fila.
- **Upper, Lower, Trim, Len, Round, Abs** → UPPER, LOWER, TRIM, LEN, ROUND, ABS en DAX; Text.* y Number.* en M.
- **ApplyMap** → Table.NestedJoin en M. La tabla MAPPING se conserva oculta.
- **Join (left, inner, right, outer)** → Table.NestedJoin. Las columnas del JOIN entran en la tabla destino.
- **Concatenate** → Table.Combine. Une por nombre de columna.
- **Resident** → Referencia a la otra consulta M. En Power Query la consulta ve la definición final de la tabla.
- **LOAD FROM csv/txt** → Csv.Document. El delimitador y la página de códigos salen del especificador Qlik.
- **LOAD FROM xlsx** → Excel.Workbook. Usa la hoja indicada en «table is».
- **INLINE** → #table.
- **SQL SELECT** → Sql.Database. Si no hay CONNECT, el servidor queda como marcador y se avisa.
- **WHERE** → Table.SelectRows.
- **GROUP BY** → Table.Group. Sum, Avg, Count, Min y Max sobre un campo.
- **Gráficos de barra, línea, tarta, tabla, KPI y filtro** → clusteredBarChart, lineChart, pieChart, tableEx, card, slicer. El resto de tipos pasa a tabla y se avisa.

### Queda como aviso

- **Above, Below, Before, After, Top, Bottom.** Funciones inter-registro. No hay equivalente directo en una medida.
- **Peek, Previous, Exists, RangeSum, RangeAvg.** Dependen del orden de carga o de una ventana. La medida queda en BLANK y se conserva el texto original.
- **Set Analysis con P(), E(), estados alternos (::), unión o intersección de conjuntos.** Solo se traduce el modificador de campo simple.
- **Dólar $(variable) dentro del análisis de conjuntos.** Power BI no expande variables de script. Hay que sustituir la variable a mano.
- **Aggr anidado.** Se avisa: el anidamiento de Aggr no se aplana solo.
- **Dual, Class, FirstSortedValue, Rank, Correl, Fractile.** Sin traducción fiel de una línea.
- **IntervalMatch, Crosstable, Generic, Hierarchy.** Cambian la forma de la tabla. No se reescriben.
- **QVD.** Power Query no lee QVD. Se deja un CSV homónimo y un aviso.
- **Section Access.** No se genera RLS. Hay que definir los roles en el modelo.
- **For each, bucles, subrutinas y BINARY.** El control de flujo del script no se ejecuta; solo se avisa.
- **Extendiones, mapas y gráficos no estándar.** El objeto pasa a tabla para no perder el dato, con aviso.
