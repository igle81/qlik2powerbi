# De Qlik a Power BI

`qlik2pbi` genera un proyecto de Power BI Desktop (PBIP) a partir de lo mínimo de una app de Qlik: el script de carga y la exportación de hojas y objetos. El modelo, las consultas M, las medidas DAX y las páginas del informe salen en local. No se envía nada a un servicio externo ni a una IA de pago.

## Aviso de licencia y atribución

Este repositorio es un fork de [cyphou/Qlik-To-PowerBI](https://github.com/cyphou/Qlik-To-PowerBI). El README de origen muestra una insignia MIT, pero el árbol no incluye un fichero `LICENSE` y la API de licencias de GitHub no detecta ninguna. **El código heredado está sin licencia.**

El uso comercial de las partes heredadas necesita permiso del autor o hay que reescribirlas. Esas partes siguen en el repositorio y no forman parte del camino que cubre esta entrega:

- `migrate.py`
- `qlik_export/`
- `powerbi_import/`
- `tools/`
- `src/`
- `web/app.py`
- los tests y la documentación anteriores

El paquete `qlik2pbi/`, la web (`qlik2pbi/pagina.py`, `api/`) y el ejemplo de `ejemplos/ventas/` son una reescritura de este fork. No importan el conversor heredado. Este README no reclama MIT para el repositorio completo y no se ha añadido un `LICENSE` que cubriera el código heredado.

## Qué hace la herramienta

La entrada son solo dos ficheros:

1. El script de carga (`.qvs`).
2. La app de Qlik Sense (`.qvf`).

No hace falta exportar la app a JSON. El `.qvf` de Qlik Sense (formato de fichero 12000) se lee tal cual: la cabecera trae el formato y, a continuación, bloques `gzjson` con el script, las hojas, los objetos, las medidas maestras, las dimensiones maestras y las variables. Los bloques binarios, donde van las filas de las tablas y las imágenes, no se leen. Si un `.qvf` concreto está dañado y no se pueden leer esos bloques, el mensaje indica como último recurso exportarlo a JSON (`qlik app unbuild` en Qlik Cloud) y pasarlo por la línea de comandos junto al `.qvs`.

La salida es una carpeta lista para abrir en Power BI Desktop:

```
salida/
  INFORME.md
  Nombre.pbip
  Nombre.SemanticModel/
    definition.pbism
    definition/
      database.tmdl
      model.tmdl
      relationships.tmdl
      expressions.tmdl
      tables/*.tmdl
  Nombre.Report/
    definition.pbir
    definition/report.json
    definition/pages/.../visuals/.../visual.json
```

Incluye las tablas y relaciones sacadas del script, las consultas Power Query equivalentes a los `LOAD` y `SQL SELECT`, las medidas DAX traducidas de las expresiones de Qlik y las páginas con gráficos de barras, líneas, tarta, tabla, KPI y filtros.

## Instalación

Hace falta Python 3.10 o superior. No hay dependencias de pago.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
qlik2pbi --script carga.qvs --qvf app.qvf --out ./salida
```

Sin instalar el paquete:

```bash
python3 -m qlik2pbi --script carga.qvs --qvf app.qvf --out ./salida
```

`--export` es el mismo argumento que `--qvf`. Si ese `.qvf` está dañado, `--export` también acepta el JSON o el XML de un unbuild.

`--zip` deja además `Nombre.zip` dentro de la carpeta de salida. `--nombre` fija el nombre del proyecto; si se omite, sale del título de la app.

Al abrir el PBIP, apunta el parámetro `CarpetaDatos` a la carpeta de los CSV. El ejemplo usa `ejemplos/ventas/datos`.

## Web

La página está en español y pensada para [powerbi.avilesia.es](https://powerbi.avilesia.es) en Vercel. Antes de subir ficheros o descargar el ZIP pide un código de acceso. Pide el `.qvs` y el `.qvf`, nada más. El navegador abre el `.qvf` en local, saca los bloques de metadatos y solo envía eso: hojas, objetos, medidas y dimensiones. Las filas de datos no salen del navegador. El `.qvs` puede pesar hasta 4 MB. Si los metadatos superan 4 MB, la página lo dice y la conversión se hace con la línea de comandos, que lee el `.qvf` entero en local sin ese tope. El PBIP se escribe en un directorio temporal que se borra al terminar y la respuesta lleva `Cache-Control: no-store`. No hay base de datos ni llamadas externas. Toda la web lleva `noindex` (`X-Robots-Tag`, meta robots y `public/robots.txt`).

La línea de comandos no consulta estas variables. Solo las usa la web.

### ACCESS_CODE

Es el código que hay que escribir en el formulario. Se lee en el servidor, en cada petición, desde la variable de entorno `ACCESS_CODE`. No tiene valor por defecto y no se guarda en el repositorio ni en la cookie. La comparación usa `hmac.compare_digest` sobre el resumen SHA-256 de los dos textos, así la duración no depende de en qué carácter difieren.

Si `ACCESS_CODE` falta o está vacío, la web no deja entrar a nadie: no muestra el formulario de subida y rechaza la conversión.

### SESSION_SECRET

Secreto con el que se firma la cookie `qlik2pbi_sesion` (HMAC-SHA256). También es una variable de entorno, sin valor por defecto y fuera del repositorio. Si falta, la web queda cerrada igual que sin código, porque no se puede firmar la sesión.

Con el código correcto el servidor responde una cookie `HttpOnly`, `SameSite=Lax` y, si la petición llega por HTTPS, `Secure`. Caduca a las 12 horas. La cookie lleva la caducidad y la firma, no el código. Cambiar `ACCESS_CODE` invalida las sesiones ya emitidas. En local, por HTTP, la cookie se envía sin `Secure` para poder probar en `http://127.0.0.1:8000`.

Genera un secreto en tu máquina, sin enviarlo a ningún sitio:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

En Vercel, proyecto de powerbi.avilesia.es, define `ACCESS_CODE` y `SESSION_SECRET` en Settings → Environment Variables. No las pongas en `vercel.json` ni en un fichero del repo.

### Intentos

Tras 5 códigos incorrectos desde la misma IP, en una ventana de 15 minutos, el servidor responde 429 y no comprueba más códigos hasta que pasa la ventana. El contador vive en la memoria de cada instancia. En Vercel no es un candado global: frena un intento repetido contra una instancia y no usa ningún servicio de pago. La IP sale de `X-Real-IP` o, si no viene, del último salto de `X-Forwarded-For`.

En local:

```bash
ACCESS_CODE='un-codigo' SESSION_SECRET='un-secreto-largo' python3 -m qlik2pbi.serve
```

Abre `http://127.0.0.1:8000`, entra con el código y después sube el `.qvs` y el `.qvf`.

En Vercel, el proyecto es la raíz del repositorio. `/` se reescribe a la función `api/app.py`. El código entra por `api/acceso.py` y la conversión por `api/convert.py`. Las tres usan la biblioteca estándar. `vercel.json` deja fuera del despliegue el código heredado y los ejemplos, y añade `noindex` a todas las rutas.

## Ejemplo

`ejemplos/ventas/` es una librería inventada. No hay datos reales. El script carga un mapa de regiones, clientes, productos, ventas, un `SQL SELECT` sin `CONNECT`, un `LEFT JOIN`, un `RESIDENT` y un `CONCATENATE`. El JSON del ejemplo describe la misma hoja que traería un `.qvf`: tres KPI, una barra, una línea, una tarta, una tabla y dos filtros. Sirve para regenerar el PBIP del repositorio. Un `.qvf` real de Qlik Sense entra por el mismo camino.

El PBIP ya generado está en `ejemplos/ventas/pbip` (`Ventas_demo`). Con el ejemplo salen 6 tablas, 6 relaciones, 8 medidas, 1 página y 9 gráficos. Tres avisos quedan a mano: el SQL sin servidor, una variable `$(vAnio)` y `Above`.

```bash
python3 -m qlik2pbi \
  --script ejemplos/ventas/carga.qvs \
  --export ejemplos/ventas/app.json \
  --out ./salida
```

## Pruebas

```bash
python3 -m pytest tests/test_qlik2pbi_ejemplo.py tests/test_qlik2pbi_acceso.py tests/test_qlik2pbi_qvf.py -q
```

Comprueban que el TMDL y el `report.json` del ejemplo son válidos, que la CLI regenera el mismo árbol, que `GROUP BY` pasa a `Table.Group`, que una exportación XML también genera un PBIP, que un `.qvf` de Qlik Sense (el de ejemplo sintético y la app pública `qlik-dev-exec-dashboard`) se lee sin pasar por JSON, que la web devuelve el ZIP en memoria solo con la cookie de sesión, y que sin `ACCESS_CODE` no entra nadie.

La batería heredada (`python3 -m pytest`) cubre el conversor anterior y no es necesaria para esta entrega.

## Cobertura

La misma lista sale en el `INFORME.md` de cada ejecución (`qlik2pbi/cobertura.py`).

### Se traduce

| Qlik | Power BI | Nota |
| --- | --- | --- |
| Sum, Count, Avg, Min, Max | SUM, COUNT, AVERAGE, MIN, MAX | `Count(DISTINCT campo)` pasa a `DISTINCTCOUNT`. |
| Only | SELECTEDVALUE | Un valor visible en el contexto actual. |
| If, and, or, not | IF, &&, \|\|, NOT | `If` anidado incluido. |
| Aggr | SUMX / VALUES o SUMMARIZE | Un nivel. `Sum(Aggr(...))` no se suma dos veces. |
| Set Analysis básico | CALCULATE | `{<Campo={valor}>}`, listas, exclusión, filtro vacío `{<Campo=>}`, `{1}` y `{$}`. |
| Year, Month, Day, Today, Now | YEAR, MONTH, DAY, TODAY, NOW en DAX; `Date.Year` y similares en M | En el script se aplican fila a fila. |
| Upper, Lower, Trim, Len, Round, Abs | UPPER, LOWER, TRIM, LEN, ROUND, ABS en DAX; `Text.*` y `Number.*` en M | |
| ApplyMap | `Table.NestedJoin` en M | La tabla `MAPPING` se conserva oculta. |
| Join (left, inner, right, outer) | `Table.NestedJoin` | Las columnas del JOIN entran en la tabla destino. |
| Concatenate | `Table.Combine` | Une por nombre de columna. |
| Resident | Referencia a la otra consulta M | La consulta ve la definición final de la tabla. |
| LOAD FROM csv/txt | `Csv.Document` | Delimitador y página de códigos del especificador Qlik. |
| LOAD FROM xlsx | `Excel.Workbook` | Usa la hoja de `table is`. |
| INLINE | `#table` | |
| SQL SELECT | `Sql.Database` | Sin `CONNECT`, el servidor queda como marcador y se avisa. |
| WHERE | `Table.SelectRows` | |
| GROUP BY | `Table.Group` | Sum, Avg, Count, Min y Max sobre un campo. |
| Gráficos de barra, línea, tarta, tabla, KPI y filtro | clusteredBarChart, lineChart, pieChart, tableEx, card, slicer | El resto de tipos pasa a tabla y se avisa. |

### Queda como aviso para revisar a mano

| Construcción | Por qué |
| --- | --- |
| Above, Below, Before, After, Top, Bottom | Funciones inter-registro. La medida queda en `BLANK()` y se conserva el texto original. |
| Peek, Previous, Exists, RangeSum, RangeAvg | Dependen del orden de carga o de una ventana. |
| Set Analysis con `P()`, `E()`, estados alternos (`::`), unión o intersección de conjuntos | Solo se traduce el modificador de campo simple. |
| Dólar `$(variable)` dentro del análisis de conjuntos | Power BI no expande variables de script. |
| Aggr anidado | El anidamiento no se aplana solo. |
| Dual, Class, FirstSortedValue, Rank, Correl, Fractile | Sin traducción fiel de una línea. |
| IntervalMatch, Crosstable, Generic, Hierarchy | Cambian la forma de la tabla. |
| QVD | Power Query no lee QVD. Se deja un CSV homónimo y un aviso. |
| Section Access | No se genera RLS. |
| For each, bucles, subrutinas y BINARY | El control de flujo del script no se ejecuta. |
| Extensiones, mapas y gráficos no estándar | El objeto pasa a tabla para no perder el dato. |

## El conversor heredado

`python migrate.py` sigue siendo la entrada del toolkit anterior (un `.qvf` o un JSON de app, asistente Streamlit en `web/app.py`). Ese flujo no es el contrato `--script` + `--export` y escribe ficheros temporales. Para un PBIP a partir del script y de la exportación, usa `qlik2pbi`.
