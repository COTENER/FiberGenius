# Fase 2 — Modelo y migración compatible

## Implementación

La migración `0031_declaraciones_ruta_y_recorridos` añade:

### Declaración oficial de la troncal

- `distancia_declarada_m`
- `capacidad_declarada_hilos`
- `hilos_ocupados_declarados`
- `hilos_reservados_declarados`
- `hilos_libres_declarados`
- `reservas_declaradas_m`
- `fuente_declaracion`
- `fecha_declaracion`
- `declarado_por`

Los campos son opcionales para conservar compatibilidad con archivos y datos
anteriores. Una capacidad declarada, cuando existe, debe ser mayor que cero.
Distancia y reservas no pueden ser negativas. La suma de los tres contadores
declarados no puede superar la capacidad oficial.

La migración no copia `Ruta.distancia_m` a `distancia_declarada_m`: el origen
del campo histórico no siempre puede demostrarse y no debe convertirse
silenciosamente en una declaración oficial.

### Completitud del tramo

`InventarioTramo.estado_inventario` admite:

- `INCOMPLETO`
- `VALIDADO`

Un tramo validado exige código, nodos de origen y destino, distancia y
capacidad. Sus contadores no pueden superar la capacidad.

Los 359 tramos históricos quedaron como `INCOMPLETO`. Esto es deliberado:
preserva datos legacy y evita afirmar que fueron homologados. Los tramos
incompletos no se consideran homologados. Desde la migración `0033`, ningún
tramo —validado o incompleto— puede conservar una suma de contadores superior
a su capacidad; el motor debe seguir tratándolos de forma conservadora.

### Recorrido de la fibra

`InventarioFibra` incorpora:

- `origen_nodo`
- `destino_nodo`

Los extremos son opcionales. Si no se informan, el recorrido se interpreta
como el recorrido completo de la troncal durante la fase del motor. Cuando se
informan, deben ser distintos.

## Compatibilidad

- No se eliminaron campos existentes.
- No se rellenaron declaraciones oficiales sin procedencia.
- No se marcaron registros legacy como validados.
- La migración se aplicó correctamente sobre la base local.
- El dashboard y las importaciones todavía no cambian en esta fase.

## Integridad fuerte y normalización canónica (migración 0033)

La migración `0033_integridad_fuerte_y_normalizacion_canonica` completa este
modelo sin retirar columnas históricas:

- La base impide que los contadores de ruta o tramo superen su capacidad.
- `FibraTramo.numero_hilo` exige el formato `F<n>`.
- Triggers transaccionales para SQLite y PostgreSQL impiden relaciones entre
  rutas diferentes, posiciones fuera de capacidad, discontinuidades entre
  tramos y asociaciones incoherentes de coordenadas o reservas.
- `NodoRed.odf_obj` y `NodoRed.hub_site_obj` enlazan identidades topológicas
  con el inventario canónico cuando existe una coincidencia exacta.
- `capacidad_hilos`, `origen_nodo` y `destino_nodo` son las fuentes canónicas
  del tramo. Los campos legacy `capacidad`, `origen` y `destino` se conservan
  sincronizados para compatibilidad con la interfaz existente.
- Los extremos normalizados de una fibra solo se completan cuando su cobertura
  incluye todos los tramos, los textos coinciden exactamente con los nodos
  terminales y ambos extremos son distintos.

Los nodos sin referencias no se eliminan automáticamente: se conservan como
evidencia histórica hasta que exista una decisión explícita de depuración.

## ODF por terminación de fibra, no por ruta (migración 0035)

La migración `0035_odf_por_fibra_no_por_ruta` elimina los campos que imponían
un único ODF de origen y destino sobre toda la ruta. La relación física queda
normalizada así:

```text
Ruta → Fibra → Terminación A/B → Puerto → ODF
```

Cada fibra puede terminar en un ODF diferente y una misma ruta puede
distribuir sus fibras entre varios ODF. Crear o eliminar una terminación no
modifica silenciosamente el estado ni la etiqueta del puerto: esos valores
pertenecen al inventario explícito de puertos.

El lote experimental 10 fue revertido antes de aplicar este cambio. Se
retiraron sus 1.753 terminaciones inferidas y se restauraron exactamente desde
el respaldo los 1.753 puertos y 87 ODF afectados. Los 1.310 ODF, 49.018
puertos y 8.449 fibras originales permanecen intactos.

## Verificación

- `manage.py check`: correcto.
- `makemigrations --check --dry-run`: sin cambios pendientes.
- 60 pruebas de modelos, importación de fases y lecturas: correctas.
- La suite principal ejecutó 92 de 93 pruebas correctamente; una prueba de
  archivos temporales fue bloqueada por permisos del entorno, sin relación con
  el modelo o la migración.
