(function () {
    'use strict';
    const root = document.getElementById('fiber-workbench');
    if (!root) return;
    const formatter = new Intl.NumberFormat('es-PE', { maximumFractionDigits: 2 });
    const csrf = () => (document.cookie.match(/(?:^|; )csrftoken=([^;]+)/) || [])[1] || '';
    const put = (id, value) => { const node = document.getElementById(id); if (node) node.textContent = formatter.format(value || 0); };
    const putText = (id, value) => { const node = document.getElementById(id); if (node) node.textContent = String(value ?? ''); };
    const inspector = document.getElementById('network-inspector');
    const inspectorToggle = document.getElementById('network-inspector-toggle');
    let routeMap = null;
    let routeRequest = null;

    function cell(value) { const td = document.createElement('td'); td.textContent = value === null || value === undefined || value === '' ? '—' : value; return td; }
    function badge(value) {
        const span = document.createElement('span'); const state = String(value || '').toLowerCase(); span.className = 'odf-status';
        if (state.includes('reserv') || state.includes('confirmar')) span.classList.add('is-warning');
        if (state.includes('parcial') || state.startsWith('sin ')) span.classList.add('is-warning');
        if (state.includes('fuera') || state.includes('error')) span.classList.add('is-danger');
        if (!value) span.classList.add('is-neutral'); span.textContent = value || 'Sin estado'; return span;
    }
    function actionButton(label, symbol, callback) { const button = document.createElement('button'); button.type = 'button'; button.className = 'odf-icon-action'; button.title = label; button.setAttribute('aria-label', label); button.textContent = symbol; button.addEventListener('click', (event) => { event.stopPropagation(); callback(); }); return button; }
    function actionCell(item, editor) {
        const td = document.createElement('td'); const wrap = document.createElement('div'); wrap.className = 'network-actions';
        wrap.appendChild(actionButton('Abrir ficha 360°', '◉', () => window.FiberGenius && window.FiberGenius.openAsset360 ? window.FiberGenius.openAsset360(item.detail_url) : window.location.assign(item.detail_url)));
        if (editor) wrap.appendChild(actionButton('Editar', '✎', () => editor(item))); td.appendChild(wrap); return td;
    }
    async function exportFile(base, params, status) {
        status.className = 'odf-export-status'; status.textContent = 'Generando archivo…';
        try {
            const url = new URL(base, location.origin); url.search = params.toString(); const response = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            if (!response.ok) throw new Error(); const blob = await response.blob(); const disposition = response.headers.get('Content-Disposition') || ''; const match = disposition.match(/filename="?([^";]+)"?/i);
            const objectUrl = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = objectUrl; link.download = match ? match[1] : 'FiberGenius.xlsx'; document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(objectUrl);
            status.classList.add('is-success'); status.textContent = 'Archivo descargado correctamente.';
        } catch (_error) { status.classList.add('is-error'); status.textContent = 'No se pudo generar el archivo. Intenta nuevamente.'; }
    }

    function setInspectorCollapsed(collapsed) {
        if (!inspector || !inspectorToggle) return;
        inspector.classList.toggle('is-collapsed', collapsed);
        inspectorToggle.setAttribute('aria-expanded', String(!collapsed));
        inspectorToggle.setAttribute('aria-label', collapsed ? 'Mostrar resumen' : 'Ocultar resumen');
        inspectorToggle.title = collapsed ? 'Mostrar resumen' : 'Ocultar resumen';
        if (!collapsed && routeMap) setTimeout(() => routeMap.invalidateSize(), 240);
    }

    if (inspectorToggle) {
        inspectorToggle.addEventListener('click', () => {
            setInspectorCollapsed(!inspector.classList.contains('is-collapsed'));
        });
        setInspectorCollapsed(window.innerWidth <= 1180);
    }

    function switchInspectorContext(name) {
        root.querySelectorAll('[data-inspector-context]').forEach((section) => {
            section.hidden = section.dataset.inspectorContext !== name;
        });
        const title = document.getElementById('fiber-inspector-title');
        if (title) {
            title.textContent = name === 'reservas' ? 'Análisis de reservas' : 'Análisis de fibras';
        }
    }

    function renderFiberSummary(summary) {
        const total = Number(summary.total || 0);
        const free = Number(summary.libres || 0);
        const used = Number(summary.ocupadas || 0);
        const reserved = Number(summary.reservadas || 0);
        const unknown = Number(summary.sin_estado || 0);
        put('fiber-summary-total', total);
        put('fiber-summary-free', free);
        put('fiber-summary-used', used);
        put('fiber-summary-reserved', reserved);
        put('fiber-summary-unknown', unknown);
        put('fiber-summary-informed', summary.estados_informados || 0);
        put('fiber-summary-inferred', summary.estados_inferidos || 0);
        const pct = (value) => total
            ? `${new Intl.NumberFormat('es-PE', { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format((value / total) * 100)}%`
            : '0%';
        putText('fiber-summary-free-pct', pct(free));
        putText('fiber-summary-used-pct', pct(used));
        putText('fiber-summary-reserved-pct', pct(reserved));
        putText('fiber-summary-unknown-pct', pct(unknown));
        const selectedState = root.querySelector('[data-resource="fibras"] [data-filter="estado"]')?.value || '';
        const stateCounts = { DISPONIBLE: free, OCUPADO: used, RESERVADO: reserved, SIN_INFORMACION: unknown };
        root.querySelectorAll('[data-fiber-state]').forEach((button) => {
            const selected = button.dataset.fiberState === selectedState;
            button.classList.toggle('is-selected', selected);
            button.classList.toggle('is-zero', Number(stateCounts[button.dataset.fiberState] || 0) === 0);
            button.setAttribute('aria-pressed', String(selected));
        });
        const donut = document.getElementById('fiber-summary-chart');
        if (donut) {
            const freePct = total ? (free / total) * 100 : 0;
            const usedPct = total ? (used / total) * 100 : 0;
            const reservedPct = total ? (reserved / total) * 100 : 0;
            const usedEnd = freePct + usedPct;
            const reservedEnd = usedEnd + reservedPct;
            donut.style.background = total
                ? `conic-gradient(#18b777 0 ${freePct}%, #2f74ee ${freePct}% ${usedEnd}%, #f59e0b ${usedEnd}% ${reservedEnd}%, #94a3b8 ${reservedEnd}% 100%)`
                : 'conic-gradient(#e8edf5 0 100%)';
        }
        const ranking = document.getElementById('fiber-top-destinations');
        if (!ranking) return;
        ranking.replaceChildren();
        const destinations = Array.isArray(summary.destinos_mayor_uso) ? summary.destinos_mayor_uso : [];
        if (!destinations.length) {
            const empty = document.createElement('p');
            empty.className = 'network-inspector-empty';
            empty.textContent = Number(summary.ocupadas || 0) + Number(summary.reservadas || 0)
                ? 'Las fibras en uso no tienen un destino documentado.'
                : 'No hay fibras ocupadas o reservadas con los filtros actuales.';
            ranking.appendChild(empty);
            return;
        }
        destinations.forEach((destination) => {
            const item = document.createElement('div');
            item.className = 'network-ranking__item';
            const activeDestination = root.querySelector('[data-resource="fibras"] [data-filter="destino"]')?.value || '';
            const activeInUse = root.querySelector('[data-resource="fibras"] [data-filter="en_uso"]')?.value || '';
            const isSelected = activeDestination === destination.destino && activeInUse === '1';
            item.classList.toggle('is-selected', isSelected);
            item.tabIndex = 0;
            item.setAttribute('role', 'button');
            item.setAttribute('aria-pressed', String(isSelected));
            item.setAttribute('aria-label', isSelected
                ? `Quitar filtro de destino ${destination.destino}`
                : `Filtrar fibras en uso con destino ${destination.destino}`);
            const label = document.createElement('div');
            label.className = 'network-ranking__label';
            const name = document.createElement('span');
            name.textContent = destination.destino;
            name.title = destination.destino;
            const value = document.createElement('b');
            const associated = Number(destination.total_asociadas ?? destination.total ?? 0);
            value.textContent = `${formatter.format(destination.total)} en uso de ${formatter.format(associated)}`;
            label.append(name, value);
            const bar = document.createElement('div');
            bar.className = 'network-ranking__bar';
            const fill = document.createElement('i');
            fill.style.width = `${Math.min(100, Number(destination.porcentaje_uso || 0))}%`;
            bar.appendChild(fill);
            const detail = document.createElement('small');
            detail.textContent = `${formatter.format(destination.ocupadas)} ocupadas · ${formatter.format(destination.reservadas)} reservadas · ${formatter.format(associated)} asociadas`;
            item.append(label, bar, detail);
            const select = () => {
                const search = root.querySelector('[data-resource="fibras"] [data-filter="q"]');
                const destinationFilter = root.querySelector('[data-resource="fibras"] [data-filter="destino"]');
                const inUseFilter = root.querySelector('[data-resource="fibras"] [data-filter="en_uso"]');
                if (!search || !destinationFilter || !inUseFilter || !fibers) return;
                const alreadySelected = destinationFilter.value === destination.destino && inUseFilter.value === '1';
                destinationFilter.value = alreadySelected ? '' : destination.destino;
                inUseFilter.value = alreadySelected ? '' : '1';
                search.value = '';
                syncFiberDestinationContext();
                fibers.page = 1;
                fibers.load();
            };
            item.addEventListener('click', select);
            item.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    select();
                }
            });
            ranking.appendChild(item);
        });
    }

    function renderReserveSummary(summary) {
        put('reserve-summary-total', summary.total);
        const meters = document.getElementById('reserve-summary-meters');
        if (meters) meters.textContent = `${formatter.format(summary.reserva_m || 0)} m`;
        const list = document.getElementById('reserve-type-summary');
        if (!list) return;
        list.replaceChildren();
        const types = Array.isArray(summary.tipos_detalle) ? summary.tipos_detalle : [];
        if (!types.length) {
            const empty = document.createElement('p');
            empty.className = 'network-inspector-empty';
            empty.textContent = 'No hay elementos con los filtros actuales.';
            list.appendChild(empty);
            return;
        }
        types.forEach((type) => {
            const item = document.createElement('div');
            item.className = 'network-type-list__item';
            const label = document.createElement('div');
            label.className = 'network-type-list__label';
            const name = document.createElement('span');
            name.textContent = type.tipo;
            const value = document.createElement('b');
            value.textContent = formatter.format(type.total);
            label.append(name, value);
            item.appendChild(label);
            if (Number(type.reserva_m || 0) > 0) {
                const detail = document.createElement('small');
                detail.textContent = `${formatter.format(type.reserva_m)} m documentados`;
                item.appendChild(detail);
            }
            list.appendChild(item);
        });
    }

    function renderMapEmpty(target, title, detail) {
        target.classList.add('is-empty');
        const empty = document.createElement('div');
        empty.className = 'network-map-empty';
        const icon = document.createElement('span');
        icon.textContent = '⌁';
        const heading = document.createElement('strong');
        heading.textContent = title;
        const description = document.createElement('small');
        description.textContent = detail;
        empty.append(icon, heading, description);
        target.replaceChildren(empty);
    }

    function drawRouteMap(payload, targetId = 'network-route-map') {
        const target = document.getElementById(targetId);
        if (!target) return;
        if (routeMap) {
            routeMap.remove();
            routeMap = null;
        }
        target.replaceChildren();
        target.classList.remove('is-empty');
        const coordinates = (payload.coordenadas || [])
            .map((point) => [Number(point[0]), Number(point[1])])
            .filter((point) => Number.isFinite(point[0]) && Number.isFinite(point[1]));
        const markers = (payload.puntos || [])
            .map((point) => ({ ...point, latitud: Number(point.latitud), longitud: Number(point.longitud) }))
            .filter((point) => Number.isFinite(point.latitud) && Number.isFinite(point.longitud));
        if (!window.L) {
            renderMapEmpty(
                target,
                'Mapa no disponible',
                'No se pudo iniciar el visor cartográfico. Recarga la página e inténtalo nuevamente.',
            );
            return;
        }
        if (coordinates.length < 2 && !markers.length) {
            renderMapEmpty(
                target,
                'Sin trazado georreferenciado',
                'La ruta no tiene suficientes coordenadas para dibujar su recorrido.',
            );
            return;
        }
        routeMap = L.map(target, {
            zoomControl: true,
            attributionControl: true,
            scrollWheelZoom: false,
            preferCanvas: true,
        });
        const dark = document.documentElement.dataset.theme === 'dark';
        L.tileLayer(
            dark
                ? 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
                : 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
            { maxZoom: 19, attribution: '&copy; OpenStreetMap &copy; CARTO' },
        ).addTo(routeMap);
        const bounds = L.latLngBounds([]);
        if (coordinates.length >= 2) {
            const line = L.polyline(coordinates, { color: '#216cf4', weight: 4, opacity: .95 }).addTo(routeMap);
            bounds.extend(line.getBounds());
        }
        markers.forEach((point) => {
            const marker = L.circleMarker([point.latitud, point.longitud], {
                radius: 4,
                color: '#fff',
                weight: 2,
                fillColor: '#f59e0b',
                fillOpacity: 1,
            }).bindTooltip(`${point.tipo}: ${point.nombre}`);
            marker.addTo(routeMap);
            bounds.extend(marker.getLatLng());
        });
        if (bounds.isValid()) routeMap.fitBounds(bounds, { padding: [18, 18], maxZoom: 15 });
        setTimeout(() => routeMap && routeMap.invalidateSize(), 80);
    }

    function fiberStateLabel(value) {
        return {
            DISPONIBLE: 'Disponible',
            OCUPADO: 'Ocupado',
            RESERVADO: 'Reservado',
            SIN_INFORMACION: 'Sin información',
        }[value] || 'Sin información';
    }

    function fiberStateSource(value) {
        return {
            INFORMADO: 'Informado en el inventario',
            INFERIDO_TRAMOS: 'Inferido desde sus tramos',
            NO_INFORMADO: 'No informado',
        }[value] || 'No informado';
    }

    function fiberCondition(value) {
        return {
            OPERATIVA: 'Operativa',
            CON_FALLA: 'Con falla',
            SIN_VERIFICAR: 'Sin verificar',
        }[value] || 'Sin verificar';
    }

    function fiberEndpoint(endpoint) {
        if (endpoint?.confirmado) return `${endpoint.odf || 'ODF'} · Puerto ${endpoint.puerto || '—'}`;
        return 'Sin terminación confirmada';
    }

    async function loadFiberPanel(item, selectedRow) {
        if (!item || !item.id) return;
        root.querySelectorAll('.odf-table tbody tr.is-selected').forEach((row) => row.classList.remove('is-selected'));
        if (selectedRow) selectedRow.classList.add('is-selected');
        setInspectorCollapsed(false);
        const empty = document.getElementById('fiber-selected-empty');
        const detail = document.getElementById('fiber-selected-detail');
        const caption = document.getElementById('fiber-selected-caption');
        const status = document.getElementById('fiber-selected-status');
        const displayedState = fiberStateLabel(item.estado);
        if (empty) empty.hidden = true;
        if (detail) detail.hidden = false;
        if (caption) caption.textContent = `${displayedState} · ${fiberStateSource(item.origen_estado)}`;
        document.getElementById('fiber-selected-name').textContent = `${item.troncal} · Fibra ${item.numero}`;
        status.textContent = displayedState;
        status.className = 'network-asset-status';
        if (item.estado === 'OCUPADO') status.classList.add('is-used');
        else if (item.estado === 'RESERVADO') status.classList.add('is-reserved');
        else if (item.estado === 'SIN_INFORMACION') status.classList.add('is-unknown');
        document.getElementById('fiber-selected-state-source').textContent = fiberStateSource(item.origen_estado);
        document.getElementById('fiber-selected-condition').textContent = fiberCondition(item.condicion_fisica);
        document.getElementById('fiber-selected-service').textContent = item.servicio && item.servicio !== '—' ? item.servicio : 'Sin servicio asignado';
        document.getElementById('fiber-selected-end-a').textContent = fiberEndpoint(item.terminacion_a);
        document.getElementById('fiber-selected-end-b').textContent = fiberEndpoint(item.terminacion_b);
        document.getElementById('fiber-selected-route').textContent = item.troncal;
        document.getElementById('fiber-selected-segments').textContent = item.ruta_id ? `${formatter.format(item.tramos_total)} tramos` : 'Troncal pendiente';
        const detailLink = document.getElementById('fiber-selected-detail-link');
        detailLink.href = item.detail_url;
        const mapLink = document.getElementById('fiber-selected-map-link');
        mapLink.hidden = !item.ruta_id;
        if (!item.ruta_id || !root.dataset.routePanelApi) {
            const mapTarget = document.getElementById('fiber-selected-map');
            if (routeMap) { routeMap.remove(); routeMap = null; }
            renderMapEmpty(mapTarget, 'Recorrido pendiente', 'La fibra todavía no está asociada a una troncal.');
            return;
        }
        if (routeRequest) routeRequest.abort();
        routeRequest = new AbortController();
        const url = new URL(root.dataset.routePanelApi, location.origin);
        url.searchParams.set('ruta_id', item.ruta_id);
        try {
            const response = await fetch(url, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                signal: routeRequest.signal,
                cache: 'no-store',
            });
            if (!response.ok) throw new Error();
            const payload = await response.json();
            mapLink.href = payload.ruta.mapa_url;
            drawRouteMap(payload, 'fiber-selected-map');
        } catch (error) {
            if (error.name === 'AbortError') return;
            const mapTarget = document.getElementById('fiber-selected-map');
            if (routeMap) { routeMap.remove(); routeMap = null; }
            renderMapEmpty(mapTarget, 'No se pudo cargar el recorrido', 'Intenta seleccionar nuevamente la fibra.');
        }
    }

    async function loadRoutePanel(item, selectedRow) {
        if (!item || !item.ruta_id || !root.dataset.routePanelApi) return;
        root.querySelectorAll('.odf-table tbody tr.is-selected').forEach((row) => row.classList.remove('is-selected'));
        if (selectedRow) selectedRow.classList.add('is-selected');
        setInspectorCollapsed(false);
        const state = document.getElementById('network-route-state');
        const empty = document.getElementById('network-route-empty');
        const detail = document.getElementById('network-route-detail');
        if (state) state.textContent = 'Cargando detalle…';
        if (empty) empty.hidden = false;
        if (detail) detail.hidden = true;
        if (routeRequest) routeRequest.abort();
        routeRequest = new AbortController();
        const url = new URL(root.dataset.routePanelApi, location.origin);
        url.searchParams.set('ruta_id', item.ruta_id);
        try {
            const response = await fetch(url, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                signal: routeRequest.signal,
            });
            if (!response.ok) throw new Error();
            const payload = await response.json();
            if (empty) empty.hidden = true;
            if (detail) detail.hidden = false;
            if (state) state.textContent = payload.ruta.tipos_trazado.join(' · ') || 'Sin trazado clasificado';
            document.getElementById('network-route-name').textContent = payload.ruta.nombre;
            document.getElementById('network-route-utilization').textContent = `${formatter.format(payload.fibras.utilizacion)}%`;
            document.getElementById('network-route-distance').textContent = `${formatter.format(payload.ruta.distancia_km)} km`;
            document.getElementById('network-route-origin').textContent = payload.ruta.origen;
            document.getElementById('network-route-destination').textContent = payload.ruta.destino;
            document.getElementById('network-route-fibers').textContent = `${formatter.format(payload.fibras.ocupadas)} ocupados · ${formatter.format(payload.fibras.libres)} libres`;
            document.getElementById('network-route-reserves').textContent = `${formatter.format(payload.reservas.elementos)} elementos · ${formatter.format(payload.reservas.reserva_m)} m`;
            const open = document.getElementById('network-route-open');
            open.href = payload.ruta.mapa_url;
            open.hidden = false;
            drawRouteMap(payload);
        } catch (error) {
            if (error.name === 'AbortError') return;
            if (state) state.textContent = 'No se pudo cargar';
            if (empty) {
                empty.hidden = false;
                const message = empty.querySelector('p');
                if (message) message.textContent = 'No fue posible obtener el detalle de esta troncal.';
            }
        }
    }

    function selectableRouteRow(row, item) {
        if (!item.ruta_id) return row;
        row.dataset.routeId = item.ruta_id;
        row.tabIndex = 0;
        row.addEventListener('click', () => loadRoutePanel(item, row));
        row.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                loadRoutePanel(item, row);
            }
        });
        return row;
    }

    function selectableFiberRow(row, item) {
        if (!item.id) return row;
        row.dataset.fiberId = item.id;
        row.tabIndex = 0;
        row.setAttribute('aria-label', `Ver detalle de la fibra ${item.numero}`);
        row.addEventListener('click', () => loadFiberPanel(item, row));
        row.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                loadFiberPanel(item, row);
            }
        });
        return row;
    }

    class InventoryTable {
        constructor(resource, options) {
            this.options = options; this.panel = root.querySelector(`[data-resource="${resource}"]`); if (!this.panel) return;
            this.form = this.panel.querySelector('form'); this.body = this.panel.querySelector('[data-body]'); this.message = this.panel.querySelector('[data-message]'); this.info = this.panel.querySelector('[data-page-info]'); this.pagination = this.panel.querySelector('[data-pagination]'); this.previous = this.panel.querySelector('[data-previous]'); this.next = this.panel.querySelector('[data-next]');
            this.page = 1; this.loaded = false; this.controller = null; this.timer = null; this.bind();
        }
        params(includePage = true) { const params = new URLSearchParams(); this.form.querySelectorAll('[data-filter]').forEach((input) => { if (input.value.trim()) params.set(input.dataset.filter, input.value.trim()); }); if (!params.has('page_size')) params.set('page_size', '25'); if (includePage) params.set('page', this.page); return params; }
        rows(items) {
            this.body.replaceChildren();
            if (!items.length) { const row = document.createElement('tr'); const empty = cell('No hay registros con los filtros seleccionados.'); empty.colSpan = this.options.columns; empty.className = 'odf-table__empty'; row.appendChild(empty); this.body.appendChild(row); return; }
            items.forEach((item) => this.body.appendChild(this.options.row(item)));
        }
        pages(meta) {
            this.pagination.replaceChildren(); const values = [...new Set([1, meta.total_pages, meta.page - 1, meta.page, meta.page + 1])].filter((p) => p > 0 && p <= meta.total_pages).sort((a, b) => a - b); let previous = 0;
            values.forEach((value) => { if (previous && value - previous > 1) { const dots = document.createElement('span'); dots.className = 'odf-page-ellipsis'; dots.textContent = '…'; this.pagination.appendChild(dots); } const button = document.createElement('button'); button.type = 'button'; button.className = 'odf-page-number'; button.textContent = value; if (value === meta.page) button.classList.add('is-current'); button.addEventListener('click', () => { this.page = value; this.load(); }); this.pagination.appendChild(button); previous = value; });
        }
        async load() {
            if (!this.panel) return; if (this.controller) this.controller.abort(); this.controller = new AbortController(); this.message.className = 'odf-message'; this.message.textContent = 'Consultando inventario…'; const url = new URL(this.options.api, location.origin); url.search = this.params().toString();
            try {
                const response = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, signal: this.controller.signal, cache: 'no-store' }); if (!response.ok) throw new Error(); const payload = await response.json(); const meta = payload.pagination;
                this.page = meta.page; this.rows(payload.data || []); this.options.summary(payload.summary || {}); const start = meta.total ? ((meta.page - 1) * meta.page_size) + 1 : 0; const end = Math.min(meta.page * meta.page_size, meta.total); this.info.textContent = `Mostrando ${start} a ${end} de ${formatter.format(meta.total)} resultados`; this.previous.disabled = !meta.has_previous; this.next.disabled = !meta.has_next; this.pages(meta); this.message.textContent = ''; this.loaded = true;
            } catch (error) { if (error.name === 'AbortError') return; this.message.classList.add('is-error'); this.message.textContent = 'No se pudo cargar el inventario.'; }
        }
        bind() {
            this.form.addEventListener('submit', (event) => { event.preventDefault(); this.page = 1; this.load(); }); const search = this.form.querySelector('[data-filter="q"]');
            search.addEventListener('input', () => { clearTimeout(this.timer); this.timer = setTimeout(() => { this.page = 1; this.load(); }, 350); }); this.form.querySelectorAll('select[data-filter]').forEach((select) => select.addEventListener('change', () => { this.page = 1; this.load(); }));
            this.form.querySelector('[data-clear]').addEventListener('click', () => { this.form.reset(); if (this.options.clearContext) this.options.clearContext(this.form); this.page = 1; search.focus(); this.load(); }); this.form.querySelector('[data-export]').addEventListener('click', () => exportFile(this.options.exportUrl, this.params(false), this.form.querySelector('[data-export-status]'))); this.previous.addEventListener('click', () => { if (this.page > 1) { this.page -= 1; this.load(); } }); this.next.addEventListener('click', () => { this.page += 1; this.load(); });
        }
    }

    function clearFiberDestinationContext(form) {
        const destination = form.querySelector('[data-filter="destino"]');
        const inUse = form.querySelector('[data-filter="en_uso"]');
        if (destination) destination.value = '';
        if (inUse) inUse.value = '';
        syncFiberDestinationContext();
    }

    function syncFiberDestinationContext() {
        const panel = root.querySelector('[data-resource="fibras"]');
        const context = panel?.querySelector('[data-fiber-destination-context]');
        const label = context?.querySelector('[data-fiber-destination-label]');
        const destination = panel?.querySelector('[data-filter="destino"]')?.value.trim() || '';
        const inUse = panel?.querySelector('[data-filter="en_uso"]')?.value.trim() || '';
        if (!context || !label) return;
        context.hidden = !(destination && inUse);
        label.textContent = destination;
        label.title = destination;
    }

    let fiberEditor;
    function fiberRow(item) { const row = document.createElement('tr'); row.appendChild(cell(item.troncal)); row.appendChild(cell(item.numero)); const state = document.createElement('td'); const displayedState = fiberStateLabel(item.estado); state.appendChild(badge(displayedState)); row.appendChild(state); const condition = fiberCondition(item.condicion_fisica); const conditionCell = document.createElement('td'); conditionCell.appendChild(badge(condition)); row.appendChild(conditionCell); const quality = document.createElement('td'); const qualityBadge = badge(item.completitud); const findings = item.calidad?.hallazgos || []; if (findings.length) { qualityBadge.textContent += ` · ${findings.length} obs.`; qualityBadge.title = findings.map((finding) => finding.mensaje).join('\n'); } if (item.calidad?.nivel === 'ERROR') qualityBadge.classList.add('is-danger'); else if (item.calidad?.nivel === 'ADVERTENCIA') qualityBadge.classList.add('is-warning'); quality.appendChild(qualityBadge); row.appendChild(quality); [item.servicio, item.site_inicial, item.site_final, item.origen, item.destino, item.conector].forEach((value) => row.appendChild(cell(value))); row.appendChild(actionCell(item, root.dataset.canEditFiber === 'true' ? openFiberEditor : null)); return selectableFiberRow(row, item); }
    function reserveRow(item) { const row = document.createElement('tr'); [item.troncal, item.nombre, item.tipo, item.reserva_m === null ? '—' : `${formatter.format(item.reserva_m)} m`, item.tramo, item.latitud, item.longitud].forEach((value) => row.appendChild(cell(value))); const state = document.createElement('td'); state.appendChild(badge(item.estado)); row.appendChild(state); row.appendChild(actionCell(item)); return selectableRouteRow(row, item); }
    const fibers = new InventoryTable('fibras', { api: root.dataset.fibersApi, exportUrl: root.dataset.fibersExport, columns: 12, row: fiberRow, clearContext: clearFiberDestinationContext, summary: (s) => { put('fibras-stat-total', s.total); put('fibras-stat-free', s.libres); put('fibras-stat-used', s.ocupadas); put('fibras-stat-reserved', s.reservadas); put('fibras-stat-unknown', s.sin_estado); put('fibras-stat-routes', s.troncales); renderFiberSummary(s); syncFiberDestinationContext(); } });
    const reserves = new InventoryTable('reservas', { api: root.dataset.reservesApi, exportUrl: root.dataset.reservesExport, columns: 9, row: reserveRow, summary: (s) => { put('reservas-stat-total', s.total); put('reservas-stat-meters', s.reserva_m); put('reservas-stat-types', s.tipos); put('reservas-stat-routes', s.troncales); put('reservas-stat-pending', s.por_confirmar); renderReserveSummary(s); } });

    const clearFiberDestination = root.querySelector('[data-clear-fiber-destination]');
    if (clearFiberDestination && fibers?.form) {
        clearFiberDestination.addEventListener('click', () => {
            clearFiberDestinationContext(fibers.form);
            fibers.page = 1;
            fibers.load();
        });
    }

    root.querySelectorAll('[data-fiber-state]').forEach((button) => {
        button.addEventListener('click', () => {
            const stateFilter = fibers?.form?.querySelector('[data-filter="estado"]');
            if (!stateFilter) return;
            stateFilter.value = stateFilter.value === button.dataset.fiberState
                ? ''
                : button.dataset.fiberState;
            stateFilter.dispatchEvent(new Event('change', { bubbles: true }));
        });
    });

    function setupDialog(id, triggerId, createUrl, onReset) {
        const dialog = document.getElementById(id); if (!dialog) return null; const form = dialog.querySelector('form'); const message = form.querySelector('[data-form-message]'); const trigger = document.getElementById(triggerId);
        dialog.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => dialog.close())); if (trigger) trigger.addEventListener('click', () => { form.reset(); if (form.elements.id) form.elements.id.value = ''; message.textContent = ''; if (onReset) onReset(form); dialog.showModal(); });
        form.addEventListener('submit', async (event) => {
            event.preventDefault(); message.className = 'odf-dialog__message'; message.textContent = 'Guardando…'; const payload = Object.fromEntries(new FormData(form).entries()); const editing = id === 'fiber-editor' && Boolean(payload.id);
            try { const response = await fetch(editing ? root.dataset.updateFiber : createUrl, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest' }, body: JSON.stringify(payload) }); const result = await response.json(); if (!response.ok || result.status !== 'success') throw new Error(result.message || 'No fue posible guardar'); message.classList.add('is-success'); message.textContent = result.message; setTimeout(() => dialog.close(), 550); if (fibers) fibers.loaded = false; if (reserves) reserves.loaded = false; if (id === 'fiber-editor' && fibers) fibers.load(); if (id === 'reserve-editor' && reserves) reserves.load(); }
            catch (error) { message.classList.add('is-error'); message.textContent = error.message; }
        }); return { dialog, form, message };
    }
    fiberEditor = setupDialog('fiber-editor', 'fiber-new', root.dataset.createFiber, (form) => { document.getElementById('fiber-editor-title').textContent = 'Nueva fibra'; form.elements.ruta_nombre.disabled = false; });
    setupDialog('reserve-editor', 'reserve-new', root.dataset.createReserve);
    function openFiberEditor(item) {
        if (!fiberEditor) return; const form = fiberEditor.form; form.reset(); fiberEditor.message.textContent = ''; document.getElementById('fiber-editor-title').textContent = 'Editar fibra';
        const values = { id: item.id, ruta_nombre: item.troncal_pendiente ? '' : item.troncal, fibra_numero: item.numero, estado: item.estado, condicion_fisica: item.condicion_fisica || 'SIN_VERIFICAR', nombre_fibra: item.servicio === '—' ? '' : item.servicio, tipo_conector: item.conector === '—' ? '' : item.conector, observaciones: item.observaciones || '' };
        Object.entries(values).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value; }); form.elements.ruta_nombre.disabled = !item.troncal_pendiente; fiberEditor.dialog.showModal();
    }

    const tables = { fibras: fibers, reservas: reserves }; const tabs = [...root.querySelectorAll('[role="tab"]')];
    function selectTab(name) { const tab = tabs.find((item) => item.dataset.tab === name) || tabs[0]; if (!tab) return; tabs.forEach((item) => { const active = item === tab; item.setAttribute('aria-selected', active); document.getElementById(item.getAttribute('aria-controls')).hidden = !active; }); switchInspectorContext(tab.dataset.tab); const table = tables[tab.dataset.tab]; if (table && !table.loaded) table.load(); const url = new URL(location.href); url.searchParams.set('tab', tab.dataset.tab); history.replaceState({}, '', url); }
    tabs.forEach((tab) => tab.addEventListener('click', () => selectTab(tab.dataset.tab))); const incoming = new URLSearchParams(location.search); const query = incoming.get('q'); if (query) root.querySelectorAll('[data-filter="q"]').forEach((input) => { input.value = query; }); selectTab(incoming.get('tab') || root.dataset.defaultTab);
})();
