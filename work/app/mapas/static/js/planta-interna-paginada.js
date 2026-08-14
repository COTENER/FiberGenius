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
        management: document.getElementById('port-management'),
        managementForm: document.getElementById('port-management-form'),
        bulkOpen: document.getElementById('ports-bulk-open'),
        bulkDialog: document.getElementById('ports-bulk-import'),
        bulkForm: document.getElementById('ports-bulk-form'),
        bulkFile: document.getElementById('ports-bulk-file'),
        bulkValidate: document.getElementById('ports-bulk-validate'),
        bulkApply: document.getElementById('ports-bulk-apply'),
        bulkBody: document.getElementById('ports-bulk-body'),
        bulkSummary: document.getElementById('ports-bulk-summary'),
        bulkMessage: document.getElementById('ports-bulk-message'),
        bulkReport: document.getElementById('ports-bulk-report'),
        historyOpen: document.getElementById('ports-history-open'),
        historyDialog: document.getElementById('ports-history'),
        historyTitle: document.getElementById('ports-history-title'),
        historySummary: document.getElementById('ports-history-summary'),
        historyBody: document.getElementById('ports-history-body'),
        historyMessage: document.getElementById('ports-history-message'),
    };
    const stats = {
        total: document.getElementById('ports-stat-total'),
        free: document.getElementById('ports-stat-free'),
        used: document.getElementById('ports-stat-used'),
        reserved: document.getElementById('ports-stat-reserved'),
        review: document.getElementById('ports-stat-review'),
    };
    const numberFormat = new Intl.NumberFormat('es-PE');
    const percentFormat = new Intl.NumberFormat('es-PE', { maximumFractionDigits: 1 });
    let page = 1;
    let abortController = null;
    let debounceTimer = null;
    let currentRows = [];
    let exportObjectUrl = null;
    let managedPort = null;
    const inspector = document.getElementById('network-inspector');
    const inspectorToggle = document.getElementById('network-inspector-toggle');
    const inspectorSummary = document.getElementById('ports-inspector-summary');
    const inspectorEmpty = document.getElementById('ports-inspector-empty');
    const inspectorDetail = document.getElementById('ports-inspector-detail');

    function setInspectorCollapsed(collapsed) {
        if (!inspector || !inspectorToggle) return;
        inspector.classList.toggle('is-collapsed', collapsed);
        inspectorToggle.setAttribute('aria-expanded', String(!collapsed));
        inspectorToggle.setAttribute('aria-label', collapsed ? 'Expandir panel' : 'Contraer panel');
    }

    function stateSummary(summary) {
        if (!inspectorSummary) return;
        const total = Number(summary?.total || 0);
        const free = Number(summary?.libres || 0);
        const used = Number(summary?.ocupados || 0);
        const reserved = Number(summary?.reservados || 0);
        const freePercent = total ? Math.min(100, (free / total) * 100) : 0;
        const usedPercent = total ? Math.min(100 - freePercent, (used / total) * 100) : 0;
        const donut = document.createElement('div');
        donut.className = 'network-donut';
        donut.style.background = `conic-gradient(#18b777 0 ${freePercent}%, #2f74ee ${freePercent}% ${freePercent + usedPercent}%, #f59e0b ${freePercent + usedPercent}% 100%)`;
        const percent = document.createElement('span');
        percent.textContent = numberFormat.format(total);
        const caption = document.createElement('small');
        caption.textContent = 'puertos';
        donut.append(percent, caption);
        const list = document.createElement('ul');
        [['is-free', 'Libres', free], ['is-used', 'Ocupados', used], ['is-reserved', 'Reservados', reserved]]
            .forEach(([className, label, value]) => {
                const item = document.createElement('li');
                const dot = document.createElement('i');
                dot.className = className;
                const text = document.createElement('span');
                text.textContent = label;
                const amount = document.createElement('b');
                amount.textContent = numberFormat.format(value);
                item.append(dot, text, amount);
                list.appendChild(item);
            });
        inspectorSummary.replaceChildren(donut, list);
        renderPortRanking(summary?.top_odfs || []);
    }

    function renderPortRanking(items) {
        const target = document.getElementById('ports-occupancy-ranking');
        if (!target) return;
        target.replaceChildren();
        if (!Array.isArray(items) || !items.length) {
            const empty = document.createElement('p');
            empty.className = 'network-inspector-empty';
            empty.textContent = 'No hay ODF con los filtros actuales.';
            target.appendChild(empty);
            return;
        }
        items.forEach((odf) => {
            const item = document.createElement('div');
            item.className = 'network-ranking__item';
            item.tabIndex = 0;
            item.setAttribute('role', 'button');
            item.setAttribute('aria-label', `Filtrar ${odf.nombre}`);
            const label = document.createElement('div');
            label.className = 'network-ranking__label';
            const name = document.createElement('span');
            name.textContent = odf.nombre;
            name.title = odf.nombre;
            const value = document.createElement('b');
            value.textContent = `${percentFormat.format(odf.utilizacion || 0)}%`;
            label.append(name, value);
            const bar = document.createElement('div');
            bar.className = 'network-ranking__bar';
            const fill = document.createElement('i');
            fill.style.width = `${Math.min(100, Number(odf.utilizacion || 0))}%`;
            bar.appendChild(fill);
            item.append(label, bar);
            const select = () => {
                elements.query.value = odf.nombre;
                page = 1;
                load();
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

    function detailField(label, value) {
        const wrapper = document.createElement('div');
        const name = document.createElement('span');
        name.textContent = label;
        const content = document.createElement('b');
        content.textContent = value || '—';
        wrapper.append(name, content);
        return wrapper;
    }

    function selectPort(port, row) {
        if (!inspectorDetail || !inspectorEmpty) return;
        elements.body.querySelectorAll('tr.is-selected').forEach(item => item.classList.remove('is-selected'));
        row?.classList.add('is-selected');
        inspectorEmpty.hidden = true;
        inspectorDetail.hidden = false;
        setInspectorCollapsed(false);
        const title = document.createElement('h3');
        title.textContent = `${port.odf} · Puerto ${port.puerto}`;
        const status = document.createElement('span');
        status.className = `network-asset-status ${port.estado === 'Reservado' ? 'is-reserved' : (port.estado === 'Ocupado' ? 'is-used' : '')}`;
        status.textContent = port.estado || 'Sin estado';
        const grid = document.createElement('div');
        grid.className = 'network-asset-grid';
        grid.append(
            detailField('Site', port.site),
            detailField('Sala / Rack', `${port.sala} · ${port.rack}`),
            detailField('Bandeja', port.bandeja),
            detailField('Fibra', port.fibra),
            detailField('Conector', port.conector),
            detailField('Patchcord', port.patchcord),
            detailField('Destino', port.destino),
            detailField('Observación', port.observaciones),
        );
        const actions = document.createElement('div');
        actions.className = 'network-asset-actions';
        const asset = document.createElement('button');
        asset.type = 'button';
        asset.textContent = 'Ficha 360°';
        asset.addEventListener('click', () => window.FiberGenius.openAsset360(port.detail_url));
        const edit = document.createElement('button');
        edit.type = 'button';
        edit.textContent = root.dataset.canEdit === 'true' ? 'Editar puerto' : 'Cerrar detalle';
        edit.addEventListener('click', () => {
            if (root.dataset.canEdit === 'true') openEditor(port);
            else {
                inspectorDetail.hidden = true;
                inspectorEmpty.hidden = false;
                row?.classList.remove('is-selected');
            }
        });
        actions.append(asset);
        if (root.dataset.canViewHistory === 'true') {
            const history = document.createElement('button');
            history.type = 'button';
            history.textContent = 'Ver historial';
            history.addEventListener('click', () => openHistory(port));
            actions.append(history);
        }
        if (root.dataset.canEdit === 'true') {
            const manage = document.createElement('button');
            manage.type = 'button';
            manage.textContent = 'Gestionar conexión';
            manage.addEventListener('click', () => openManagement(port));
            actions.append(manage);
        }
        actions.append(edit);
        inspectorDetail.replaceChildren(title, status, grid, actions);
    }

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

    function closeHistory() {
        if (elements.historyDialog?.open) elements.historyDialog.close();
    }

    async function openHistory(port = null) {
        if (!elements.historyDialog || !elements.historyBody) return;
        elements.historyTitle.textContent = port
            ? `Historial · ${port.odf} / Puerto ${port.puerto}`
            : 'Historial de puertos ODF';
        elements.historySummary.textContent = 'Consultando eventos…';
        elements.historyMessage.textContent = '';
        const loadingRow = document.createElement('tr');
        const loadingCell = td('Cargando historial…');
        loadingCell.colSpan = 8;
        loadingRow.appendChild(loadingCell);
        elements.historyBody.replaceChildren(loadingRow);
        elements.historyDialog.showModal();
        try {
            const url = new URL(root.dataset.historyUrl, window.location.origin);
            url.searchParams.set('page_size', '100');
            if (port?.id) url.searchParams.set('puerto_id', String(port.id));
            const response = await fetch(url, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            });
            const payload = await response.json();
            if (!response.ok || payload.status !== 'success') {
                throw new Error(payload.message || 'No se pudo consultar el historial.');
            }
            elements.historyBody.replaceChildren();
            (payload.data || []).forEach(evento => {
                const row = document.createElement('tr');
                const fecha = evento.fecha
                    ? new Intl.DateTimeFormat('es-PE', {
                        dateStyle: 'short', timeStyle: 'medium',
                    }).format(new Date(evento.fecha))
                    : '—';
                const fibra = [evento.fibra, evento.extremo ? `Extremo ${evento.extremo}` : '']
                    .filter(Boolean).join(' · ');
                const estado = `${evento.estado_anterior || '—'} → ${evento.estado_nuevo || '—'}`;
                [
                    fecha, evento.usuario, evento.origen, evento.accion_nombre,
                    fibra, evento.puerto_anterior, evento.puerto_nuevo, estado,
                ].forEach(value => row.appendChild(td(value || '—')));
                elements.historyBody.appendChild(row);
            });
            if (!payload.data?.length) {
                const row = document.createElement('tr');
                const cell = td('No existen cambios auditados para esta selección.');
                cell.colSpan = 8;
                row.appendChild(cell);
                elements.historyBody.appendChild(row);
            }
            const total = payload.pagination?.total || 0;
            elements.historySummary.textContent = `${numberFormat.format(total)} eventos registrados`;
            if (total > 100) {
                elements.historyMessage.textContent = 'Se muestran los 100 eventos más recientes.';
            }
        } catch (error) {
            elements.historyMessage.textContent = error.message;
            const row = document.createElement('tr');
            const cell = td('No fue posible cargar el historial.');
            cell.colSpan = 8;
            row.appendChild(cell);
            elements.historyBody.replaceChildren(row);
        }
    }

    function svgButton(label, path, handler) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'ports-icon-action';
        button.setAttribute('aria-label', label);
        button.title = label;
        button.innerHTML = `<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${path}</svg>`;
        button.addEventListener('click', event => {
            event.stopPropagation();
            handler(event);
        });
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
        stateSummary(summary);
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
            row.dataset.portId = String(port.id);
            row.tabIndex = 0;
            row.setAttribute('aria-label', `Ver detalle de ${port.odf}, puerto ${port.puerto}`);
            row.addEventListener('click', () => selectPort(port, row));
            row.addEventListener('keydown', event => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    selectPort(port, row);
                }
            });
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
            if (root.dataset.canViewHistory === 'true') {
                actions.appendChild(svgButton(
                    `Ver historial de ${port.odf}, puerto ${port.puerto}`,
                    '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
                    () => openHistory(port),
                ));
            }
            if (root.dataset.canEdit === 'true') {
                actions.appendChild(svgButton(
                    `Gestionar conexión de ${port.odf}, puerto ${port.puerto}`,
                    '<path d="M8 12h8M12 8v8"/><circle cx="12" cy="12" r="9"/>',
                    () => openManagement(port),
                ));
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
        editorValue('port-editor-status').disabled = Boolean(port);
        editorValue('port-editor-fiber').value = port?.fibra === '—' ? '' : (port?.fibra || '');
        editorValue('port-editor-fiber').readOnly = true;
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

    function managementValue(id) {
        return document.getElementById(id);
    }

    function updateEndpointLabels() {
        const endpoint = managementValue('port-management-end');
        if (!endpoint) return;
        const optionA = endpoint.querySelector('option[value="A"]');
        const optionB = endpoint.querySelector('option[value="B"]');
        if (optionA) optionA.textContent = 'Extremo técnico A';
        if (optionB) optionB.textContent = 'Extremo técnico B';
    }

    function updateProvisionalFiberField() {
        const select = managementValue('port-management-fiber');
        const field = managementValue('port-management-new-fiber-field');
        const input = managementValue('port-management-new-fiber');
        const creating = select?.value === '__NUEVA__';
        if (field) field.hidden = !creating;
        if (input) {
            input.required = creating;
            if (!creating) input.value = '';
        }
        updateEndpointLabels();
    }

    function setManagementMessage(text, success = false) {
        const message = managementValue('port-management-message');
        message.textContent = text || '';
        message.classList.toggle('is-success', success);
    }

    function closeManagement() {
        if (elements.management?.open) elements.management.close();
        managedPort = null;
    }

    async function loadFibersForRoute() {
        const route = managementValue('port-management-route').value;
        const select = managementValue('port-management-fiber');
        select.replaceChildren();
        updateProvisionalFiberField();
        updateEndpointLabels();
        if (!route) {
            select.disabled = true;
            select.appendChild(new Option('Primero seleccione una ruta', ''));
            return;
        }
        select.disabled = true;
        select.appendChild(new Option('Cargando fibras…', ''));
        try {
            const url = new URL(root.dataset.fibersUrl, window.location.origin);
            if (route === '__PENDIENTE__') {
                url.searchParams.set('ruta_pendiente', '1');
            } else {
                url.searchParams.set('ruta', route);
            }
            url.searchParams.set('page_size', '200');
            const response = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.message || 'No se pudieron consultar las fibras');
            select.replaceChildren(new Option('Seleccionar fibra', ''));
            if (route === '__PENDIENTE__') {
                select.appendChild(new Option('Registrar hilo con troncal pendiente', '__NUEVA__'));
            }
            (payload.data || []).forEach(fiber => {
                const ubicaciones = [fiber.origen, fiber.destino]
                    .filter(value => value && !value.startsWith('Pendiente de orientación'))
                    .join(' ↔ ');
                const option = new Option(
                    `${fiber.numero} · ${ubicaciones || fiber.estado}`,
                    String(fiber.id),
                );
                option.dataset.trace = JSON.stringify(fiber.trazabilidad || {});
                option.dataset.state = fiber.estado || '';
                option.dataset.number = fiber.numero || '';
                option.dataset.routePending = fiber.troncal_pendiente ? 'true' : 'false';
                select.appendChild(option);
            });
            select.disabled = false;
            if (route === '__PENDIENTE__') select.value = '__NUEVA__';
            updateProvisionalFiberField();
        } catch (error) {
            select.replaceChildren(new Option('Consulta no disponible', ''));
            setManagementMessage(error.message);
        }
    }

    function openManagement(port) {
        if (!elements.management) return;
        managedPort = port;
        elements.managementForm.reset();
        managementValue('port-management-id').value = String(port.id);
        managementValue('port-management-title').textContent = `${port.odf} · Puerto ${port.puerto}`;
        managementValue('port-management-mode').textContent = `Estado actual: ${port.estado}`;
        const current = managementValue('port-management-current');
        const connectionText = port.conexion
            ? `${port.conexion.ruta} / ${port.conexion.fibra} · Extremo ${port.conexion.extremo}`
            : (port.requiere_regularizacion ? 'Ocupado sin terminación oficial: requiere vinculación' : 'Sin fibra conectada');
        current.textContent = connectionText;

        const canConnect = root.dataset.canConnect === 'true' && (
            port.estado === 'Libre' || port.estado === 'Reservado'
            || (port.estado === 'Ocupado' && !port.conexion)
        );
        managementValue('port-connect-section').hidden = !canConnect;
        managementValue('port-disconnect-section').hidden = !port.conexion
            || root.dataset.canDisconnect !== 'true';
        managementValue('port-reservation-section').hidden = !['Libre', 'Reservado'].includes(port.estado);
        managementValue('port-management-reserve').hidden = port.estado !== 'Libre';
        managementValue('port-management-cancel-reserve').hidden = port.estado !== 'Reservado';
        managementValue('port-reservation-help').textContent = port.estado === 'Reservado'
            ? 'Puede cancelar la reserva o conectar una fibra para consumirla.'
            : 'Reserve el puerto sin crear una conexión de fibra.';
        managementValue('port-management-fiber').replaceChildren(new Option('Primero seleccione una ruta', ''));
        managementValue('port-management-fiber').disabled = true;
        updateProvisionalFiberField();
        setManagementMessage('');
        elements.management.showModal();
    }

    async function postManagement(accion, extra = {}, permitirMover = false) {
        if (!managedPort) return;
        const csrf = elements.managementForm.querySelector('[name="csrfmiddlewaretoken"]').value;
        const response = await fetch(root.dataset.manageUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
            body: JSON.stringify({
                accion,
                puerto_id: managedPort.id,
                permitir_mover: permitirMover,
                ...extra,
            }),
        });
        const result = await response.json();
        if (result.status === 'confirmation_required' && result.code === 'MOVER_TERMINACION') {
            if (window.confirm(`${result.message}\n\n¿Desea moverla a este puerto?`)) {
                return postManagement(accion, extra, true);
            }
            return;
        }
        if (!response.ok || result.status !== 'success') throw new Error(result.message || 'No se pudo completar la operación');
        setManagementMessage(result.message, true);
        window.setTimeout(() => { closeManagement(); load(); }, 550);
    }

    async function connectManagedPort() {
        const fiberSelect = managementValue('port-management-fiber');
        const fiberId = fiberSelect.value;
        if (!fiberId) {
            setManagementMessage('Seleccione una fibra.');
            return;
        }
        const creatingProvisional = fiberId === '__NUEVA__';
        const provisionalNumber = managementValue('port-management-new-fiber')?.value.trim();
        if (creatingProvisional && !provisionalNumber) {
            setManagementMessage('Indique el número del hilo.');
            managementValue('port-management-new-fiber')?.focus();
            return;
        }
        const selectedFiber = fiberSelect.selectedOptions[0];
        let sincronizarFibra = false;
        if (!creatingProvisional && (selectedFiber?.dataset.state || '').toLowerCase() === 'libre') {
            sincronizarFibra = window.confirm(
                `La fibra ${selectedFiber.dataset.number} está Libre. `
                + '¿Desea informar también su estado global como Ocupado?'
            );
        }
        setManagementMessage('Conectando…');
        try {
            await postManagement('conectar', {
                fibra_id: creatingProvisional ? null : fiberId,
                fibra_numero: creatingProvisional ? provisionalNumber : '',
                extremo: managementValue('port-management-end').value,
                sincronizar_fibra: sincronizarFibra,
            });
        } catch (error) {
            setManagementMessage(error.message);
        }
    }

    async function disconnectManagedPort() {
        if (!window.confirm('¿Desea desconectar la fibra y liberar este puerto?')) return;
        const fiberNumber = managedPort?.conexion?.fibra || 'conectada';
        const sincronizarFibra = window.confirm(
            `¿Desea informar también el estado global de ${fiberNumber} como Disponible?`
        );
        setManagementMessage('Desconectando…');
        try {
            await postManagement('desconectar', {
                sincronizar_fibra: sincronizarFibra,
            });
        } catch (error) {
            setManagementMessage(error.message);
        }
    }

    async function simpleManagementAction(accion) {
        setManagementMessage('Aplicando cambio…');
        try {
            await postManagement(accion);
        } catch (error) {
            setManagementMessage(error.message);
        }
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

    function closeBulkImport() {
        if (elements.bulkDialog?.open) elements.bulkDialog.close();
    }

    function resetBulkResult() {
        if (!elements.bulkBody) return;
        elements.bulkBody.replaceChildren();
        const row = document.createElement('tr');
        const empty = document.createElement('td');
        empty.colSpan = 6;
        empty.textContent = 'Sin resultados.';
        row.appendChild(empty);
        elements.bulkBody.appendChild(row);
        elements.bulkApply.disabled = true;
        elements.bulkReport.hidden = true;
        elements.bulkMessage.textContent = '';
        elements.bulkSummary.textContent = 'Seleccione y valide un archivo.';
    }

    function renderBulkResult(payload) {
        const rows = payload.rows || [];
        elements.bulkBody.replaceChildren();
        rows.slice(0, 250).forEach(item => {
            const row = document.createElement('tr');
            row.className = item.resultado === 'ERROR' ? 'is-error' : 'is-valid';
            [
                item.fila,
                item.resultado,
                item.accion,
                [item.site, item.odf, item.puerto].filter(Boolean).join(' / '),
                [item.troncal, item.fibra, item.extremo].filter(Boolean).join(' / '),
                item.mensaje,
            ].forEach(value => row.appendChild(td(value || '—')));
            elements.bulkBody.appendChild(row);
        });
        if (rows.length > 250) {
            const row = document.createElement('tr');
            const more = document.createElement('td');
            more.colSpan = 6;
            more.textContent = `Se muestran 250 de ${rows.length} filas. El reporte descargable contiene todas.`;
            row.appendChild(more);
            elements.bulkBody.appendChild(row);
        }
        const summary = payload.summary || {};
        elements.bulkSummary.textContent = `${summary.total || 0} filas · ${summary.errores || 0} errores · ${summary.sin_cambios || 0} sin cambios`;
        elements.bulkMessage.textContent = payload.message || '';
        elements.bulkMessage.classList.toggle('is-success', payload.status === 'valid' || payload.status === 'success');
        elements.bulkApply.disabled = !payload.can_apply;
        if (payload.report_url) {
            elements.bulkReport.href = payload.report_url;
            elements.bulkReport.hidden = false;
        }
    }

    async function submitBulkImport(mode) {
        const file = elements.bulkFile?.files?.[0];
        if (!file) {
            elements.bulkMessage.textContent = 'Seleccione un archivo .xlsx.';
            return;
        }
        if (mode === 'aplicar' && !window.confirm('¿Desea aplicar todas las operaciones validadas?')) return;
        elements.bulkValidate.disabled = true;
        elements.bulkApply.disabled = true;
        elements.bulkMessage.textContent = mode === 'aplicar' ? 'Aplicando archivo…' : 'Validando archivo…';
        const data = new FormData();
        data.append('archivo', file);
        data.append('modo', mode);
        const csrf = elements.bulkForm.querySelector('[name="csrfmiddlewaretoken"]').value;
        try {
            const response = await fetch(root.dataset.bulkImportUrl, {
                method: 'POST',
                headers: { 'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest' },
                body: data,
            });
            const payload = await response.json();
            renderBulkResult(payload);
            if (payload.status === 'success') load();
        } catch (_error) {
            elements.bulkMessage.classList.remove('is-success');
            elements.bulkMessage.textContent = 'No se pudo procesar el archivo.';
        } finally {
            elements.bulkValidate.disabled = false;
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
    elements.historyOpen?.addEventListener('click', () => openHistory());
    document.getElementById('ports-history-close')?.addEventListener('click', closeHistory);
    document.getElementById('ports-history-dismiss')?.addEventListener('click', closeHistory);
    elements.bulkOpen?.addEventListener('click', () => { resetBulkResult(); elements.bulkDialog.showModal(); });
    elements.bulkFile?.addEventListener('change', resetBulkResult);
    elements.bulkValidate?.addEventListener('click', () => submitBulkImport('validar'));
    elements.bulkApply?.addEventListener('click', () => submitBulkImport('aplicar'));
    document.getElementById('ports-bulk-close')?.addEventListener('click', closeBulkImport);
    document.getElementById('ports-bulk-cancel')?.addEventListener('click', closeBulkImport);
    elements.editorForm?.addEventListener('submit', saveEditor);
    document.getElementById('port-editor-close')?.addEventListener('click', closeEditor);
    document.getElementById('port-editor-cancel')?.addEventListener('click', closeEditor);
    managementValue('port-management-close')?.addEventListener('click', closeManagement);
    managementValue('port-management-cancel')?.addEventListener('click', closeManagement);
    managementValue('port-management-route')?.addEventListener('change', loadFibersForRoute);
    managementValue('port-management-fiber')?.addEventListener('change', updateProvisionalFiberField);
    managementValue('port-management-connect')?.addEventListener('click', connectManagedPort);
    managementValue('port-management-disconnect')?.addEventListener('click', disconnectManagedPort);
    managementValue('port-management-reserve')?.addEventListener('click', () => simpleManagementAction('reservar'));
    managementValue('port-management-cancel-reserve')?.addEventListener('click', () => simpleManagementAction('cancelar_reserva'));

    const incoming = new URLSearchParams(window.location.search);
    elements.query.value = incoming.get('q') || '';
    if (['Libre', 'Ocupado', 'Reservado'].includes(incoming.get('estado'))) elements.status.value = incoming.get('estado');
    elements.site.value = incoming.get('site') || '';
    elements.room.value = incoming.get('sala') || '';
    elements.rack.value = incoming.get('rack') || '';
    if (['10', '25', '50', '100', '200'].includes(incoming.get('page_size'))) elements.pageSize.value = incoming.get('page_size');
    inspectorToggle?.addEventListener('click', () => setInspectorCollapsed(!inspector.classList.contains('is-collapsed')));
    setInspectorCollapsed(window.innerWidth <= 1180);
    load();
})();
