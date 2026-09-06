# Fase 4 — Importaciones y nombres operativos

## Resultado

La carga de troncales conserva un único CSV y una única tarjeta. El formato
anterior continúa siendo válido y se agregan columnas opcionales para los
valores oficiales de la ruta.

## Tarjetas

Sin cambiar la estructura visual, estilos, modal ni navegación existentes:

- `Cargar Troncales y Datos Generales` se movió a **Modo Inventario Físico**.
- `Cargar Detalles Técnicos Complementarios` pasó a llamarse
  `Cargar Inventario Técnico de Tramos`.
- `Cargar inventario de fibras ópticas` pasó a llamarse
  `Cargar Fibras por Tramo`.

No se creó una tarjeta adicional.

## CSV de troncales

La única columna obligatoria es `Ruta` o `Troncal`. Se mantienen las columnas
anteriores y se aceptan estas columnas opcionales:

- `Distancia (m)`
- `Capacidad`
- `Hilos Ocupados`
- `Hilos Reservados`
- `Hilos Libres`
- `Reservas (m)`

Una columna omitida no modifica el dato que ya exista. Cuando la columna está
presente, una celda vacía representa falta de información (`NULL`) y el valor
`0` representa un cero confirmado. La capacidad, cuando se informa, debe ser
mayor que cero.

Cada fila se valida por separado. Una fila cuya suma de hilos ocupados,
reservados y libres supere la capacidad declarada se rechaza, mientras las
filas válidas del mismo archivo sí se procesan. La fuente y fecha de la
declaración quedan auditadas.

## Compatibilidad

- Los CSV de ocho columnas usados antes de esta fase siguen funcionando.
- El comando de validación acepta tanto
  `Cargar Troncales y Datos Generales.csv` como el nombre anterior
  `Cargar Troncales y Metadatos.csv`.
- La plantilla descargable fue ampliada en vez de crear otro archivo de carga.
