# Fase 1 — Diagnóstico inicial

Fecha de corte: 2026-07-30
Base revisada: `fibergenius_rc2_pruebas.sqlite3`
Configuración: `fibergenius.settings_pruebas`

## Resumen de datos

| Control | Resultado |
|---|---:|
| Troncales | 359 |
| Troncales sin tramos | 0 |
| Troncales con un tramo | 359 |
| Troncales con varios tramos | 0 |
| Tramos | 359 |
| Tramos sin distancia | 0 |
| Tramos sin capacidad numérica | 6 |
| Tramos sin nodo de origen | 0 |
| Tramos sin nodo de destino | 3 |
| Tramos con contadores por encima de capacidad | 0 |
| Fibras lógicas | 1274 |
| Asignaciones de fibras por tramo | 1274 |
| Fibras sin asignación a tramo | 0 |
| Troncales con distancia registrada | 359 |

## Hallazgos

### 1. La base actual solo cubre el caso de un tramo

Las 359 troncales poseen exactamente un tramo. La compatibilidad con ese caso
es buena, pero la base actual no homologa por sí sola las reglas de continuidad
para dos o más tramos. La suite deberá crear casos controlados de varios tramos.

### 2. Hay seis tramos sin capacidad numérica

Estos registros no permiten probar una capacidad efectiva física. Deben
mantenerse como información incompleta; no se debe inferir capacidad ni
disponibilidad.

### 3. Hay tres tramos sin nodo de destino

La migración legacy no pudo resolver esos extremos con certeza. No deben
completarse automáticamente sin una fuente verificable.

### 4. La cobertura individual actual es completa para el caso legacy

Las 1274 fibras lógicas tienen una asignación `FibraTramo`. Como todas las rutas
poseen un único tramo, esto permite conservar los indicadores actuales mientras
se introduce el motor nuevo.

### 5. No se detectaron contadores imposibles

No existen tramos cuya suma almacenada de ocupados, reservados y libres supere
la capacidad numérica conocida.

## Riesgos de implementación

1. El campo actual `Ruta.distancia_m` puede representar distancia oficial,
   geográfica o importada. La migración debe preservar el dato y registrar su
   procedencia sin afirmar un origen que no pueda demostrarse.
2. Los contadores de tramo y el detalle `FibraTramo` son dos fuentes. El motor
   nuevo debe dar precedencia al detalle cuando existe y utilizar contadores
   solo como compatibilidad para un tramo.
3. Los escenarios de varios tramos necesitan pruebas sintéticas antes de
   activarse con datos reales.
4. Una capacidad o un extremo faltante debe producir `Sin información` o
   `No calculable`, nunca cero ni libre.

## Línea base visual

Se conservarán como referencias las capturas existentes en:

- `work/app/gui-review/dashboard.png`
- `work/app/gui-review/configuracion.png`
- `work/app/gui-review/inventario-desktop.png`
- `work/app/gui-review/inventario-mobile.png`
- `work/app/gui-review/mapa-alarmas.png`

La automatización del navegador no estuvo disponible durante este corte, por
lo que estas capturas existentes constituyen la línea base inicial. Antes de
cerrar la Fase 5 se deberán repetir capturas en el entorno disponible y
compararlas con estas referencias.

## Evaluación

La lógica propuesta es consistente con el dominio:

- suma distancia y reservas físicas;
- usa el mínimo para capacidad;
- clasifica cada fibra una sola vez;
- no convierte ausencia de inventario en disponibilidad.

La condición para avanzar es mantener explícitamente `Sin verificar` y no
sumar contadores entre tramos. Ambas reglas quedan fijadas en la especificación
de la Fase 1.
