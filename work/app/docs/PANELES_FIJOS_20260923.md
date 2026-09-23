# Paneles de análisis fijos — 23/09/2026

Alcance: Troncales, Tramos, ODF, Puertos ODF, Fibras y Reservas.

- En escritorio (>1180 px), cada panel permanece en la columna reservada,
  alineado inicialmente con sus filtros. La rueda sobre la página no lo mueve.
- La altura disponible llega hasta el borde inferior de la ventana; en ventanas
  bajas se limita la posición inicial para conservar espacio utilizable.
- Solo el cuerpo del panel tiene scroll; el encabezado no se contrae. Al llegar
  al final del contenido, la rueda no se transfiere al scroll de la página.
- Colapsar, cambiar de tamaño o abrir/cerrar el menú lateral recalcula la posición
  mediante observación de tamaños, sin consultar ni modificar inventario.
- En móvil permanece en el flujo de la página y puede contraerse. El contenido
  es accesible por teclado con foco visible.
- No cambia tipografía, tarjetas, cálculos, modelos, Dashboard ni posición del mapa.

## Validación

`scripts/tests/inspector-dock.cjs`: posición, scroll de página, colapsado,
ventana baja, transición móvil y agrupación de actualizaciones.
También pasan responsive-inventory, routes-summary y los 26 contratos gui-stage5.

Edge sobre copia aislada: seis contextos en 1440×900, 1280×720 y 390×844,
18 escenarios correctos. Se utilizó la rueda real del navegador sobre página
y panel, incluido el límite de scroll interno; se probó colapsar/expandir y
regresar de móvil a escritorio. Sin excepciones JavaScript, escrituras HTTP
ni desbordamiento global. En Reservas a 1440 px no había scroll de página
por su contenido vacío; el scroll interno se comprobó igualmente.

Evidencia: `outputs/fixed_inspectors_final_20260923/report.json` y captura.
La primera ejecución falló por un argumento del instrumento de Playwright;
se corrigió el instrumento y se repitieron los 18 escenarios completos.
No sustituye la suite general, pruebas PostgreSQL ni dispositivos físicos.
