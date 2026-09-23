# Reversión visual solicitada — 23/09/2026

Se retiran el aumento tipográfico del 22/09 y los rediseños visuales posteriores.
No es un reset al commit del 16/09 ni una restauración de base de datos.

## Cambios retirados, en orden

1. Mínimo general de 12 px en los cuatro estilos de inventario: se recuperan los tamaños anteriores de tablas, filtros, formularios y paneles.
2. Ajuste posterior de densidad y presentación móvil de Sites: se recupera la plantilla previa.
3. Compactación y reorganización visual de los seis paneles contextuales y sus rankings.
4. Separación del porcentaje del ranking en una columna propia: vuelve su presentación anterior.
5. Dona centrada, leyenda inferior y eliminación de recuadros: vuelven dona lateral, leyenda a su lado y tarjetas enmarcadas.
6. Sustitución de la dona de Troncales por cifras y gráficos de barras: vuelve «Capacidad consolidada». La barra intermedia de uso de fibras ya había sido sustituida por la de trazado; ninguna queda activa.
7. Gráfico de trazado de Troncales y cálculo adicional asociado: retirados junto con sus pruebas exclusivas.

## Correcciones conservadas

- Importaciones, validaciones, permisos, recuperación y preparación de entrega.
- Proveedores de mapas y reserva de espacio para su atribución.
- Contención de tablas/filtros, identificadores completos y protección de anchos internos.
- Prevención del recorte de cifras KPI (espacios e iconos), con los tamaños tipográficos originales.
- Paginación accesible y adaptación funcional del inspector a ventanas pequeñas.
- Resumen propio al regresar a Troncales/Tramos: una respuesta de la pestaña oculta no reemplaza la visible.
- Corrección de «Sin destino informado» en puertos: los libres no generan pendientes.

No se modificaron modelos, migraciones, servicios, importadores, configuración,
Dashboard, posición del mapa ni datos. No se hizo commit ni push.

## Respaldo

`outputs/antes_reversion_visual_20260923/` contiene copias verificadas de los
22 archivos anteriores a esta reversión, conservando su estructura de rutas.
Los tres archivos retirados (CSS de Sites y pruebas exclusivas del gráfico de
trazado) pueden recuperarse de esa copia. No restaurar archivos completos sobre
cambios posteriores sin compararlos primero.

Los documentos del 22/09 conservan sus evidencias históricas; no representan
la presentación vigente después de esta reversión.

## Validación de la reversión

- 23 pruebas Django correctas (administración GUI, troncales sin tramos y lecturas).
- Contratos de tipografía anterior, paginación, contención y resumen de pestañas;
  28 comprobaciones de contexto global y pruebas de adaptación responsive.
- Edge: seis contextos (Troncales, Tramos, ODF, Puertos, Fibras y Reservas),
  a 1440 y 390 px. Tablas nuevamente de 10 px, tarjetas enmarcadas, sin barra
  de trazado posterior, sin desbordamiento global ni excepciones JavaScript.
- Resumen conservado al volver de Tramos a Troncales. Ninguna escritura HTTP.
- Evidencia: `outputs/visual_revert_final_20260923/report.json` y capturas.
- Comparación de huellas: únicamente cambiaron los 22 archivos respaldados
  y se añadió este documento. Dashboard y cambios funcionales ajenos intactos.
- Servidor habitual reiniciado; health, login y CSS restaurado responden HTTP 200.

La revisión visual es focalizada, no certifica toda la aplicación ni sustituye
la suite completa o las pruebas PostgreSQL pendientes.

## Ajuste puntual posterior solicitado: tarjeta de Troncales

Tras la reversión el usuario pidió corregir específicamente «Capacidad consolidada».
Se cambia únicamente esa tarjeta a «Resumen de troncales»: total sin dona
decorativa, tres métricas en filas alineadas y distancia documentada al pie.
Tipografía local de 11–12 px; no se aumenta la letra global ni se cambian rankings,
Dashboard, modelos o consultas. Tramos conserva su distribución por trazado.
La clase de presentación se retira al cambiar a Tramos y se restituye al volver.

Pruebas: `scripts/tests/routes-summary.cjs`, contratos GUI y adaptación responsive.
Capturas y mediciones aisladas: `outputs/route_summary_20260923/`.

### Presentación aprobada: cuatro tarjetas

El usuario solicitó después una cuadrícula 2 × 2 en esa misma tarjeta:
Troncales, Fibras inventariadas, Tramos y Reservas físicas. Cifra arriba,
etiqueta debajo y kilómetros al pie. Se conservan valores, filtros,
resumen de Tramos y estilos de todas las demás pantallas.
Prueba específica actualizada en `scripts/tests/routes-summary.cjs`;
evidencia visual en `outputs/route_tiles_20260923/`.
