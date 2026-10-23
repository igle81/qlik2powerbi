# Tablas que hay que importar

Estas son las tablas del script de carga. El PBIP no incluye las filas: en Power BI Desktop hay que dejar cada origen accesible y actualizar el modelo.

## Qué cambiar en Power Query

- Cambia el parámetro `CarpetaDatos` (ahora vale `datos`) a la carpeta donde estén estos ficheros:
  - `clientes.csv`, ruta del script `lib://Datos/clientes.csv`.
  - `productos.csv`, ruta del script `lib://Datos/productos.csv`.
  - `ventas.csv`, ruta del script `lib://Datos/ventas.csv`.
  - `historico.csv`, ruta del script `lib://Datos/historico.csv`.
- En `VentasSQL`, la consulta usa el servidor `SERVIDOR` y la base `BASEDATOS`. Sustituye esos dos valores por la conexión real antes de actualizar.

## Orden de carga sugerido

1. `MapaRegion` — tabla de mapeo (oculta en el modelo)
2. `Clientes` — dimensión
3. `Productos` — dimensión
4. `VentasSQL` — dimensión
5. `Ventas` — hechos
6. `Historico` — hechos

## MapaRegion

- Nombre en Power BI: `MapaRegion`
- Papel: tabla de mapeo (oculta en el modelo)
- Origen: Datos INLINE escritos en el script.
- Campos:
  - `Codigo` — texto
  - `Region` — texto
- Claves de relación:
  - Ninguna.
- Transformaciones:
  - Ninguna aparte del origen.

## Clientes

- Nombre en Power BI: `Clientes`
- Papel: dimensión
- Origen: CSV `lib://Datos/clientes.csv`. En Power Query se lee CarpetaDatos & "/clientes.csv".
- Campos:
  - `ClienteID` — número entero
  - `Cliente` — texto
  - `Region` — texto
  - `Pais` — texto
- Claves de relación:
  - `ClienteID` ← `Ventas.ClienteID` (varios a uno)
  - `ClienteID` ← `Historico.ClienteID` (varios a uno)
- Transformaciones:
  - ApplyMap('MapaRegion') de Zona a Region, con defecto 'Sin región'.

## Productos

- Nombre en Power BI: `Productos`
- Papel: dimensión
- Origen: CSV `lib://Datos/productos.csv`. En Power Query se lee CarpetaDatos & "/productos.csv".
- Campos:
  - `ProductoID` — número entero
  - `Producto` — texto
  - `Categoria` — texto
  - `Precio` — número decimal
- Claves de relación:
  - `ProductoID` ← `Ventas.ProductoID` (varios a uno)
  - `ProductoID` ← `Historico.ProductoID` (varios a uno)
- Transformaciones:
  - Ninguna aparte del origen.

## Ventas

- Nombre en Power BI: `Ventas`
- Papel: hechos
- Origen: CSV `lib://Datos/ventas.csv`. En Power Query se lee CarpetaDatos & "/ventas.csv".
- Campos:
  - `PedidoID` — número entero
  - `Fecha` — fecha
  - `Anio` — número entero
  - `Mes` — número entero
  - `ClienteID` — número entero
  - `ProductoID` — número entero
  - `Cantidad` — número entero
  - `Importe` — número decimal
  - `Segmento` — texto
  - `Estado` — texto
- Claves de relación:
  - `ClienteID` → `Clientes.ClienteID` (varios a uno)
  - `ProductoID` → `Productos.ProductoID` (varios a uno)
  - `PedidoID` → `VentasSQL.PedidoID` (varios a uno)
- Transformaciones:
  - WHERE Cantidad > 0.
  - LEFT JOIN por PedidoID con RESIDENT VentasSQL.

## VentasSQL

- Nombre en Power BI: `VentasSQL`
- Papel: dimensión
- Origen: Conexión SQL SERVIDOR / BASEDATOS.
- Consulta: `SELECT PedidoID, Estado FROM dbo.Pedidos WHERE Estado <> 'Anulado'`
- Campos:
  - `PedidoID` — número entero
  - `Estado` — texto
- Claves de relación:
  - `PedidoID` ← `Ventas.PedidoID` (varios a uno)
  - `PedidoID` ← `Historico.PedidoID` (varios a uno)
- Transformaciones:
  - Ninguna aparte del origen.

## Historico

- Nombre en Power BI: `Historico`
- Papel: hechos
- Origen: RESIDENT Ventas.
- Campos:
  - `PedidoID` — número entero
  - `Fecha` — fecha
  - `Anio` — número entero
  - `Mes` — número entero
  - `ClienteID` — número entero
  - `ProductoID` — número entero
  - `Cantidad` — número entero
  - `Importe` — número decimal
  - `Segmento` — texto
  - `Estado` — texto
- Claves de relación:
  - `ClienteID` → `Clientes.ClienteID` (varios a uno)
  - `ProductoID` → `Productos.ProductoID` (varios a uno)
  - `PedidoID` → `VentasSQL.PedidoID` (varios a uno)
- Transformaciones:
  - CONCATENATE de CSV `lib://Datos/historico.csv`. En Power Query se lee CarpetaDatos & "/historico.csv".
