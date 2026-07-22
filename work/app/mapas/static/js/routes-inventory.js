(function () {
    'use strict';
    const root = document.getElementById('routes-workbench');
    if (!root) return;
    const number = new Intl.NumberFormat('es-PE', { maximumFractionDigits: 2 });
    const csrf = () => (document.cookie.match(/(?:^|; )csrftoken=([^;]+)/) || [])[1] || '';

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
        button.addEventListener('click', handler);
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
            search.addEventListener('input', () => { clearTimeout(this.timer); this.timer = setTimeout(() => { this.page = 1; this.load(); }, 350); });
            this.form.querySelectorAll('select[data-filter]').forEach((select) => select.addEventListener('change', () => { this.page = 1; this.load(); }));
            this.form.querySelector('[data-clear]').addEventListener('click', () => { this.form.reset(); this.page = 1; search.focus(); this.load(); });
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
        row.appendChild(actions(item, canEditRoute, openRouteEditor)); return row;
    }
    function tramoRow(item) {
        const row = document.createElement('tr');
        [item.troncal, item.secuencia, item.origen, item.destino, item.tipo, `${number.format(item.distancia_km)} km`, item.capacidad, item.ocupados, item.libres, `${number.format(item.reservas_m)} m`].forEach((value) => row.appendChild(cell(value)));
        const state = document.createElement('td'); state.appendChild(badge(item.estado)); row.appendChild(state); row.appendChild(actions(item, false)); return row;
    }
    const text = (id, value) => { const node = document.getElementById(id); if (node) node.textContent = number.format(value || 0); };
    routesTable = new InventoryTable('troncales', {
        api: root.dataset.troncalesApi, exportUrl: root.dataset.troncalesExport, columns: 11, row: routeRow,
        summary: (s) => { text('troncales-stat-total', s.total); text('troncales-stat-distance', s.distancia_km); text('troncales-stat-segments', s.tramos); text('troncales-stat-fibers', s.fibras); text('troncales-stat-reserves', s.reservas); },
    });
    const tramosTable = new InventoryTable('tramos', {
        api: root.dataset.tramosApi, exportUrl: root.dataset.tramosExport, columns: 12, row: tramoRow,
        summary: (s) => { text('tramos-stat-total', s.total); text('tramos-stat-distance', s.distancia_km); text('tramos-stat-aerial', s.aereos); text('tramos-stat-underground', s.soterrados); text('tramos-stat-reserve', s.reservas_m); },
    });

    function dialogSetup(id, triggerId, submitUrl, beforeOpen) {
        const dialog = document.getElementById(id); const trigger = document.getElementById(triggerId);
        if (!dialog) return null; const form = dialog.querySelector('form'); const message = form.querySelector('[data-form-message]');
        dialog.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => dialog.close()));
        if (trigger) trigger.addEventListener('click', () => { form.reset(); message.textContent = ''; if (beforeOpen) beforeOpen(form); dialog.showModal(); });
        form.addEventListener('submit', async (event) => {
            event.preventDefault(); message.className = 'odf-dialog__message'; message.textContent = 'Guardando…';
            const payload = Object.fromEntries(new FormData(form).entries());
            const editing = id === 'route-editor' && Boolean(payload.nombre_original);
            if (editing && form.dataset.preserved) Object.assign(payload, JSON.parse(form.dataset.preserved));
            try {
                const response = await fetch(editing ? root.dataset.updateRoute : submitUrl, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest' }, body: JSON.stringify(payload) });
                const result = await response.json(); if (!response.ok || result.status !== 'success') throw new Error(result.message || 'No fue posible guardar');
                message.classList.add('is-success'); message.textContent = result.message; setTimeout(() => dialog.close(), 550);
                routesTable.loaded = false; tramosTable.loaded = false; routesTable.load();
            } catch (error) { message.classList.add('is-error'); message.textContent = error.message; }
        }); return { dialog, form, message };
    }
    const routeEditor = dialogSetup('route-editor', 'route-new', root.dataset.createRoute, (form) => { form.elements.nombre_original.value = ''; form.dataset.preserved = ''; document.getElementById('route-editor-title').textContent = 'Nueva troncal'; });
    dialogSetup('tramo-editor', 'tramo-new', root.dataset.createTramo);
    function openRouteEditor(item) {
        if (!routeEditor) return; const form = routeEditor.form; form.reset(); routeEditor.message.textContent = '';
        document.getElementById('route-editor-title').textContent = 'Editar troncal';
        const typeMap = { 'AÉREO': 'AEREO', 'HÍBRIDO': 'HIBRIDO' };
        const values = { nombre_original: item.nombre, nombre: item.nombre, hub_origen: item.origen === '—' ? '' : item.origen, destino: item.destino === '—' ? '' : item.destino, estado: item.estado, distancia_km: item.distancia_km, capacidad: item.capacidad === '—' ? '' : item.capacidad, tipo_fibra: item.tipo_fibra === '—' ? '' : item.tipo_fibra, odf_nombre: item.odf === '—' ? '' : item.odf, tipo_trazado: typeMap[item.tipo] || item.tipo, reserva_km: item.reserva_km };
        form.dataset.preserved = JSON.stringify({ marca_modelo: item.marca_modelo === '—' ? '' : item.marca_modelo, serial: item.serial === '—' ? '' : item.serial, mufas: item.mufas, splitters: item.splitters, hilos_ocupados: item.hilos_ocupados, hilos_libres: item.hilos_libres, reserva_km: item.reserva_km });
        Object.entries(values).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value; }); routeEditor.dialog.showModal();
    }

    const tables = { troncales: routesTable, tramos: tramosTable }; const tabs = [...root.querySelectorAll('[role="tab"]')];
    function selectTab(name) {
        const tab = tabs.find((item) => item.dataset.tab === name) || tabs[0];
        tabs.forEach((item) => { const active = item === tab; item.setAttribute('aria-selected', active); document.getElementById(item.getAttribute('aria-controls')).hidden = !active; });
        if (!tables[tab.dataset.tab].loaded) tables[tab.dataset.tab].load(); const url = new URL(location.href); url.searchParams.set('tab', tab.dataset.tab); history.replaceState({}, '', url);
    }
    tabs.forEach((tab) => tab.addEventListener('click', () => selectTab(tab.dataset.tab)));
    const incoming = new URLSearchParams(location.search); const query = incoming.get('q'); if (query) root.querySelectorAll('[data-filter="q"]').forEach((input) => { input.value = query; });
    selectTab(incoming.get('tab') || 'troncales');
})();
