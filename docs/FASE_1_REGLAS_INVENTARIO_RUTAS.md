# Fase 1 — Reglas de inventario de rutas, tramos y fibras

## Alcance protegido

La implementación modifica el modelo, las validaciones, las importaciones y el
origen de los indicadores. No rediseña el dashboard, la barra lateral, las
pestañas, las tarjetas, los modales, la navegación, los colores, los iconos ni
el comportamiento responsive.

En importación solo se permiten estos cambios visibles:

1. Mover la tarjeta existente `ruta_otu` desde Modo Operativo Completo hacia
   Modo Inventario Físico, sin duplicarla.
2. Renombrar tres tarjetas y actualizar sus textos de ayuda:
   - **Cargar Troncales y Datos Generales**.
   - **Cargar Inventario Técnico de Tramos**.
   - **Cargar Fibras por Tramo**.
3. Mantener la estructura, estilo, icono, modal y endpoint de las tarjetas.

## Jerarquía de inventario

Una troncal (`Ruta`) debe admitir uno o varios tramos ordenados:

```text
Troncal
├── Tramo T001
├── Tramo T002
└── Tramo T003
```

Los tramos consecutivos deben mantener continuidad: el destino de un tramo
debe coincidir con el origen del siguiente.

## Ausencia de datos

- `NULL` representa **Sin información**.
- `0` representa un valor conocido y confirmado en cero.
- Una posición no inventariada nunca se interpreta como libre.

## Distancia de una troncal

El sistema presenta un solo valor operativo con esta precedencia:

1. Distancia oficial válida registrada en la troncal.
2. Suma de las distancias de todos sus tramos, únicamente si el conjunto está
   completo.
3. Distancia calculada desde la geometría.
4. Sin información.

Una suma parcial puede mostrarse en auditoría, pero no se usa como distancia
completa. La comparación entre fuentes usa estas bandas:

- diferencia menor al 2 %: normal;
- diferencia entre 2 % y 5 %: advertencia;
- diferencia mayor al 5 %: inconsistencia.

## Reservas de cable

Los metros de reserva siguen esta precedencia:

1. Reserva oficial válida registrada en la troncal.
2. Suma completa de las reservas de sus tramos.
3. Sin información.

La geometría no se utiliza para calcular reservas.

## Capacidad

Las capacidades de tramos consecutivos nunca se suman.

```text
capacidad efectiva calculada = mínimo(capacidad de cada tramo)
capacidad máxima instalada = máximo(capacidad de cada tramo)
```

La capacidad declarada de la troncal representa capacidad extremo a extremo.
Para decisiones de disponibilidad, una declaración nunca puede superar el
cuello de botella físico:

```text
capacidad segura = mínimo(capacidad declarada, capacidad efectiva calculada)
```

Si falta capacidad en algún tramo, la capacidad calculada de la ruta queda
como no calculable. El sistema puede conservar una declaración oficial, pero
debe advertir que no fue verificada físicamente.

## Recorrido de una fibra

La continuidad se evalúa sobre los tramos que componen el recorrido de la
fibra. Si no existe un recorrido parcial declarado, se asume que el recorrido
corresponde a todos los tramos de la troncal.

Una fibra parcial puede aportar disponibilidad entre sus nodos de inicio y
fin, pero no a la disponibilidad extremo a extremo de toda la troncal.

## Estado operativo de una fibra

Cada fibra se clasifica exactamente una vez mediante esta prioridad:

```text
Ocupado > Reservado > Sin verificar > Libre
```

Reglas:

1. Si está ocupada en algún tramo del recorrido, el estado operativo es
   **Ocupado**.
2. Si no está ocupada y está reservada en algún tramo, es **Reservado**.
3. Si no está ocupada ni reservada, pero falta un tramo o estado, es
   **Sin verificar**.
4. Solo es **Libre** cuando está confirmada como libre en todos los tramos de
   su recorrido.

El detalle técnico conserva, sin competir con el valor operativo, si el estado
es completo, parcial, inconsistente o carece de cobertura.

## Contadores de la troncal

Los contadores de distintos tramos no se suman. Se cuenta una sola vez el
estado operativo de cada fibra del recorrido:

```text
ocupados + reservados + libres + sin verificar = capacidad efectiva
```

Para una troncal de un solo tramo se pueden usar los contadores del tramo
cuando no exista detalle individual. Para varios tramos sin `FibraTramo`
suficiente, el resultado es **No calculable**, nunca una suma.

## Presentación

Las pantallas operativas muestran un único valor por indicador. La procedencia,
las diferencias y el detalle de conciliación quedan disponibles para auditoría
o vistas de detalle; no se agregan valores competidores al dashboard.

## Compatibilidad CSV

Se conservan las tres cargas existentes:

1. Troncales y datos generales.
2. Inventario técnico de tramos.
3. Fibras por tramo.

No se crean nuevas tarjetas. Los CSV históricos continúan aceptándose y las
columnas oficiales nuevas de la troncal son inicialmente opcionales.
