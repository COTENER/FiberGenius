# Dashboard de Calidad de información

## Separación funcional

- Resumen conserva sus indicadores, mapa y gráficos operativos. Se retiró únicamente el bloque transversal «Datos pendientes».
- La nueva pantalla está en **Carga y calidad → Calidad de información**, URL `/inventario/calidad/`.
- Distingue información pendiente (cargas parciales válidas) de contradicciones entre registros. No interpreta una condición física con falla como un error de calidad de datos.
- No modifica inventario, reglas de importación, estados, conexiones, modelos ni migraciones. No realiza reparaciones automáticas.

## Controles consultables

| Grupo | Control | Unidad contada |
| --- | --- | --- |
| Por completar | Estado global sin información | Fibra |
| Por completar | Troncal sin asociar | Fibra |
| Por completar | Falta terminación A, B o ambas | Fibra, una vez |
| Por completar | Asignación pendiente a uno o más tramos de su troncal | Fibra, una vez |
| Por completar | Menos de dos puntos de recorrido | Troncal |
| Por completar | Sin tramos técnicos | Troncal |
| Por completar | Site, sala o rack sin asignar | ODF |
| Por completar | Menos puertos registrados que capacidad declarada | ODF |
| Por completar | Latitud o longitud ausente | Site |
| Inconsistencia | Asignación de fibra a tramo de otra troncal, o referencia cuya fibra ya no tiene troncal | Fibra |
| Inconsistencia | Más puertos registrados que capacidad positiva declarada | ODF |
| Inconsistencia | Terminación en puerto no ocupado, u ocupado sin terminación oficial | Puerto |

Los totales suman observaciones, no activos únicos. Un activo puede aparecer en más de un control. No hay porcentaje general ni etiqueta «inventario completo»: estos controles no sustituyen todas las comprobaciones detalladas de la ficha de fibra.

Dos coordenadas de troncal no certifican geometría completa. Cero es una coordenada de Site válida. «SIN ASIGNAR» es ubicación pendiente, no un Site físico pendiente de georreferenciar. Capacidad cero no se interpreta automáticamente como exceso de puertos. La ausencia de tramos técnicos se distingue de una asignación parcial de fibras a tramos existentes.

## Uso e integración

- Cada tarjeta abre sus registros afectados, explicación y siguiente paso recomendado. Los registros tienen acceso a la ficha 360° existente.
- Site compartido con el resto de pantallas; se hereda el contexto y se puede quitar desde cualquier pantalla. Un Site inexistente conserva el filtro y muestra aviso, sin ampliar silenciosamente la consulta.
- Los activos sin ubicación conocida se revisan en Todos los Sites. El filtro de fibras conserva las relaciones oficiales y los fallbacks de los filtros existentes.
- La búsqueda afecta al listado del control seleccionado, no a los totales de las tarjetas. La paginación es de 25 filas y conserva Site, control y búsqueda.
- Los controles se habilitan con los permisos de consulta existentes de sus entidades. Los controles cruzados requieren las consultas correspondientes; un código no autorizado devuelve 404. La pantalla exige autenticación y solo admite GET.
- Estilos propios acotados a `.quality-*`, tokens de Fiber Genius, componentes de botones y ficha compartidos. Se incluyen reglas adaptables, foco visible y movimiento reducido, sin nuevos recursos externos.
- Las consultas usan agregaciones y subconsultas SQL; los detalles se precargan solo para la página visible. No se calcula el diagnóstico completo individual de miles de fibras en cada apertura.

## Validación y límites

- 134 pruebas de regresión correctas en la ejecución final (22 nuevas de Calidad). Los errores de disco/BD impresos por las pruebas de importación son escenarios simulados; el resultado final de la ejecución fue OK.
- 168 comprobaciones JavaScript correctas en los seis scripts de regresión locales.
- `mapas.tests_calidad_dashboard`: pruebas aisladas de conteos, extremos A/B, recorridos de uno/dos/tres tramos, permisos, lectura sin escrituras, coordenadas cero, ubicación pendiente, capacidad, paginación, búsqueda, escape HTML y consultas sin N+1.
- Regresiones de Resumen actualizadas para permitir el enlace al nuevo dashboard en el menú, conservando la separación del contenido operativo.
- JavaScript: contexto de Site ampliado con herencia y limpieza en la nueva pantalla. Versión del recurso actualizada para evitar caché anterior.
- Base actual consultada con `PRAGMA query_only=ON`: las doce vistas de control devolvieron 200, entre 0,24 y 0,64 segundos por render en esta comprobación local. Filtros y búsqueda también respondieron correctamente.
- `manage.py check` correcto y `makemigrations --check --dry-run` sin cambios.
- No se ejecutó validación contra PostgreSQL ni la suite completa del proyecto en este bloque.
- La conexión del navegador integrado sigue fallando antes de abrir la aplicación. La inspección visual real (pantalla pequeña, tema oscuro y clics) queda pendiente; no se certifica a partir de las pruebas de render del servidor.
- No se realizó commit ni push, y se preservaron los cambios anteriores del árbol de trabajo.

Comando de regresión:

```powershell
$env:DEBUG='False'
.\.venv\Scripts\python.exe manage.py test mapas.tests_calidad_dashboard mapas.tests_dashboard_correcciones mapas.tests_revision_pantallas mapas.tests.GuiOperativaTests mapas.tests_gui_administracion mapas.tests_navegacion_importaciones mapas.tests_importacion_progreso --settings=fibergenius.settings_pruebas --noinput
```
