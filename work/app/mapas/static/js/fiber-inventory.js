(function () {
    'use strict';
    const root = document.getElementById('fiber-workbench');
    if (!root) return;
    const formatter = new Intl.NumberFormat('es-PE', { maximumFractionDigits: 2 });
    const csrf = () => (document.cookie.match(/(?:^|; )csrftoken=([^;]+)/) || [])[1] || '';
    const put = (id, value) => { const node = document.getElementById(id); if (node) node.textContent = formatter.format(value || 0); };

    function cell(value) { const td = document.createElement('td'); td.textContent = value === null || value === undefined || value === '' ? '—' : value; return td; }
    function badge(value) {
        const span = document.createElement('span'); const state = String(value || '').toLowerCase(); span.className = 'odf-status';
        if (state.includes('reserv') || state.includes('confirmar')) span.classList.add('is-warning');
        if (state.includes('fuera') || state.includes('error')) span.classList.add('is-danger');
        if (!value) span.classList.add('is-neutral'); span.textContent = value || 'Sin estado'; return span;
    }
    function actionButton(label, symbol, callback) { const button = document.createElement('button'); button.type = 'button'; button.className = 'odf-icon-action'; button.title = label; button.setAttribute('aria-label', label); button.textContent = symbol; button.addEventListener('click', callback); return button; }
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
                const response = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, signal: this.controller.signal }); if (!response.ok) throw new Error(); const payload = await response.json(); const meta = payload.pagination;
                this.page = meta.page; this.rows(payload.data || []); this.options.summary(payload.summary || {}); const start = meta.total ? ((meta.page - 1) * meta.page_size) + 1 : 0; const end = Math.min(meta.page * meta.page_size, meta.total); this.info.textContent = `Mostrando ${start} a ${end} de ${formatter.format(meta.total)} resultados`; this.previous.disabled = !meta.has_previous; this.next.disabled = !meta.has_next; this.pages(meta); this.message.textContent = ''; this.loaded = true;
            } catch (error) { if (error.name === 'AbortError') return; this.message.classList.add('is-error'); this.message.textContent = 'No se pudo cargar el inventario.'; }
        }
        bind() {
            this.form.addEventListener('submit', (event) => { event.preventDefault(); this.page = 1; this.load(); }); const search = this.form.querySelector('[data-filter="q"]');
            search.addEventListener('input', () => { clearTimeout(this.timer); this.timer = setTimeout(() => { this.page = 1; this.load(); }, 350); }); this.form.querySelectorAll('select[data-filter]').forEach((select) => select.addEventListener('change', () => { this.page = 1; this.load(); }));
            this.form.querySelector('[data-clear]').addEventListener('click', () => { this.form.reset(); this.page = 1; search.focus(); this.load(); }); this.form.querySelector('[data-export]').addEventListener('click', () => exportFile(this.options.exportUrl, this.params(false), this.form.querySelector('[data-export-status]'))); this.previous.addEventListener('click', () => { if (this.page > 1) { this.page -= 1; this.load(); } }); this.next.addEventListener('click', () => { this.page += 1; this.load(); });
        }
    }

    let fiberEditor;
    function fiberRow(item) { const row = document.createElement('tr'); row.appendChild(cell(item.troncal)); row.appendChild(cell(item.numero)); const state = document.createElement('td'); state.appendChild(badge(item.estado)); row.appendChild(state); [item.servicio, item.origen, item.destino, item.conector].forEach((value) => row.appendChild(cell(value))); row.appendChild(actionCell(item, root.dataset.canEditFiber === 'true' ? openFiberEditor : null)); return row; }
    function reserveRow(item) { const row = document.createElement('tr'); [item.troncal, item.nombre, item.tipo, item.reserva_m === null ? '—' : `${formatter.format(item.reserva_m)} m`, item.tramo, item.latitud, item.longitud].forEach((value) => row.appendChild(cell(value))); const state = document.createElement('td'); state.appendChild(badge(item.estado)); row.appendChild(state); row.appendChild(actionCell(item)); return row; }
    const fibers = new InventoryTable('fibras', { api: root.dataset.fibersApi, exportUrl: root.dataset.fibersExport, columns: 8, row: fiberRow, summary: (s) => { put('fibras-stat-total', s.total); put('fibras-stat-free', s.libres); put('fibras-stat-used', s.ocupadas); put('fibras-stat-reserved', s.reservadas); put('fibras-stat-routes', s.troncales); } });
    const reserves = new InventoryTable('reservas', { api: root.dataset.reservesApi, exportUrl: root.dataset.reservesExport, columns: 9, row: reserveRow, summary: (s) => { put('reservas-stat-total', s.total); put('reservas-stat-meters', s.reserva_m); put('reservas-stat-types', s.tipos); put('reservas-stat-routes', s.troncales); put('reservas-stat-pending', s.por_confirmar); } });

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
        const values = { id: item.id, ruta_nombre: item.troncal, fibra_numero: item.numero, estado: item.estado, nombre_fibra: item.servicio === '—' ? '' : item.servicio, origen_odf: item.origen === '—' ? '' : item.origen, destino: item.destino === '—' ? '' : item.destino, tipo_conector: item.conector === '—' ? '' : item.conector };
        Object.entries(values).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value; }); form.elements.ruta_nombre.disabled = true; fiberEditor.dialog.showModal();
    }

    const tables = { fibras: fibers, reservas: reserves }; const tabs = [...root.querySelectorAll('[role="tab"]')];
    function selectTab(name) { const tab = tabs.find((item) => item.dataset.tab === name) || tabs[0]; if (!tab) return; tabs.forEach((item) => { const active = item === tab; item.setAttribute('aria-selected', active); document.getElementById(item.getAttribute('aria-controls')).hidden = !active; }); const table = tables[tab.dataset.tab]; if (table && !table.loaded) table.load(); const url = new URL(location.href); url.searchParams.set('tab', tab.dataset.tab); history.replaceState({}, '', url); }
    tabs.forEach((tab) => tab.addEventListener('click', () => selectTab(tab.dataset.tab))); const incoming = new URLSearchParams(location.search); const query = incoming.get('q'); if (query) root.querySelectorAll('[data-filter="q"]').forEach((input) => { input.value = query; }); selectTab(incoming.get('tab') || root.dataset.defaultTab);
})();
