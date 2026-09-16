# FiberGenius RC2 GUI v0.5.0

Aplicación Django para inventario y visualización de infraestructura de fibra óptica.

## Contenido

- `work/app/fibergenius/`: configuración general del proyecto Django.
- `work/app/mapas/`: aplicación principal con el dominio de FiberGenius.
- `compose.demo.yaml`: despliegue Docker local en `127.0.0.1:8001`.
- `.env.demo.example`: configuración de ejemplo sin credenciales.
- `DESPLIEGUE_DOCKER_DEMO.md`: instrucciones de despliegue.

## Datos no incluidos

Este repositorio no contiene bases SQLite pobladas, inventarios CSV, archivos `.env`
reales ni credenciales. Esos elementos deben mantenerse fuera del control de versiones
y cargarse mediante los procedimientos autorizados para cada entorno.

## Arranque con Docker

```bash
cp .env.demo.example .env.demo
# Edite .env.demo y defina una clave secreta aleatoria.
docker compose -f compose.demo.yaml up -d --build
docker compose -f compose.demo.yaml ps
```

La primera ejecución crea una base SQLite vacía y aplica las migraciones. Para crear
el primer administrador de forma interactiva:

```bash
docker compose -f compose.demo.yaml exec fiber-genius-rc2-lab-web \
  python manage.py createsuperuser
```

También puede definir temporalmente `DJANGO_SUPERUSER_USERNAME`,
`DJANGO_SUPERUSER_EMAIL` y `DJANGO_SUPERUSER_PASSWORD` en `.env.demo` para que el
contenedor cree el usuario durante el primer arranque. Retire esos valores después.

## Desarrollo local

```bash
cd work/app
python -m venv .venv
.venv/Scripts/pip install -r requirements.demo.txt
.venv/Scripts/pip install -r requirements.dev.txt
.venv/Scripts/python manage.py test --settings=fibergenius.settings_pruebas
```

## Perfiles de dependencias

- `requirements.demo.txt`: aplicación ejecutada con SQLite, tanto en la demo
  Docker como en las pruebas locales.
- `requirements.dev.txt`: herramientas de desarrollo y validación, instaladas
  después de las dependencias de la demo.
- `requirements.lock`: conjunto cerrado para el despliegue de producción en
  Windows con PostgreSQL. Instálelo con
  `python -m pip install --require-hashes -r requirements.lock`.

La suite actual contiene 101 pruebas y cubre el 73 % del código Python medido. El
flujo de integración continua rechaza cambios que reduzcan la cobertura por debajo
de ese umbral. Las pruebas adicionales cubren seguridad, permisos, inventario,
webhooks, VeEX, trazas SOR, auditoría, Sites, perfiles de umbral y los indicadores
del centro de control de inventario. Troncales, tramos, fibras y reservas usan
consultas paginadas en servidor, indicadores filtrables y exportación Excel.

## Auditoría de la carga inicial

Después de aplicar migraciones, el inventario existente puede registrarse como una
línea base reconstruida. Este comando no la presenta como una importación ejecutada
desde la GUI: conserva el origen, la fecha declarada, el hash SHA-256 del manifiesto
y los conteos actuales del inventario.

```bash
python manage.py migrate
python manage.py registrar_baseline_inventario \
  --manifest /ruta/al/manifiesto.csv \
  --fecha-origen 2026-07-18T12:00:00-05:00 \
  --descripcion "Carga V5 reconstruida para homologación" \
  --usuario admin
```

Use un manifiesto verificado y respaldado. El comando rechaza el mismo hash si ya
fue registrado.
