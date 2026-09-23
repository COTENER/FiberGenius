# Fase 5 — Integración, validación y cierre operativo

## Resultado final

El motor único de rutas alimenta el dashboard, la consulta paginada de
troncales, el panel contextual y la ficha 360. Todas estas superficies reciben
el mismo valor operativo, sin mostrar simultáneamente valores declarados,
sumados o geométricos.

No se rediseñaron:

- el dashboard;
- la barra lateral;
- las pestañas;
- las tablas, tarjetas o paneles;
- los estilos y archivos JavaScript/CSS existentes.

## Reglas operativas cerradas

### Distancia

1. Valor declarado de la troncal.
2. Suma completa de los tramos.
3. Geometría.
4. Sin información.

### Capacidad

La capacidad de tramos consecutivos no se suma. El cálculo físico es el mínimo
de todos los tramos. Una capacidad declarada se utiliza si es físicamente
posible; si supera el cuello de botella, prevalece el mínimo físico seguro y
se registra una inconsistencia crítica.

Si falta la capacidad de un tramo y no existe declaración oficial, ninguna
pantalla publica la capacidad de otro tramo como si fuera la capacidad total
de la ruta.

### Estados

Una fibra se cuenta una sola vez con la prioridad:

`Ocupado > Reservado > Sin verificar > Libre`.

Solo es libre cuando está confirmada como libre durante todo su recorrido. En
toda ruta con capacidad efectiva se cumple:

`ocupados + reservados + libres + sin verificar = capacidad efectiva`.

### Reservas

Se usa primero el valor declarado de la troncal. En su ausencia, solo se usa
la suma cuando todos los tramos tienen el dato.

## Orden recomendado de carga

1. Sites.
2. Troncales y Datos Generales.
3. Trazado geográfico.
4. Inventario Técnico de Tramos.
5. Fibras por Tramo.
6. ODF, puertos y elementos complementarios.

Los pasos 2, 4 y 5 siguen siendo archivos separados para evitar un CSV
excesivamente ancho y repetitivo.

## Interpretación de datos

- Campo vacío: no existe información confirmada.
- `0`: valor confirmado en cero.
- Capacidad de ruta sin todos los tramos: no calculable.
- Fibra libre solo en parte de la ruta: sin verificar en el nivel troncal.
- Declaración superior al mínimo físico: se usa el mínimo físico seguro.

## Verificación de aceptación

Antes de una carga productiva:

1. Ejecutar `python manage.py check`.
2. Confirmar que `python manage.py makemigrations --check --dry-run` no genere
   cambios.
3. Cargar primero una muestra controlada.
4. Revisar filas rechazadas y advertencias del lote.
5. Confirmar que los contadores de cada ruta cierren con la capacidad.
6. Comparar dashboard, tabla de troncales y panel contextual para una ruta de
   muestra.

La migración no convierte automáticamente datos históricos en declaraciones
oficiales. Los valores históricos continúan como calculados hasta que sean
declarados mediante la carga de Troncales y Datos Generales.

## Resultado de la validación final

Validación ejecutada el 30 de julio de 2026:

- 111 pruebas integrales superadas.
- `manage.py check`: sin observaciones.
- `makemigrations --check --dry-run`: sin cambios pendientes.
- Dashboard de inventario: HTTP 200.
- Inventario externo: HTTP 200.
- API paginada de troncales: HTTP 200.
- Panel contextual de troncal: HTTP 200.
- 359 rutas auditadas en la base actual.
- 353 rutas con capacidad efectiva calculable.
- 6 rutas sin capacidad suficiente, conservadas como `Sin información`.
- 0 rutas con cierre incorrecto de estados.
- 0 inconsistencias críticas en los datos actuales.

La inspección visual automatizada del navegador integrado no estuvo disponible
por una restricción del entorno de ejecución. La conservación de la barra
lateral, pestañas, hojas de estilo, scripts y estructura de dashboard se
verificó mediante pruebas de renderizado del HTML y respuestas reales de las
vistas.
