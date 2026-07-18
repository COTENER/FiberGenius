(function () {
    'use strict';

    const root = document.getElementById('ports-workbench');
    if (!root) return;

    const form = document.getElementById('ports-filters');
    const query = document.getElementById('ports-query');
    const statusFilter = document.getElementById('ports-status-filter');
    const pageSize = document.getElementById('ports-page-size');
    const clear = document.getElementById('ports-clear');
    const body = document.getElementById('ports-body');
    const message = document.getElementById('ports-message');
    const pageInfo = document.getElementById('ports-page-info');
    const previous = document.getElementById('ports-previous');
    const next = document.getElementById('ports-next');
    const exportCsv = document.getElementById('ports-export-csv');
    const exportXlsx = document.getElementById('ports-export-xlsx');
    let page = 1;
    let abortController = null;
    let debounceTimer = null;

    function td(text, className) {
        const cell = document.createElement('td');
        if (className) cell.className = className;
        cell.textContent = text ?? '—';
        return cell;
    }

    function statusBadge(value) {
        const badge = document.createElement('span');
        badge.className = `status-badge ${value === 'Ocupado' ? 'status-badge--busy' : 'status-badge--free'}`;
        badge.textContent = value || 'Sin estado';
        return badge;
    }

    function currentParams(includePage = true) {
        const params = new URLSearchParams();
        if (query.value.trim()) params.set('q', query.value.trim());
        if (statusFilter.value) params.set('estado', statusFilter.value);
        params.set('page_size', pageSize.value);
        if (includePage) params.set('page', String(page));
        return params;
    }

    function updateExportLinks() {
        const params = currentParams(false);
        [
            [exportCsv, root.dataset.exportCsv],
            [exportXlsx, root.dataset.exportXlsx],
        ].forEach(([link, base]) => {
            const url = new URL(base, window.location.origin);
            url.search = params.toString();
            link.href = url.toString();
        });
    }

    function renderRows(rows) {
        body.replaceChildren();
        if (!rows.length) {
            const row = document.createElement('tr');
            const cell = td('No se encontraron puertos con los filtros seleccionados.', 'data-table__empty');
            cell.colSpan = 12;
            row.appendChild(cell);
            body.appendChild(row);
            return;
        }
        rows.forEach((port) => {
            const row = document.createElement('tr');
            row.appendChild(td(port.site));
            row.appendChild(td(port.sala));
            row.appendChild(td(port.rack));
            row.appendChild(td(port.odf));
            row.appendChild(td(port.bandeja));
            row.appendChild(td(port.puerto));
            row.appendChild(td(port.fibra));
            const stateCell = document.createElement('td');
            stateCell.appendChild(statusBadge(port.estado));
            row.appendChild(stateCell);
            row.appendChild(td(port.conector));
            row.appendChild(td(port.patchcord));
            row.appendChild(td(port.destino));
            const actionCell = document.createElement('td');
            const detail = document.createElement('button');
            detail.type = 'button';
            detail.className = 'row-action';
            detail.textContent = 'Ficha 360°';
            detail.setAttribute('aria-label', `Abrir ficha 360° de ${port.odf}, puerto ${port.puerto}`);
            detail.addEventListener('click', () => window.FiberGenius.openAsset360(port.detail_url));
            actionCell.appendChild(detail);
            row.appendChild(actionCell);
            body.appendChild(row);
        });
    }

    async function load() {
        if (abortController) abortController.abort();
        abortController = new AbortController();
        message.classList.remove('is-error');
        message.textContent = 'Consultando puertos…';
        previous.disabled = true;
        next.disabled = true;
        updateExportLinks();

        const url = new URL(root.dataset.apiUrl, window.location.origin);
        url.search = currentParams(true).toString();
        try {
            const response = await fetch(url, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                signal: abortController.signal,
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const payload = await response.json();
            const pagination = payload.pagination;
            page = pagination.page;
            renderRows(payload.data || []);
            const start = pagination.total === 0 ? 0 : ((pagination.page - 1) * pagination.page_size) + 1;
            const end = Math.min(pagination.page * pagination.page_size, pagination.total);
            pageInfo.textContent = `Mostrando ${start}–${end} de ${pagination.total.toLocaleString('es-PE')} puertos`;
            previous.disabled = !pagination.has_previous;
            next.disabled = !pagination.has_next;
            message.textContent = '';
        } catch (error) {
            if (error.name === 'AbortError') return;
            message.classList.add('is-error');
            message.textContent = 'No se pudo cargar la consulta de puertos. Intenta nuevamente.';
            body.replaceChildren();
            pageInfo.textContent = 'Consulta no disponible';
        }
    }

    form.addEventListener('submit', (event) => {
        event.preventDefault();
        page = 1;
        load();
    });
    query.addEventListener('input', () => {
        window.clearTimeout(debounceTimer);
        debounceTimer = window.setTimeout(() => { page = 1; load(); }, 350);
    });
    statusFilter.addEventListener('change', () => { page = 1; load(); });
    pageSize.addEventListener('change', () => { page = 1; load(); });
    clear.addEventListener('click', () => {
        query.value = '';
        statusFilter.value = '';
        pageSize.value = '50';
        page = 1;
        query.focus();
        load();
    });
    previous.addEventListener('click', () => { page = Math.max(1, page - 1); load(); });
    next.addEventListener('click', () => { page += 1; load(); });

    const incoming = new URLSearchParams(window.location.search);
    query.value = incoming.get('q') || '';
    if (['Libre', 'Ocupado'].includes(incoming.get('estado'))) statusFilter.value = incoming.get('estado');
    load();
})();
