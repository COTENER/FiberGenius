# Fase 3 — Motor operativo único

## Servicio central

`mapas.services.capacidad` concentra ahora:

- capacidad declarada, calculada y segura;
- estados operativos de fibras;
- distancia oficial, por tramos o por geometría;
- reservas oficiales o sumadas desde tramos;
- procedencia y conciliación de cada indicador.

Las claves anteriores del servicio se conservan para evitar cambios en el
contrato consumido por las pantallas. Se añadieron claves de procedencia,
conciliación y valores auxiliares para auditoría.

## Capacidad

```text
capacidad calculada = mínimo de todos los tramos
```

Si existe capacidad oficial:

- cuando es físicamente posible, es el valor operativo;
- cuando supera el cuello de botella, se usa el mínimo físico y se marca
  `INCONSISTENCIA_CRITICA`;
- cuando faltan capacidades de tramos, la declaración puede operar, pero queda
  sin comparación física.

El campo visible `capacidad` contiene un solo valor operativo. El rango físico
se conserva internamente como `capacidad_instalada`.

## Estados

Cada fibra se cuenta una vez:

```text
Ocupado > Reservado > Sin verificar > Libre
```

Una declaración de estados solo reemplaza el cálculo cuando ocupados,
reservados y libres están presentes como conjunto completo. Una declaración
parcial no se mezcla con estados calculados.

Cuando existe capacidad efectiva:

```text
ocupados + reservados + libres + sin verificar = capacidad efectiva
```

`Sin verificar` incorpora tanto fibras lógicas con cobertura incompleta como
posiciones de capacidad que todavía no fueron inventariadas.

## Distancia

```text
distancia declarada
→ suma completa de tramos
→ geometría
→ sin información
```

La geometría respeta inicios de segmento y no conecta visualmente segmentos
separados.

## Reservas

```text
reserva declarada
→ suma completa de reservas de tramos
→ sin información
```

La geometría no participa en este cálculo.

## Integraciones

El motor alimenta:

- dashboard de inventario;
- resumen y consulta paginada de troncales;
- panel contextual de troncal;
- ficha 360 de troncal;
- exportaciones que reutilizan la serialización de troncales.

No se modificaron plantillas, tarjetas, barra lateral ni pestañas.

## Verificación

- 67 pruebas de motor, modelos, importaciones y lecturas: correctas.
- Dashboard de inventario: HTTP 200.
- Inventario externo: HTTP 200.
- API de troncales: HTTP 200.
- Panel de troncal: HTTP 200.
- 353 rutas con capacidad cierran exactamente sus cuatro estados.
- 6 rutas permanecen sin capacidad, según el diagnóstico de la Fase 1.
- No hay cambios de modelos pendientes de migración.

## Compatibilidad

Los datos actuales se calculan desde tramos y fibras porque todavía no existen
declaraciones oficiales cargadas:

- distancia: 359 desde tramos;
- capacidad: 353 desde tramos y 6 sin información;
- estados: 359 desde fibras.
