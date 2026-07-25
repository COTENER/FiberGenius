# Despliegue Docker de la demo RC2

Este despliegue inicia la aplicación Django y mantiene su base SQLite en
`data_demo/`, un directorio local excluido de Git.

## Configuración

```bash
cp .env.demo.example .env.demo
```

Edite `.env.demo` y sustituya `FIBERGENIUS_SECRET_KEY` por un valor aleatorio largo.
No publique ese archivo ni credenciales de usuarios.

Para el acceso local documentado por HTTP, mantenga
`FIBERGENIUS_SECURE_COOKIES=false`. Si publica la demo detrás de Caddy con HTTPS,
cambie el valor a `true` y agregue el origen HTTPS a
`FIBERGENIUS_CSRF_TRUSTED_ORIGINS`.

## Arranque

```bash
docker compose -f compose.demo.yaml up -d --build
docker compose -f compose.demo.yaml ps
```

La aplicación queda disponible en `http://127.0.0.1:8001`. Si no existe una base de
datos, el contenedor crea una vacía y aplica las migraciones.

Para crear el primer administrador:

```bash
docker compose -f compose.demo.yaml exec fiber-genius-rc2-lab-web \
  python manage.py createsuperuser
```

## Conexión con Caddy

Conecte el contenedor de Caddy a la red de la demo una sola vez:

```bash
docker network connect fiber-genius-rc2-lab-network n8n-caddy
```

Agregue el contenido de `Caddyfile.demo` a la configuración de Caddy y recargue el
contenedor. Ajuste los hosts y orígenes confiables en `.env.demo` al dominio real.

## Persistencia

La base activa queda en `data_demo/fibergenius.sqlite3`. Al reconstruir el contenedor
se actualizan el código y la interfaz sin borrar esa base. Elimine `data_demo` solo si
quiere reiniciar deliberadamente la demo.
