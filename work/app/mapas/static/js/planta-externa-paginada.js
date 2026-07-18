(function () {
    'use strict';

    const root = document.getElementById('outside-plant-workbench');
    if (!root) return;

    function td(text) {
        const cell = document.createElement('td');
        cell.textContent = text ?? '—';
        return cell;
    }

    function badge(value) {
        const normalized = (value || '').toLowerCase();
        let modifier = 'status-badge--neutral';
        if (normalized.includes('libre') || normalized.includes('activo')) modifier = 'status-badge--free';
        if (normalized.includes('ocup')) modifier = 'status-badge--busy';
        if (normalized.includes('reserv')) modifier = 'status-badge--reserved';
        const node = document.createElement('span');
        node.className = `status-badge ${modifier}`;
        node.textContent = value || 'Sin estado';
        return node;
    }

    function actionCell(item, label) {
        const cell = document.createElement('td');
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'row-action';
        button.textContent = 'Ficha 360°';
        button.setAttribute('aria-label', `Abrir ficha 360° de ${label}`);
        button.addEventListener('click', () => window.FiberGenius.openAsset360(item.detail_url));
        cell.appendChild(button);
        return cell;
    }

    class ServerTable {
        constructor(options) {
            this.options = options;
            this.form = document.getElementById(`${options.prefix}-filters`);
            if (!this.form) return;
            this.query = document.getElementById(`${options.prefix}-query`);
            this.filter = document.getElementById(`${options.prefix}-${options.filterId}`);
            this.size = document.getElementById(`${options.prefix}-size`);
            this.clear = document.getElementById(`${options.prefix}-clear`);
            this.body = document.getElementById(`${options.prefix}-body`);
            this.message = document.getElementById(`${options.prefix}-message`);
            this.info = document.getElementById(`${options.prefix}-info`);
            this.previous = document.getElementById(`${options.prefix}-previous`);
            this.next = document.getElementById(`${options.prefix}-next`);
            this.csv = document.getElementById(`${options.prefix}-csv`);
            this.xlsx = document.getElementById(`${options.prefix}-xlsx`);
            this.page = 1;
            this.controller = null;
            this.timer = null;
            this.loaded = false;
            this.bind();
        }

        params(withPage = true) {
            const params = new URLSearchParams();
            if (this.query.value.trim()) params.set('q', this.query.value.trim());
            if (this.filter.value) params.set(this.options.filterParam, this.filter.value);
            params.set('page_size', this.size.value);
            if (withPage) params.set('page', String(this.page));
            return params;
        }

        updateExports() {
            const params = this.params(false);
            [[this.csv, this.options.csv], [this.xlsx, this.options.xlsx]].forEach(([link, base]) => {
                const url = new URL(base, window.location.origin);
                url.search = params.toString();
                link.href = url.toString();
            });
        }

        render(rows) {
            this.body.replaceChildren();
            if (!rows.length) {
                const row = document.createElement('tr');
                const empty = td('No se encontraron registros con los filtros seleccionados.');
                empty.className = 'data-table__empty';
                empty.colSpan = this.options.columnCount;
                row.appendChild(empty);
                this.body.appendChild(row);
                return;
            }
            rows.forEach((item) => this.body.appendChild(this.options.renderRow(item)));
        }

        async load() {
            if (!this.form) return;
            if (this.controller) this.controller.abort();
            this.controller = new AbortController();
            this.message.classList.remove('is-error');
            this.message.textContent = 'Consultando inventario…';
            this.previous.disabled = true;
            this.next.disabled = true;
            this.updateExports();
            const url = new URL(this.options.api, window.location.origin);
            url.search = this.params(true).toString();
            try {
                const response = await fetch(url, {
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                    signal: this.controller.signal,
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const payload = await response.json();
                const pagination = payload.pagination;
                this.page = pagination.page;
                this.render(payload.data || []);
                const start = pagination.total === 0 ? 0 : ((pagination.page - 1) * pagination.page_size) + 1;
                const end = Math.min(pagination.page * pagination.page_size, pagination.total);
                this.info.textContent = `Mostrando ${start}–${end} de ${pagination.total.toLocaleString('es-PE')} registros`;
                this.previous.disabled = !pagination.has_previous;
                this.next.disabled = !pagination.has_next;
                this.message.textContent = '';
                this.loaded = true;
            } catch (error) {
                if (error.name === 'AbortError') return;
                this.message.classList.add('is-error');
                this.message.textContent = 'No se pudo cargar la consulta. Intenta nuevamente.';
                this.info.textContent = 'Consulta no disponible';
            }
        }

        bind() {
            if (!this.form) return;
            this.form.addEventListener('submit', (event) => { event.preventDefault(); this.page = 1; this.load(); });
            this.query.addEventListener('input', () => {
                window.clearTimeout(this.timer);
                this.timer = window.setTimeout(() => { this.page = 1; this.load(); }, 350);
            });
            this.filter.addEventListener('change', () => { this.page = 1; this.load(); });
            this.size.addEventListener('change', () => { this.page = 1; this.load(); });
            this.clear.addEventListener('click', () => {
                this.query.value = '';
                this.filter.value = '';
                this.size.value = '50';
                this.page = 1;
                this.query.focus();
                this.load();
            });
            this.previous.addEventListener('click', () => { this.page = Math.max(1, this.page - 1); this.load(); });
            this.next.addEventListener('click', () => { this.page += 1; this.load(); });
        }
    }

    function fiberRow(fiber) {
        const row = document.createElement('tr');
        row.appendChild(td(fiber.troncal));
        row.appendChild(td(fiber.numero));
        const state = document.createElement('td'); state.appendChild(badge(fiber.estado)); row.appendChild(state);
        row.appendChild(td(fiber.servicio));
        row.appendChild(td(fiber.origen));
        row.appendChild(td(fiber.destino));
        row.appendChild(td(fiber.conector));
        row.appendChild(actionCell(fiber, `${fiber.troncal}, fibra ${fiber.numero}`));
        return row;
    }

    function elementRow(item) {
        const row = document.createElement('tr');
        row.appendChild(td(item.troncal));
        row.appendChild(td(item.nombre));
        row.appendChild(td(item.tipo));
        row.appendChild(td(item.reserva_m === null ? '—' : item.reserva_m));
        row.appendChild(td(item.tramo));
        row.appendChild(td(item.latitud));
        row.appendChild(td(item.longitud));
        const state = document.createElement('td'); state.appendChild(badge(item.estado)); row.appendChild(state);
        row.appendChild(actionCell(item, item.nombre));
        return row;
    }

    const fibers = new ServerTable({
        prefix: 'fibers', filterId: 'status', filterParam: 'estado', columnCount: 8,
        api: root.dataset.fibersApi, csv: root.dataset.fibersCsv, xlsx: root.dataset.fibersXlsx,
        renderRow: fiberRow,
    });
    const elements = new ServerTable({
        prefix: 'elements', filterId: 'type', filterParam: 'tipo', columnCount: 9,
        api: root.dataset.elementsApi, csv: root.dataset.elementsCsv, xlsx: root.dataset.elementsXlsx,
        renderRow: elementRow,
    });

    const tables = { fibras: fibers, elementos: elements };
    const tabs = Array.from(root.querySelectorAll('[role="tab"]'));
    const panels = Array.from(root.querySelectorAll('[role="tabpanel"]'));

    function selectTab(name, focus = false) {
        const target = tabs.find((tab) => tab.dataset.tab === name) || tabs[0];
        if (!target) return;
        tabs.forEach((tab) => tab.setAttribute('aria-selected', tab === target ? 'true' : 'false'));
        panels.forEach((panel) => { panel.hidden = panel.id !== target.getAttribute('aria-controls'); });
        if (focus) target.focus();
        const table = tables[target.dataset.tab];
        if (table && table.form && !table.loaded) table.load();
        const url = new URL(window.location.href);
        url.searchParams.set('tab', target.dataset.tab);
        window.history.replaceState({}, '', url);
    }

    tabs.forEach((tab, index) => {
        tab.addEventListener('click', () => selectTab(tab.dataset.tab));
        tab.addEventListener('keydown', (event) => {
            if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
            event.preventDefault();
            const delta = event.key === 'ArrowRight' ? 1 : -1;
            const nextTab = tabs[(index + delta + tabs.length) % tabs.length];
            selectTab(nextTab.dataset.tab, true);
        });
    });

    const incoming = new URLSearchParams(window.location.search);
    const incomingQuery = incoming.get('q') || '';
    if (fibers.form) fibers.query.value = incomingQuery;
    if (elements.form) elements.query.value = incomingQuery;
    selectTab(incoming.get('tab') || root.dataset.defaultTab);
})();
