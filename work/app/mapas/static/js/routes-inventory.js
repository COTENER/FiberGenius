(function () {
    'use strict';
    const root = document.getElementById('routes-workbench');
    if (!root) return;
    const number = new Intl.NumberFormat('es-PE', { maximumFractionDigits: 2 });
    const csrf = () => (document.cookie.match(/(?:^|; )csrftoken=([^;]+)/) || [])[1] || '';
    const inspector = document.getElementById('network-inspector');
    const inspectorToggle = document.getElementById('network-inspector-toggle');
    let routeMap = null;
    let routeRequest = null;
    let rankingContext = null;

    function setInspectorCollapsed(collapsed) {
        if (!inspector || !inspectorToggle) return;
        inspector.classList.toggle('is-collapsed', collapsed);
        inspectorToggle.setAttribute('aria-expanded', String(!collapsed));
        inspectorToggle.setAttribute('aria-label', collapsed ? 'Expandir panel' : 'Contraer panel');
        if (!collapsed && routeMap) setTimeout(() => routeMap.invalidateSize(), 240);
    }

    function renderContextSummary(kind, summary) {
        const target = document.getElementById('routes-context-summary');
        if (!target) return;
        const total = Number(summary?.total || 0);
        const percentage = (value) => total ? Math.max(0, Math.min(100, (Number(value || 0) / total) * 100)) : 0;
        const donut = document.createElement('div');
        donut.className = 'network-donut';
        if (kind === 'troncales') {
            donut.style.background = total
                ? 'conic-gradient(#2f74ee 0 100%)'
                : 'conic-gradient(#e8edf5 0 100%)';
        } else {
            const aerialPercent = percentage(summary?.aereos);
            const undergroundPercent = percentage(summary?.soterrados);
            donut.style.background = total
                ? `conic-gradient(#2f74ee 0 ${aerialPercent}%, #f59e0b ${aerialPercent}% ${aerialPercent + undergroundPercent}%, #94a3b8 ${aerialPercent + undergroundPercent}% 100%)`
                : 'conic-gradient(#e8edf5 0 100%)';
        }
        const value = document.createElement('span');
        value.textContent = number.format(total);
        const caption = document.createElement('small');
        caption.textContent = kind === 'troncales' ? 'troncales' : 'tramos';
        donut.append(value, caption);
        const list = document.createElement('ul');
        const items = kind === 'troncales'
            ? [
                ['is-used', 'Fibras', summary?.fibras, 'hilos'],
                ['is-free', 'Tramos', summary?.tramos, total ? `${number.format(Number(summary?.tramos || 0) / total)} por ruta` : '0 por ruta'],
                ['is-reserved', 'Reservas físicas', summary?.reservas, 'elementos'],
            ]
            : [
                ['is-used', 'Aéreos', summary?.aereos, `${number.format(percentage(summary?.aereos))}%`],
                ['is-reserved', 'Soterrados', summary?.soterrados, `${number.format(percentage(summary?.soterrados))}%`],
                ['is-unknown', 'Sin clasificar', Math.max(0, total - Number(summary?.aereos || 0) - Number(summary?.soterrados || 0)), `${number.format(percentage(Math.max(0, total - Number(summary?.aereos || 0) - Number(summary?.soterrados || 0))))}%`],
            ];
        items.forEach(([className, label, amount, context]) => {
            const item = document.createElement('li');
            const row = document.createElement('div');
            row.className = 'network-state-line';
            const dot = document.createElement('i');
            dot.className = className;
            const text = document.createElement('span');
            text.textContent = label;
            const totalNode = document.createElement('b');
            const count = document.createElement('span');
            count.textContent = number.format(amount || 0);
            const detail = document.createElement('small');
            detail.textContent = context;
            totalNode.append(count, detail);
            row.append(dot, text, totalNode);
            item.appendChild(row);
            list.appendChild(item);
        });
        target.replaceChildren(donut, list);
        const source = document.getElementById('routes-context-source');
        if (source) {
            source.replaceChildren();
            const primary = document.createElement('span');
            const secondary = document.createElement('span');
            if (kind === 'troncales') {
                primary.textContent = number.format(summary?.distancia_km || 0);
                secondary.textContent = number.format(summary?.tramos || 0);
                source.append(primary, ' km documentados · ', secondary, ' tramos');
            } else {
                primary.textContent = number.format(summary?.reservas_m || 0);
                secondary.textContent = number.format(Math.max(0, total - Number(summary?.aereos || 0) - Number(summary?.soterrados || 0)));
                source.append(primary, ' m de reserva · ', secondary, ' sin clasificar');
            }
        }
        renderRouteRanking(summary?.top_ocupacion || [], kind);
    }

    function renderRouteRanking(items, kind) {
        const target = document.getElementById('routes-occupancy-ranking');
        if (!target) return;
        target.replaceChildren();
        if (!Array.isArray(items) || !items.length) {
            const empty = document.createElement('p');
            empty.className = 'network-inspector-empty';
            empty.textContent = 'No hay capacidad registrada con los filtros actuales.';
            target.appendChild(empty);
            return;
        }
        items.forEach((route) => {
            const item = document.createElement('div');
            item.className = 'network-ranking__item';
            item.tabIndex = 0;
            item.setAttribute('role', 'button');
            item.setAttribute('aria-label', `Filtrar ${route.nombre}`);
            const table = tables[kind];
            const selected = rankingContext?.kind === kind
                && rankingContext.id === String(route.id);
            item.classList.toggle('is-selected', selected);
            item.setAttribute('aria-pressed', String(selected));
            const label = document.createElement('div');
            label.className = 'network-ranking__label';
            const name = document.createElement('span');
            name.textContent = route.nombre;
            name.title = route.nombre;
            const value = document.createElement('b');
            value.textContent = `${number.format(route.utilizados || 0)} en uso de ${number.format(route.total || 0)}`;
            label.append(name, value);
            const bar = document.createElement('div');
            bar.className = 'network-ranking__bar';
            const fill = document.createElement('i');
            fill.style.width = `${Math.min(100, Number(route.utilizacion || 0))}%`;
            bar.appendChild(fill);
            const detail = document.createElement('small');
            const notes = [`${number.format(route.utilizacion || 0)}% de ocupación`];
            if (Number(route.sin_inventariar || 0)) notes.push(`${number.format(route.sin_inventariar)} sin inventariar`);
            if (Number(route.hilos_sin_estado || 0)) notes.push(`${number.format(route.hilos_sin_estado)} sin estado`);
            detail.textContent = notes.join(' · ');
            item.append(label, bar, detail);
            const select = () => {
                if (!table) return;
                if (selected) {
                    rankingContext = null;
                } else {
                    rankingContext = { kind, id: String(route.id), label: route.nombre };
                }
                syncRankingContexts();
                table.page = 1;
                table.load();
            };
            item.addEventListener('click', select);
            item.addEventListener('keydown', event => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    select();
                }
            });
            target.appendChild(item);
        });
    }

    function syncRankingContexts() {
        root.querySelectorAll('[data-ranking-context]').forEach((context) => {
            const form = context.closest('form');
            const panel = context.closest('[data-resource]');
            const kind = panel?.dataset.resource;
            const active = Boolean(
                rankingContext
                && rankingContext.kind === kind
            );
            context.hidden = !active;
            const label = context.querySelector('[data-ranking-context-label]');
            if (label) label.textContent = active ? rankingContext.label : '';
        });
    }

    function clearRankingContext(table, { reload = true } = {}) {
        if (rankingContext?.kind === table.resource) rankingContext = null;
        syncRankingContexts();
        table.page = 1;
        if (reload) table.load();
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

    function drawRouteMap(payload) {
        const target = document.getElementById('network-route-map');
        if (!target) return;
        if (routeMap) routeMap.remove();
        routeMap = null;
        target.replaceChildren();
        target.classList.remove('is-empty');
        const coordinates = (payload.coordenadas || [])
            .map(point => [Number(point[0]), Number(point[1])])
            .filter(point => Number.isFinite(point[0]) && Number.isFinite(point[1]));
        if (!window.L) {
            renderMapEmpty(
                target,
                'Mapa no disponible',
                'No se pudo iniciar el visor cartográfico. Recarga la página e inténtalo nuevamente.',
            );
            return;
        }
        if (coordinates.length < 2) {
            renderMapEmpty(
                target,
                'Sin trazado georreferenciado',
                'La ruta no tiene suficientes coordenadas para dibujar su recorrido.',
            );
            return;
        }
        routeMap = L.map(target, { zoomControl: true, attributionControl: true, scrollWheelZoom: false, preferCanvas: true });
        const dark = document.documentElement.dataset.theme === 'dark';
        L.tileLayer(
            dark ? 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png' : 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
            { maxZoom: 19, attribution: '&copy; OpenStreetMap &copy; CARTO' },
        ).addTo(routeMap);
        const line = L.polyline(coordinates, { color: '#7657ed', weight: 5, opacity: .96 }).addTo(routeMap);
        routeMap.fitBounds(line.getBounds(), { padding: [18, 18], maxZoom: 15 });
        setTimeout(() => routeMap?.invalidateSize(), 80);
    }

    function metric(label, value) {
        const wrapper = document.createElement('span');
        const name = document.createElement('small');
        name.textContent = label;
        const content = document.createElement('b');
        content.textContent = value;
        wrapper.append(name, content);
        return wrapper;
    }

    function meta(label, value) {
        const wrapper = document.createElement('div');
        const term = document.createElement('dt');
        term.textContent = label;
        const content = document.createElement('dd');
        content.textContent = value || '—';
        wrapper.append(term, content);
        return wrapper;
    }

    async function selectRoute(rutaId, row) {
        if (!rutaId || !root.dataset.routePanelApi) return;
        root.querySelectorAll('.odf-table tbody tr.is-selected').forEach(item => item.classList.remove('is-selected'));
        row?.classList.add('is-selected');
        setInspectorCollapsed(false);
        const empty = document.getElementById('network-route-empty');
        const detail = document.getElementById('network-route-detail');
        empty.hidden = false;
        empty.querySelector('strong').textContent = 'Cargando ruta…';
        detail.hidden = true;
        routeRequest?.abort();
        routeRequest = new AbortController();
        const url = new URL(root.dataset.routePanelApi, location.origin);
        url.searchParams.set('ruta_id', rutaId);
        try {
            const response = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, signal: routeRequest.signal, cache: 'no-store' });
            if (!response.ok) throw new Error();
            const payload = await response.json();
            empty.hidden = true;
            detail.hidden = false;
            document.getElementById('network-route-name').textContent = payload.ruta.nombre;
            const metrics = document.getElementById('network-route-metrics');
            metrics.replaceChildren(
                metric('Utilización', `${number.format(payload.fibras.utilizacion)}%`),
                metric('Distancia', `${number.format(payload.ruta.distancia_km)} km`),
                metric('Fibras', number.format(payload.fibras.total)),
                metric('Reservas', number.format(payload.reservas.elementos)),
            );
            document.getElementById('network-route-meta').replaceChildren(
                meta('Origen', payload.ruta.origen),
                meta('Destino', payload.ruta.destino),
                meta('Trazado', payload.ruta.tipos_trazado.join(' · ')),
                meta('Reserva lineal', `${number.format(payload.reservas.reserva_m)} m`),
            );
            drawRouteMap(payload);
        } catch (error) {
            if (error.name === 'AbortError') return;
            empty.hidden = false;
            empty.querySelector('strong').textContent = 'No se pudo cargar';
            empty.querySelector('p').textContent = 'Intenta seleccionar la ruta nuevamente.';
        }
    }

    function makeSelectable(row, rutaId, label) {
        if (!rutaId) return row;
        row.dataset.routeId = rutaId;
        row.tabIndex = 0;
        row.setAttribute('aria-label', `Ver detalle de ${label}`);
        row.addEventListener('click', () => selectRoute(rutaId, row));
        row.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                selectRoute(rutaId, row);
            }
        });
        return row;
    }

    function cell(value, className) {
        const td = document.createElement('td');
        td.textContent = value === null || value === undefined || value === '' ? '—' : value;
        if (className) td.className = className;
        return td;
    }
    function badge(value) {
        const span = document.createElement('span');
        const state = String(value || '').toLowerCase();
        span.className = 'odf-status';
        if (state.includes('manten') || state.includes('construc') || state.includes('reserv')) span.classList.add('is-warning');
        if (state.includes('fuera') || state.includes('baja') || state.includes('error')) span.classList.add('is-danger');
        if (state.includes('sin estado') || state.includes('clasificar')) span.classList.add('is-neutral');
        span.textContent = value || 'Sin estado';
        return span;
    }
    function iconButton(label, symbol, handler, extraClass) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `odf-icon-action ${extraClass || ''}`;
        button.title = label;
        button.setAttribute('aria-label', label);
        button.textContent = symbol;
        button.addEventListener('click', event => {
            event.stopPropagation();
            handler(event);
        });
        return button;
    }
    function actions(item, canEdit, editHandler) {
        const td = document.createElement('td');
        const wrap = document.createElement('div');
        wrap.className = 'network-actions';
        wrap.appendChild(iconButton('Abrir ficha 360°', '◉', () => {
            if (window.FiberGenius && window.FiberGenius.openAsset360) window.FiberGenius.openAsset360(item.detail_url);
            else window.location.assign(item.detail_url);
        }));
        if (canEdit && editHandler) wrap.appendChild(iconButton('Editar', '✎', () => editHandler(item)));
        td.appendChild(wrap);
        return td;
    }
    async function download(url, params, status) {
        status.className = 'odf-export-status';
        status.textContent = 'Generando archivo…';
        try {
            const target = new URL(url, window.location.origin);
            target.search = params.toString();
            const response = await fetch(target, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const blob = await response.blob();
            const disposition = response.headers.get('Content-Disposition') || '';
            const match = disposition.match(/filename="?([^";]+)"?/i);
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = match ? match[1] : 'FiberGenius.xlsx';
            document.body.appendChild(link); link.click(); link.remove();
            URL.revokeObjectURL(link.href);
            status.classList.add('is-success'); status.textContent = 'Archivo descargado correctamente.';
        } catch (_error) {
            status.classList.add('is-error'); status.textContent = 'No se pudo generar el archivo. Intenta nuevamente.';
        }
    }

    class InventoryTable {
        constructor(resource, options) {
            this.resource = resource;
            this.options = options;
            this.panel = root.querySelector(`[data-resource="${resource}"]`);
            this.form = this.panel.querySelector('form');
            this.body = this.panel.querySelector('[data-body]');
            this.message = this.panel.querySelector('[data-message]');
            this.info = this.panel.querySelector('[data-page-info]');
            this.pagination = this.panel.querySelector('[data-pagination]');
            this.previous = this.panel.querySelector('[data-previous]');
            this.next = this.panel.querySelector('[data-next]');
            this.page = 1; this.loaded = false; this.controller = null; this.timer = null;
            this.bind();
        }
        params(includePage = true) {
            const params = new URLSearchParams();
            this.form.querySelectorAll('[data-filter]').forEach((input) => {
                const value = input.value.trim();
                if (value) params.set(input.dataset.filter, value);
            });
            if (!params.has('page_size')) params.set('page_size', '25');
            if (rankingContext?.kind === this.resource) params.set('ranking_id', rankingContext.id);
            if (includePage) params.set('page', this.page);
            return params;
        }
        renderSummary(summary) { this.options.summary(summary || {}); }
        renderRows(rows) {
            this.body.replaceChildren();
            if (!rows.length) {
                const row = document.createElement('tr'); const empty = cell('No hay registros con los filtros seleccionados.');
                empty.colSpan = this.options.columns; empty.className = 'odf-table__empty'; row.appendChild(empty); this.body.appendChild(row); return;
            }
            rows.forEach((item) => this.body.appendChild(this.options.row(item)));
        }
        renderPagination(meta) {
            this.pagination.replaceChildren();
            const pages = new Set([1, meta.total_pages, meta.page - 1, meta.page, meta.page + 1]);
            let last = 0;
            [...pages].filter((p) => p >= 1 && p <= meta.total_pages).sort((a, b) => a - b).forEach((p) => {
                if (last && p - last > 1) { const dots = document.createElement('span'); dots.className = 'odf-page-ellipsis'; dots.textContent = '…'; this.pagination.appendChild(dots); }
                const button = document.createElement('button'); button.type = 'button'; button.className = 'odf-page-number'; button.textContent = p;
                if (p === meta.page) { button.classList.add('is-current'); button.setAttribute('aria-current', 'page'); }
                button.addEventListener('click', () => { this.page = p; this.load(); }); this.pagination.appendChild(button); last = p;
            });
        }
        async load() {
            if (this.controller) this.controller.abort();
            this.controller = new AbortController(); this.message.className = 'odf-message'; this.message.textContent = 'Consultando inventario…';
            const url = new URL(this.options.api, window.location.origin); url.search = this.params().toString();
            try {
                const response = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, signal: this.controller.signal });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const payload = await response.json(); const meta = payload.pagination;
                this.page = meta.page; this.renderRows(payload.data || []); this.renderSummary(payload.summary);
                const start = meta.total ? ((meta.page - 1) * meta.page_size) + 1 : 0; const end = Math.min(meta.page * meta.page_size, meta.total);
                this.info.textContent = `Mostrando ${start} a ${end} de ${number.format(meta.total)} resultados`;
                this.previous.disabled = !meta.has_previous; this.next.disabled = !meta.has_next; this.renderPagination(meta);
                this.message.textContent = ''; this.loaded = true;
            } catch (error) {
                if (error.name === 'AbortError') return;
                this.message.classList.add('is-error'); this.message.textContent = 'No se pudo cargar el inventario.';
            }
        }
        bind() {
            this.form.addEventListener('submit', (event) => { event.preventDefault(); this.page = 1; this.load(); });
            const search = this.form.querySelector('[data-filter="q"]');
            search.addEventListener('input', () => {
                clearTimeout(this.timer);
                this.timer = setTimeout(() => { this.page = 1; this.load(); }, 350);
            });
            this.form.querySelectorAll('select[data-filter]').forEach((select) => select.addEventListener('change', () => { this.page = 1; this.load(); }));
            this.form.querySelector('[data-clear]').addEventListener('click', () => {
                this.form.reset();
                if (rankingContext?.kind === this.resource) rankingContext = null;
                syncRankingContexts();
                this.page = 1;
                search.focus();
                this.load();
            });
            this.form.querySelector('[data-ranking-context-clear]')?.addEventListener('click', () => clearRankingContext(this));
            this.form.querySelector('[data-export]').addEventListener('click', () => download(this.options.exportUrl, this.params(false), this.form.querySelector('[data-export-status]')));
            this.previous.addEventListener('click', () => { if (this.page > 1) { this.page -= 1; this.load(); } });
            this.next.addEventListener('click', () => { this.page += 1; this.load(); });
        }
    }

    let routesTable;
    const canEditRoute = root.dataset.canEditRoute === 'true';
    function routeRow(item) {
        const row = document.createElement('tr');
        [item.nombre, item.origen, item.destino].forEach((value) => row.appendChild(cell(value)));
        row.appendChild(cell(item.tipo)); row.appendChild(cell(`${number.format(item.distancia_km)} km`, 'odf-number'));
        [item.tramos, item.capacidad, item.fibras, item.reservas].forEach((value) => row.appendChild(cell(value, 'odf-number')));
        const state = document.createElement('td'); state.appendChild(badge(item.estado)); row.appendChild(state);
        row.appendChild(actions(item, canEditRoute, openRouteEditor));
        return makeSelectable(row, item.id, item.nombre);
    }
    function tramoRow(item) {
        const row = document.createElement('tr');
        [item.troncal, item.secuencia, item.origen, item.destino, item.tipo, `${number.format(item.distancia_km)} km`, item.capacidad, item.ocupados, item.libres, `${number.format(item.reservas_m)} m`].forEach((value) => row.appendChild(cell(value)));
        const state = document.createElement('td'); state.appendChild(badge(item.estado)); row.appendChild(state); row.appendChild(actions(item, false));
        return makeSelectable(row, item.ruta_id, item.troncal);
    }
    const text = (id, value) => { const node = document.getElementById(id); if (node) node.textContent = number.format(value || 0); };
    routesTable = new InventoryTable('troncales', {
        api: root.dataset.troncalesApi, exportUrl: root.dataset.troncalesExport, columns: 11, row: routeRow,
        summary: (s) => { text('troncales-stat-total', s.total); text('troncales-stat-distance', s.distancia_km); text('troncales-stat-segments', s.tramos); text('troncales-stat-fibers', s.fibras); text('troncales-stat-reserves', s.reservas); renderContextSummary('troncales', s); },
    });
    const tramosTable = new InventoryTable('tramos', {
        api: root.dataset.tramosApi, exportUrl: root.dataset.tramosExport, columns: 12, row: tramoRow,
        summary: (s) => { text('tramos-stat-total', s.total); text('tramos-stat-distance', s.distancia_km); text('tramos-stat-aerial', s.aereos); text('tramos-stat-underground', s.soterrados); text('tramos-stat-reserve', s.reservas_m); renderContextSummary('tramos', s); },
    });

    function dialogSetup(id, triggerId, submitUrl, beforeOpen) {
        const dialog = document.getElementById(id); const trigger = document.getElementById(triggerId);
        if (!dialog) return null; const form = dialog.querySelector('form'); const message = form.querySelector('[data-form-message]');
        dialog.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => dialog.close()));
        if (trigger) trigger.addEventListener('click', () => { form.reset(); message.textContent = ''; if (beforeOpen) beforeOpen(form); dialog.showModal(); });
        form.addEventListener('submit', async (event) => {
            event.preventDefault(); message.className = 'odf-dialog__message'; message.textContent = 'Guardando…';
            let payload = Object.fromEntries(new FormData(form).entries());
            const editing = id === 'route-editor' && Boolean(payload.nombre_original);
            if (editing) payload = InventoryEdit.formPatch(form, ['nombre_original']);
            try {
                const response = await fetch(editing ? root.dataset.updateRoute : submitUrl, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest' }, body: JSON.stringify(payload) });
                const result = await response.json(); if (!response.ok || result.status !== 'success') throw new Error(result.message || 'No fue posible guardar');
                message.classList.add('is-success'); message.textContent = result.message; setTimeout(() => dialog.close(), 550);
                routesTable.loaded = false; tramosTable.loaded = false; routesTable.load();
            } catch (error) { message.classList.add('is-error'); message.textContent = error.message; }
        }); return { dialog, form, message };
    }
    const routeEditor = dialogSetup('route-editor', 'route-new', root.dataset.createRoute, (form) => { form.elements.nombre_original.value = ''; [...form.elements].forEach(input => { input.disabled = false; }); document.getElementById('route-editor-title').textContent = 'Nueva troncal'; });
    dialogSetup('tramo-editor', 'tramo-new', root.dataset.createTramo);
    function openRouteEditor(item) {
        if (!routeEditor) return; const form = routeEditor.form; form.reset(); routeEditor.message.textContent = '';
        document.getElementById('route-editor-title').textContent = 'Editar troncal';
        const typeMap = { 'AÉREO': 'AEREO', 'HÍBRIDO': 'HIBRIDO' };
        const values = { nombre_original: item.nombre, nombre: item.nombre, hub_origen: item.origen === '—' ? '' : item.origen, destino: item.destino === '—' ? '' : item.destino, estado: item.estado, distancia_km: item.distancia_km, capacidad: item.capacidad === '—' ? '' : item.capacidad, tipo_fibra: item.tipo_fibra === '—' ? '' : item.tipo_fibra, odf_nombre: item.odf === '—' ? '' : item.odf, tipo_trazado: typeMap[item.tipo] || item.tipo, reserva_km: item.reserva_km };
        Object.assign(values, item.edicion || {});
        [...form.elements].forEach(input => {
            input.disabled = Boolean(item.edicion) && input.name in values && input.name !== 'nombre_original' && !(input.name in item.edicion);
        });
        Object.entries(values).forEach(([key, value]) => {
            const input = form.elements[key];
            if (!input) return;
            if (input.tagName === 'SELECT' && ![...input.options].some(option => option.value === String(value ?? ''))) input.add(new Option(value || 'Sin información', value ?? ''));
            input.value = value;
        });
        InventoryEdit.snapshot(form);
        routeEditor.dialog.showModal();
    }

    const tables = { troncales: routesTable, tramos: tramosTable }; const tabs = [...root.querySelectorAll('[role="tab"]')];
    function selectTab(name) {
        const tab = tabs.find((item) => item.dataset.tab === name) || tabs[0];
        tabs.forEach((item) => { const active = item === tab; item.setAttribute('aria-selected', active); document.getElementById(item.getAttribute('aria-controls')).hidden = !active; });
        document.getElementById('network-inspector-title').textContent = tab.dataset.tab === 'tramos' ? 'Análisis de tramos' : 'Análisis de troncales';
        document.getElementById('routes-context-title').textContent = tab.dataset.tab === 'tramos' ? 'Tipos de trazado' : 'Capacidad consolidada';
        document.getElementById('routes-ranking-title').textContent = tab.dataset.tab === 'tramos' ? 'Tramos con mayor ocupación' : 'Troncales con mayor ocupación';
        if (!tables[tab.dataset.tab].loaded) tables[tab.dataset.tab].load(); const url = new URL(location.href); url.searchParams.set('tab', tab.dataset.tab); history.replaceState({}, '', url);
    }
    tabs.forEach((tab) => tab.addEventListener('click', () => selectTab(tab.dataset.tab)));
    const incoming = new URLSearchParams(location.search);
    root.querySelectorAll('[data-filter]').forEach((input) => {
        const value = incoming.get(input.dataset.filter);
        if (value !== null && [...input.options || []].some((option) => option.value === value)) {
            input.value = value;
        } else if (value !== null && input.matches('input')) {
            input.value = value;
        }
    });
    inspectorToggle?.addEventListener('click', () => setInspectorCollapsed(!inspector.classList.contains('is-collapsed')));
    setInspectorCollapsed(window.innerWidth <= 1180);
    selectTab(incoming.get('tab') || 'troncales');
})();
