/* Shared raster failover for Resumen and Mapa de red; inventory layers are never touched. */
(function () {
    'use strict';
    window.FGBaseMap = function (map, target, options = {}) {
        const config = JSON.parse(document.getElementById('fg-map-config').textContent);
        let active = null;
        let mode = null;
        let timer = null;
        let destroyed = false;
        let autoFallback = false;
        let reason = '';
        const names = { light: 'Mapa claro', dark: 'Mapa oscuro', streets: 'Esri calles / respaldo', satellite: 'Esri satélite' };
        const control = L.control({ position: 'bottomright' });
        const box = L.DomUtil.create('div', 'fg-basemap-control');
        const select = L.DomUtil.create('select', '', box);
        select.setAttribute('aria-label', 'Proveedor del mapa base');
        Object.keys(names).forEach(key => {
            if (!config[key].url) return;
            const option = document.createElement('option');
            option.value = key;
            option.textContent = names[key];
            select.appendChild(option);
        });
        const status = L.DomUtil.create('span', 'fg-basemap-status', box);
        status.setAttribute('role', 'status');
        const retry = L.DomUtil.create('button', '', box);
        retry.type = 'button';
        retry.textContent = 'Reintentar mapa';
        retry.hidden = true;
        L.DomEvent.disableClickPropagation(box);
        L.DomEvent.disableScrollPropagation(box);
        control.onAdd = () => box;
        control.addTo(map);

        function inform(message, failed = false) {
            status.textContent = message;
            status.hidden = !message;
            retry.hidden = !failed;
            box.dataset.failed = String(failed);
            if (options.onStatus) options.onStatus(failed);
        }

        function clearTimer() {
            window.clearTimeout(timer);
            timer = null;
        }

        function canFallback(from) {
            return ['light', 'dark'].includes(from) && config.fallbackEnabled &&
                config.streets.url && !config.streets.missingKey &&
                config.streets.url !== config[from].url;
        }

        function failed(layer) {
            if (destroyed || active !== layer) return;
            clearTimer();
            if (canFallback(mode)) {
                // Leaflet still accesses layer._map after emitting load. Removing the layer
                // synchronously from that event breaks its fade lifecycle.
                timer = window.setTimeout(() => {
                    if (!destroyed && active === layer) {
                        activate('streets', 'Proveedor principal no disponible. Respaldo de calles activo.', true);
                    }
                }, 0);
            } else {
                target.classList.add('map-fallback-grid');
                inform('Mapa base no disponible o incompleto. El inventario sigue visible.', true);
            }
        }

        function activate(next, message = '', automatic = false) {
            if (destroyed) return;
            clearTimer();
            const old = active;
            active = null; // Ignore pending events from a removed layer.
            if (old) { old.off(); map.removeLayer(old); }
            mode = next;
            autoFallback = automatic;
            reason = message;
            select.value = next;
            target.dataset.basemapTheme = next;
            if (options.onChange) options.onChange(next);
            const spec = config[next];
            if (!spec.url || spec.missingKey) {
                if (canFallback(next)) {
                    activate('streets', spec.missingKey
                        ? 'CARTO sin clave configurada. Respaldo de calles activo.'
                        : 'Mapa principal sin configurar. Respaldo de calles activo.', true);
                } else {
                    inform(spec.missingKey ? 'Configura la clave CARTO para utilizar este mapa.' : 'Mapa base sin configurar.', true);
                }
                return;
            }
            inform(message || 'Cargando mapa base…');
            let errors = 0;
            let loaded = 0;
            const layer = L.tileLayer(spec.url, {
                attribution: spec.attribution,
                maxZoom: options.maxZoom || 19,
                // Send only the application origin, never site/filter query parameters.
                referrerPolicy: 'strict-origin-when-cross-origin',
            });
            active = layer;
            const arm = () => {
                if (active !== layer || destroyed) return;
                errors = 0;
                loaded = 0;
                clearTimer();
                timer = window.setTimeout(() => failed(layer), 12000);
            };
            layer.on('loading', arm);
            layer.on('tileerror', () => { if (active === layer) errors++; });
            layer.on('tileload', () => { if (active === layer) loaded++; });
            layer.on('load', () => {
                if (active !== layer || destroyed) return;
                clearTimer();
                if (errors && errors >= loaded) { failed(layer); return; }
                if (loaded) target.classList.remove('map-fallback-grid');
                inform(errors ? 'Mapa base incompleto. Puedes cambiar de proveedor.' : reason, Boolean(errors));
            });
            arm();
            layer.addTo(map);
            layer.bringToBack();
        }

        select.addEventListener('change', () => activate(select.value));
        retry.addEventListener('click', () => activate(mode));
        map.on('unload', () => {
            destroyed = true;
            clearTimer();
            if (active) active.off();
        });
        const theme = () => {
            const preferred = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
            return config[preferred].url ? preferred : (preferred === 'dark' ? 'light' : 'dark');
        };
        activate(theme());
        return {
            followTheme() {
                // Theme changes must not silently switch a manual/automatic backup back to CARTO.
                if (!autoFallback && ['light', 'dark'].includes(mode)) activate(theme());
            },
        };
    };
}());
