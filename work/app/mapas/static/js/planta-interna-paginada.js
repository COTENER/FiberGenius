(function () {
    'use strict';

    const root = document.getElementById('ports-workbench');
    if (!root) return;

    const elements = {
        form: document.getElementById('ports-filters'),
        query: document.getElementById('ports-query'),
        status: document.getElementById('ports-status-filter'),
        site: document.getElementById('ports-site-filter'),
        room: document.getElementById('ports-room-filter'),
        rack: document.getElementById('ports-rack-filter'),
        pageSize: document.getElementById('ports-page-size'),
        clear: document.getElementById('ports-clear'),
        body: document.getElementById('ports-body'),
        message: document.getElementById('ports-message'),
        pageInfo: document.getElementById('ports-page-info'),
        pagination: document.getElementById('ports-pagination'),
        previous: document.getElementById('ports-previous'),
        next: document.getElementById('ports-next'),
        exportCsv: document.getElementById('ports-export-csv'),
        exportXlsx: document.getElementById('ports-export-xlsx'),
        exportStatus: document.getElementById('ports-export-status'),
        newPort: document.getElementById('ports-new'),
        editor: document.getElementById('port-editor'),
        editorForm: document.getElementById('port-editor-form'),
    };
    const stats = {
        total: document.getElementById('ports-stat-total'),
        free: document.getElementById('ports-stat-free'),
        used: document.getElementById('ports-stat-used'),
        reserved: document.getElementById('ports-stat-reserved'),
        review: document.getElementById('ports-stat-review'),
    };
    const numberFormat = new Intl.NumberFormat('es-PE');
    let page = 1;
    let abortController = null;
    let debounceTimer = null;
    let currentRows = [];
    let exportObjectUrl = null;

    function exportFilename(response) {
        const disposition = response.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="?([^";]+)"?/i);
        return match?.[1] || 'FiberGenius_puertos.xlsx';
    }

    function showExportFallback(objectUrl, filename) {
        if (!elements.exportStatus) return;
        const text = document.createTextNode(`Archivo preparado: ${filename}. `);
        const fallback = document.createElement('a');
        fallback.href = objectUrl;
        fallback.download = filename;
        fallback.textContent = 'Descargar nuevamente';
        elements.exportStatus.className = 'ports-export-status is-success';
        elements.exportStatus.replaceChildren(text, fallback);
    }

    function bindExportDownload(link) {
        if (!link) return;
        const original = link.innerHTML;
        link.addEventListener('click', async event => {
            event.preventDefault();
            if (link.dataset.busy === 'true') {
                return;
            }
            link.dataset.busy = 'true';
            link.setAttribute('aria-disabled', 'true');
            link.textContent = 'Preparando Excel…';
            if (elements.exportStatus) {
                elements.exportStatus.className = 'ports-export-status';
                elements.exportStatus.textContent = 'Generando archivo…';
            }
            try {
                const response = await fetch(link.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const blob = await response.blob();
                const filename = exportFilename(response);
                if (exportObjectUrl) URL.revokeObjectURL(exportObjectUrl);
                exportObjectUrl = URL.createObjectURL(blob);
                const download = document.createElement('a');
                download.href = exportObjectUrl;
                download.download = filename;
                download.hidden = true;
                document.body.appendChild(download);
                download.click();
                download.remove();
                showExportFallback(exportObjectUrl, filename);
            } catch (error) {
                if (elements.exportStatus) {
                    elements.exportStatus.className = 'ports-export-status is-error';
                    elements.exportStatus.textContent = 'No se pudo descargar el archivo. Intenta nuevamente.';
                }
            } finally {
                link.dataset.busy = 'false';
                link.removeAttribute('aria-disabled');
                link.innerHTML = original;
            }
        });
    }

    bindExportDownload(elements.exportCsv);
    bindExportDownload(elements.exportXlsx);

    function td(text, className) {
        const cell = document.createElement('td');
        if (className) cell.className = className;
        cell.textContent = text ?? '—';
        return cell;
    }

    function svgButton(label, path, handler) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'ports-icon-action';
        button.setAttribute('aria-label', label);
        button.title = label;
        button.innerHTML = `<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${path}</svg>`;
        button.addEventListener('click', handler);
        return button;
    }

    function statusBadge(value) {
        const badge = document.createElement('span');
        const modifier = value === 'Ocupado'
            ? 'is-used'
            : (value === 'Reservado' ? 'is-reserved' : 'is-free');
        badge.className = `port-status ${modifier}`;
        badge.textContent = value || 'Sin estado';
        return badge;
    }

    function currentParams(includePage = true) {
        const params = new URLSearchParams();
        const fields = [
            ['q', elements.query.value.trim()],
            ['estado', elements.status.value],
            ['site', elements.site.value],
            ['sala', elements.room.value],
            ['rack', elements.rack.value],
        ];
        fields.forEach(([name, value]) => { if (value) params.set(name, value); });
        params.set('page_size', elements.pageSize.value);
        if (includePage) params.set('page', String(page));
        return params;
    }

    function updateExportLinks() {
        const params = currentParams(false);
        [[elements.exportCsv, root.dataset.exportCsv], [elements.exportXlsx, root.dataset.exportXlsx]]
            .forEach(([link, base]) => {
                if (!link) return;
                const url = new URL(base, window.location.origin);
                url.search = params.toString();
                link.href = url.toString();
            });
    }

    function updateStats(summary) {
        const values = {
            total: summary?.total || 0,
            free: summary?.libres || 0,
            used: summary?.ocupados || 0,
            reserved: summary?.reservados || 0,
            review: summary?.sin_destino || 0,
        };
        Object.entries(values).forEach(([key, value]) => {
            if (stats[key]) stats[key].textContent = numberFormat.format(value);
        });
    }

    function renderRows(rows) {
        currentRows = rows;
        elements.body.replaceChildren();
        if (!rows.length) {
            const row = document.createElement('tr');
            const cell = td('No se encontraron puertos con los filtros seleccionados.', 'ports-table__empty');
            cell.colSpan = 12;
            row.appendChild(cell);
            elements.body.appendChild(row);
            return;
        }
        rows.forEach(port => {
            const row = document.createElement('tr');
            [port.site, port.sala, port.rack, port.odf, port.bandeja, port.puerto, port.fibra]
                .forEach(value => row.appendChild(td(value)));
            const stateCell = document.createElement('td');
            stateCell.appendChild(statusBadge(port.estado));
            row.appendChild(stateCell);
            [port.conector, port.patchcord, port.destino].forEach(value => row.appendChild(td(value)));

            const actionCell = document.createElement('td');
            const actions = document.createElement('div');
            actions.className = 'ports-row-actions';
            actions.appendChild(svgButton(
                `Abrir ficha 360° de ${port.odf}, puerto ${port.puerto}`,
                '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/>',
                () => window.FiberGenius.openAsset360(port.detail_url),
            ));
            if (root.dataset.canEdit === 'true') {
                actions.appendChild(svgButton(
                    `Editar ${port.odf}, puerto ${port.puerto}`,
                    '<path d="m4 20 4-.8L19 8.2a2 2 0 0 0-3-3L5 16.2 4 20Z"/><path d="m14.5 6.5 3 3"/>',
                    () => openEditor(port),
                ));
            }
            actionCell.appendChild(actions);
            row.appendChild(actionCell);
            elements.body.appendChild(row);
        });
    }

    function paginationSequence(current, total) {
        if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
        const values = new Set([1, total, current - 1, current, current + 1]);
        const ordered = [...values].filter(value => value > 0 && value <= total).sort((a, b) => a - b);
        const result = [];
        ordered.forEach((value, index) => {
            if (index && value - ordered[index - 1] > 1) result.push('…');
            result.push(value);
        });
        return result;
    }

    function renderPagination(pagination) {
        elements.pagination.replaceChildren();
        paginationSequence(pagination.page, pagination.total_pages).forEach(value => {
            if (value === '…') {
                const ellipsis = document.createElement('span');
                ellipsis.className = 'ports-page-ellipsis';
                ellipsis.textContent = value;
                elements.pagination.appendChild(ellipsis);
                return;
            }
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `ports-page-number ${value === pagination.page ? 'is-current' : ''}`;
            button.textContent = String(value);
            button.setAttribute('aria-label', `Ir a la página ${value}`);
            if (value === pagination.page) button.setAttribute('aria-current', 'page');
            button.addEventListener('click', () => { page = value; load(); });
            elements.pagination.appendChild(button);
        });
    }

    async function load() {
        if (abortController) abortController.abort();
        abortController = new AbortController();
        elements.message.classList.remove('is-error');
        elements.message.textContent = 'Consultando puertos…';
        elements.previous.disabled = true;
        elements.next.disabled = true;
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
            updateStats(payload.summary);
            renderPagination(pagination);
            const start = pagination.total === 0 ? 0 : ((pagination.page - 1) * pagination.page_size) + 1;
            const end = Math.min(pagination.page * pagination.page_size, pagination.total);
            elements.pageInfo.textContent = `Mostrando ${start} a ${end} de ${numberFormat.format(pagination.total)} resultados`;
            elements.previous.disabled = !pagination.has_previous;
            elements.next.disabled = !pagination.has_next;
            elements.message.textContent = '';
            const browserUrl = new URL(window.location.href);
            browserUrl.search = currentParams(false).toString();
            window.history.replaceState({}, '', browserUrl);
        } catch (error) {
            if (error.name === 'AbortError') return;
            elements.message.classList.add('is-error');
            elements.message.textContent = 'No se pudo cargar la consulta de puertos. Intenta nuevamente.';
            elements.body.replaceChildren();
            elements.pageInfo.textContent = 'Consulta no disponible';
            updateStats({});
        }
    }

    function editorValue(id) {
        return document.getElementById(id);
    }

    function openEditor(port = null) {
        if (!elements.editor) return;
        elements.editorForm.reset();
        editorValue('port-editor-id').value = port?.id || '';
        editorValue('port-editor-title').textContent = port ? 'Editar puerto ODF' : 'Nuevo puerto ODF';
        editorValue('port-editor-mode').textContent = port
            ? `Actualiza la identificación y conexión del puerto ${port.puerto}.`
            : 'Registra una posición física y su conexión óptica.';
        editorValue('port-editor-submit').textContent = port ? 'Guardar cambios' : 'Crear puerto';
        editorValue('port-editor-odf').value = port?.odf || '';
        editorValue('port-editor-odf').disabled = Boolean(port);
        editorValue('port-editor-number').value = port?.puerto || '';
        editorValue('port-editor-tray').value = port?.bandeja === '—' ? '' : (port?.bandeja || '');
        editorValue('port-editor-status').value = port?.estado || 'Libre';
        editorValue('port-editor-fiber').value = port?.fibra === '—' ? '' : (port?.fibra || '');
        editorValue('port-editor-connector').value = port?.conector === '—' ? '' : (port?.conector || '');
        editorValue('port-editor-patchcord').value = ['—', 'No'].includes(port?.patchcord) ? '' : (port?.patchcord || '');
        editorValue('port-editor-destination').value = port?.destino === '—' ? '' : (port?.destino || '');
        editorValue('port-editor-notes').value = port?.observaciones || '';
        editorValue('port-editor-message').textContent = '';
        editorValue('port-editor-message').classList.remove('is-success');
        elements.editor.showModal();
        (port ? editorValue('port-editor-number') : editorValue('port-editor-odf')).focus();
    }

    function closeEditor() {
        if (elements.editor?.open) elements.editor.close();
    }

    async function saveEditor(event) {
        event.preventDefault();
        const id = editorValue('port-editor-id').value;
        const payload = {
            id,
            odf_nombre: editorValue('port-editor-odf').value,
            puerto_odf: editorValue('port-editor-number').value.trim(),
            bandeja: editorValue('port-editor-tray').value.trim(),
            estado_puerto: editorValue('port-editor-status').value,
            fibra: editorValue('port-editor-fiber').value.trim(),
            tipo_conector: editorValue('port-editor-connector').value.trim(),
            patchcord: editorValue('port-editor-patchcord').value.trim(),
            destino: editorValue('port-editor-destination').value.trim(),
            observaciones: editorValue('port-editor-notes').value.trim(),
        };
        const message = editorValue('port-editor-message');
        message.textContent = 'Guardando…';
        const csrf = elements.editorForm.querySelector('[name="csrfmiddlewaretoken"]').value;
        try {
            const response = await fetch(id ? root.dataset.updateUrl : root.dataset.createUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
                body: JSON.stringify(payload),
            });
            const result = await response.json();
            if (!response.ok || result.status !== 'success') throw new Error(result.message || 'No se pudo guardar');
            message.classList.add('is-success');
            message.textContent = result.message;
            window.setTimeout(() => { closeEditor(); load(); }, 450);
        } catch (error) {
            message.classList.remove('is-success');
            message.textContent = error.message;
        }
    }

    elements.form.addEventListener('submit', event => { event.preventDefault(); page = 1; load(); });
    elements.query.addEventListener('input', () => {
        window.clearTimeout(debounceTimer);
        debounceTimer = window.setTimeout(() => { page = 1; load(); }, 350);
    });
    [elements.status, elements.site, elements.room, elements.rack, elements.pageSize]
        .forEach(field => field.addEventListener('change', () => { page = 1; load(); }));
    elements.clear.addEventListener('click', () => {
        elements.form.reset();
        elements.pageSize.value = '25';
        page = 1;
        elements.query.focus();
        load();
    });
    elements.previous.addEventListener('click', () => { page = Math.max(1, page - 1); load(); });
    elements.next.addEventListener('click', () => { page += 1; load(); });
    elements.newPort?.addEventListener('click', () => openEditor());
    elements.editorForm?.addEventListener('submit', saveEditor);
    document.getElementById('port-editor-close')?.addEventListener('click', closeEditor);
    document.getElementById('port-editor-cancel')?.addEventListener('click', closeEditor);

    const incoming = new URLSearchParams(window.location.search);
    elements.query.value = incoming.get('q') || '';
    if (['Libre', 'Ocupado', 'Reservado'].includes(incoming.get('estado'))) elements.status.value = incoming.get('estado');
    elements.site.value = incoming.get('site') || '';
    elements.room.value = incoming.get('sala') || '';
    elements.rack.value = incoming.get('rack') || '';
    if (['10', '25', '50', '100', '200'].includes(incoming.get('page_size'))) elements.pageSize.value = incoming.get('page_size');
    load();
})();
