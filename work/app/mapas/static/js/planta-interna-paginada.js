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
        rankingContext: document.getElementById('ports-ranking-context'),
        rankingContextLabel: document.getElementById('ports-ranking-context-label'),
        rankingContextClear: document.getElementById('ports-ranking-context-clear'),
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
    const canCreateFiber = root.dataset.canCreateFiber === 'true';
    let page = 1;
    let abortController = null;
    let debounceTimer = null;
    let currentRows = [];
    let exportObjectUrl = null;
    let managedPort = null;
    let fiberSearchTimer = null;
    let rankingOdfId = '';
    let rankingOdf = '';
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
        const unknown = Math.max(0, total - free - used - reserved);
        const percent = (value) => total ? Math.max(0, Math.min(100, (value / total) * 100)) : 0;
        const freePercent = percent(free);
        const usedPercent = percent(used);
        const reservedPercent = percent(reserved);
        const usedEnd = freePercent + usedPercent;
        const reservedEnd = usedEnd + reservedPercent;
        const donut = document.createElement('div');
        donut.className = 'network-donut';
        donut.style.background = total
            ? `conic-gradient(#18b777 0 ${freePercent}%, #2f74ee ${freePercent}% ${usedEnd}%, #f59e0b ${usedEnd}% ${reservedEnd}%, #94a3b8 ${reservedEnd}% 100%)`
            : 'conic-gradient(#e8edf5 0 100%)';
        const totalNode = document.createElement('span');
        totalNode.textContent = numberFormat.format(total);
        const caption = document.createElement('small');
        caption.textContent = 'puertos';
        donut.append(totalNode, caption);
        const list = document.createElement('ul');
        const states = [
            ['is-free', 'Libres', 'LIBRE', free],
            ['is-used', 'Ocupados', 'OCUPADO', used],
            ['is-reserved', 'Reservados', 'RESERVADO', reserved],
        ];
        if (unknown) states.push(['is-unknown', 'Sin información', '', unknown]);
        states.forEach(([className, label, statusValue, value]) => {
                const item = document.createElement('li');
                const button = document.createElement('button');
                button.type = 'button';
                const selected = elements.status.value === statusValue && Boolean(statusValue);
                button.classList.toggle('is-selected', selected);
                button.classList.toggle('is-zero', Number(value) === 0);
                button.setAttribute('aria-pressed', String(selected));
                const dot = document.createElement('i');
                dot.className = className;
                const text = document.createElement('span');
                text.textContent = label;
                const amount = document.createElement('b');
                const count = document.createElement('span');
                count.textContent = numberFormat.format(value);
                const ratio = document.createElement('small');
                ratio.textContent = `${percentFormat.format(percent(value))}%`;
                amount.append(count, ratio);
                button.append(dot, text, amount);
                if (statusValue) {
                    button.addEventListener('click', () => {
                        elements.status.value = selected ? '' : statusValue;
                        page = 1;
                        load();
                    });
                } else {
                    button.disabled = true;
                }
                item.appendChild(button);
                list.appendChild(item);
            });
        inspectorSummary.replaceChildren(donut, list);
        const source = document.getElementById('ports-inspector-source');
        if (source) {
            source.replaceChildren();
            const informed = document.createElement('span');
            informed.textContent = numberFormat.format(total - unknown);
            const missing = document.createElement('span');
            missing.textContent = numberFormat.format(unknown);
            source.append(informed, ' con estado · ', missing, ' sin información');
        }
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
            const selected = rankingOdfId === String(odf.id);
            item.classList.toggle('is-selected', selected);
            item.setAttribute('aria-pressed', String(selected));
            const label = document.createElement('div');
            label.className = 'network-ranking__label';
            const name = document.createElement('span');
            name.textContent = odf.nombre;
            name.title = odf.nombre;
            const value = document.createElement('b');
            value.textContent = `${numberFormat.format(odf.utilizados || 0)} en uso de ${numberFormat.format(odf.total || 0)}`;
            label.append(name, value);
            const bar = document.createElement('div');
            bar.className = 'network-ranking__bar';
            const fill = document.createElement('i');
            fill.style.width = `${Math.min(100, Number(odf.utilizacion || 0))}%`;
            bar.appendChild(fill);
            const detail = document.createElement('small');
            detail.textContent = `${percentFormat.format(odf.utilizacion || 0)}% de ocupación · ${numberFormat.format(Math.max(0, Number(odf.total || 0) - Number(odf.utilizados || 0)))} libres`;
            item.append(label, bar, detail);
            const select = () => {
                rankingOdfId = selected ? '' : String(odf.id);
                rankingOdf = selected ? '' : odf.nombre;
                syncRankingContext();
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

    function syncRankingContext() {
        if (!elements.rankingContext) return;
        const active = Boolean(rankingOdfId);
        elements.rankingContext.hidden = !active;
        if (elements.rankingContextLabel) elements.rankingContextLabel.textContent = active ? rankingOdf : '';
    }

    function clearRankingContext({ reload = true } = {}) {
        rankingOdf = '';
        rankingOdfId = '';
        syncRankingContext();
        page = 1;
        if (reload) load();
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
        status.className = `network-asset-status ${port.estado === 'RESERVADO' ? 'is-reserved' : (port.estado === 'OCUPADO' ? 'is-used' : '')}`;
        status.textContent = port.estado_label || 'Sin estado';
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
            manage.textContent = 'Gestionar puerto';
            manage.addEventListener('click', () => openManagement(port));
            actions.append(manage);
        } else {
            const close = document.createElement('button');
            close.type = 'button';
            close.textContent = 'Cerrar detalle';
            close.addEventListener('click', () => {
                inspectorDetail.hidden = true;
                inspectorEmpty.hidden = false;
                row?.classList.remove('is-selected');
            });
            actions.append(close);
        }
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
        const modifier = value === 'OCUPADO'
            ? 'is-used'
            : (value === 'RESERVADO' ? 'is-reserved' : 'is-free');
        badge.className = `port-status ${modifier}`;
        badge.textContent = {
            LIBRE: 'Libre',
            OCUPADO: 'Ocupado',
            RESERVADO: 'Reservado',
        }[value] || 'Sin estado';
        return badge;
    }

    function currentParams(includePage = true) {
        const params = new URLSearchParams();
        const fields = [
            ['q', elements.query.value.trim()],
            ['odf_id', rankingOdfId],
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
                    `Gestionar ${port.odf}, puerto ${port.puerto}`,
                    '<path d="M12 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21h-4v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3.1 14H3v-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3.1V3h4v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.5 1h.1v4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>',
                    () => openManagement(port),
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

    function openEditor() {
        if (!elements.editor) return;
        elements.editorForm.reset();
        editorValue('port-editor-id').value = '';
        editorValue('port-editor-title').textContent = 'Nuevo puerto ODF';
        editorValue('port-editor-mode').textContent = 'Registra una nueva posición física en un ODF.';
        editorValue('port-editor-submit').textContent = 'Crear puerto';
        editorValue('port-editor-odf').disabled = false;
        editorValue('port-editor-status').disabled = false;
        editorValue('port-editor-message').textContent = '';
        editorValue('port-editor-message').classList.remove('is-success');
        elements.editor.showModal();
        editorValue('port-editor-odf').focus();
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

    function appendNewFiberOption(select) {
        if (canCreateFiber) {
            select.appendChild(new Option('Registrar nuevo hilo · troncal pendiente', '__NUEVA__'));
        }
    }

    function emptyFiberPrompt() {
        return canCreateFiber
            ? 'Busca una fibra o registra un hilo pendiente'
            : 'Busca una fibra existente';
    }

    function terminationLabel(termination, endpoint) {
        if (!termination?.confirmado) return `Extremo ${endpoint}: pendiente`;
        const odfPort = [
            termination.odf,
            termination.puerto ? `Puerto ${termination.puerto}` : '',
        ].filter(Boolean).join(' / ');
        const location = [
            termination.site,
            odfPort,
        ].filter(Boolean).join(' · ');
        return `Extremo ${endpoint}: ${location}`;
    }

    function fiberTerminationContext(fiber) {
        return [
            terminationLabel(fiber.terminacion_a, 'A'),
            terminationLabel(fiber.terminacion_b, 'B'),
        ].join('  |  ');
    }

    function suggestMissingEndpoint() {
        const select = managementValue('port-management-fiber');
        const endpoint = managementValue('port-management-end');
        const option = select?.selectedOptions?.[0];
        if (!option || !endpoint || !option.value || option.value === '__NUEVA__') return;
        let trace = {};
        try {
            trace = JSON.parse(option.dataset.trace || '{}');
        } catch (_error) {
            return;
        }
        const hasA = trace.extremo_a?.confirmado === true;
        const hasB = trace.extremo_b?.confirmado === true;
        if (hasA && !hasB) endpoint.value = 'B';
        if (hasB && !hasA) endpoint.value = 'A';
    }

    function setManagementMessage(text, success = false) {
        const message = managementValue('port-management-message');
        message.textContent = text || '';
        message.classList.toggle('is-success', success);
    }

    function clearFiberResults() {
        const target = managementValue('port-management-fiber-results');
        if (!target) return;
        target.replaceChildren();
        target.hidden = true;
    }

    function syncFiberResultSelection() {
        const selectedId = managementValue('port-management-fiber')?.value || '';
        managementValue('port-management-fiber-results')
            ?.querySelectorAll('.port-management-fiber-result')
            .forEach(button => button.classList.toggle('is-selected', button.dataset.fiberId === selectedId));
    }

    function renderFiberResults(fibers) {
        const target = managementValue('port-management-fiber-results');
        if (!target) return;
        target.replaceChildren();
        if (!fibers.length) {
            target.hidden = true;
            return;
        }

        const visibleFibers = fibers.slice(0, 8);
        const header = document.createElement('div');
        header.className = 'port-management-fiber-results__header';
        const title = document.createElement('strong');
        title.textContent = `${numberFormat.format(fibers.length)} fibras encontradas`;
        const help = document.createElement('span');
        help.textContent = fibers.length > visibleFibers.length
            ? `Mostrando ${visibleFibers.length}; refina la búsqueda para ver menos`
            : 'Selecciona una para conectarla';
        header.append(title, help);

        const list = document.createElement('div');
        list.className = 'port-management-fiber-results__list';
        visibleFibers.forEach(fiber => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'port-management-fiber-result';
            button.dataset.fiberId = String(fiber.id);
            const name = document.createElement('strong');
            name.textContent = `Fibra ${fiber.numero || 'sin número'}`;
            const route = document.createElement('span');
            route.textContent = `${fiber.troncal || 'Troncal pendiente'} · ${fiber.estado_label || fiber.estado || 'Sin información'}`;
            const locations = document.createElement('small');
            locations.textContent = fiberTerminationContext(fiber);
            button.title = `${name.textContent} · ${route.textContent} · ${locations.textContent}`;
            button.append(name, route, locations);
            button.addEventListener('click', () => {
                const select = managementValue('port-management-fiber');
                select.value = String(fiber.id);
                updateProvisionalFiberField();
                suggestMissingEndpoint();
                syncFiberResultSelection();
            });
            list.appendChild(button);
        });
        target.append(header, list);
        target.hidden = false;
    }

    function closeManagement() {
        if (elements.management?.open) elements.management.close();
        managedPort = null;
    }

    function setActiveManagementTab(tabName) {
        const isData = tabName === 'data';
        const dataTab = managementValue('port-management-data-tab');
        const connectionTab = managementValue('port-management-connection-tab');
        const dataPanel = managementValue('port-management-data-panel');
        const connectionPanel = managementValue('port-management-connection-panel');
        dataTab?.classList.toggle('is-active', isData);
        connectionTab?.classList.toggle('is-active', !isData);
        dataTab?.setAttribute('aria-selected', String(isData));
        connectionTab?.setAttribute('aria-selected', String(!isData));
        if (dataPanel) dataPanel.hidden = !isData;
        if (connectionPanel) connectionPanel.hidden = isData;
        setManagementMessage('');
    }

    function renderManagementSummary(port) {
        const target = managementValue('port-management-current');
        if (!target) return;
        const status = document.createElement('span');
        status.className = `port-management-status ${port.estado === 'OCUPADO' ? 'is-used' : (port.estado === 'RESERVADO' ? 'is-reserved' : 'is-free')}`;
        status.textContent = port.estado_label || 'Libre';
        const identity = document.createElement('div');
        const title = document.createElement('strong');
        title.textContent = `${port.site} · ${port.odf} · Puerto ${port.puerto}`;
        const connection = document.createElement('p');
        if (port.conexion) {
            connection.textContent = `${port.conexion.fibra} · Extremo ${port.conexion.extremo} · ${port.conexion.ruta || 'Troncal pendiente'}`;
        } else if (port.estado === 'OCUPADO') {
            connection.textContent = 'Inconsistencia: puerto ocupado sin terminación oficial.';
        } else if (port.estado === 'RESERVADO') {
            connection.textContent = 'Puerto reservado, sin fibra conectada.';
        } else {
            connection.textContent = 'Puerto libre, sin fibra conectada.';
        }
        identity.append(title, connection);
        target.replaceChildren(identity, status);
    }

    function fillManagementData(port) {
        managementValue('port-management-data-odf').value = port.odf || '';
        managementValue('port-management-data-number').value = port.puerto || '';
        managementValue('port-management-data-tray').value = port.bandeja === '—' ? '' : (port.bandeja || '');
        managementValue('port-management-data-connector').value = port.conector === '—' ? '' : (port.conector || '');
        managementValue('port-management-data-patchcord').value = ['—', 'No'].includes(port.patchcord) ? '' : (port.patchcord || '');
        managementValue('port-management-data-destination').value = port.destino === '—' ? '' : (port.destino || '');
        managementValue('port-management-data-notes').value = port.observaciones || '';
    }

    async function loadFibers() {
        const route = managementValue('port-management-route').value;
        const query = managementValue('port-management-fiber-query').value.trim();
        const select = managementValue('port-management-fiber');
        select.replaceChildren();
        updateProvisionalFiberField();
        updateEndpointLabels();
        if (!route && query.length === 1) {
            clearFiberResults();
            select.disabled = true;
            select.appendChild(new Option('Escribe al menos dos caracteres', ''));
            return;
        }
        if (!route && !query) {
            clearFiberResults();
            select.disabled = false;
            select.appendChild(new Option(emptyFiberPrompt(), ''));
            appendNewFiberOption(select);
            updateProvisionalFiberField();
            return;
        }
        select.disabled = true;
        select.appendChild(new Option('Cargando fibras…', ''));
        setManagementMessage('Buscando fibras…');
        try {
            const url = new URL(root.dataset.fibersUrl, window.location.origin);
            if (route === '__PENDIENTE__') {
                url.searchParams.set('ruta_pendiente', '1');
            } else if (route) {
                url.searchParams.set('ruta', route);
            }
            if (query) url.searchParams.set('q', query);
            url.searchParams.set('page_size', '200');
            const response = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.message || 'No se pudieron consultar las fibras');
            const fibers = payload.data || [];
            select.replaceChildren(new Option(fibers.length ? 'Seleccionar fibra' : 'No se encontraron fibras', ''));
            if (!route || route === '__PENDIENTE__') {
                appendNewFiberOption(select);
            }
            fibers.forEach(fiber => {
                const option = new Option(
                    `${fiber.numero} · ${fiber.troncal || 'Troncal pendiente'} · ${fiberTerminationContext(fiber)}`,
                    String(fiber.id),
                );
                option.dataset.trace = JSON.stringify(fiber.trazabilidad || {});
                option.dataset.state = fiber.estado || '';
                option.dataset.number = fiber.numero || '';
                option.dataset.routePending = fiber.troncal_pendiente ? 'true' : 'false';
                select.appendChild(option);
            });
            renderFiberResults(fibers);
            select.disabled = false;
            updateProvisionalFiberField();
            setManagementMessage(
                fibers.length === 200
                    ? 'Se muestran los primeros 200 resultados. Refina la búsqueda si no encuentras la fibra.'
                    : `${numberFormat.format(fibers.length)} fibra(s) encontrada(s).`,
                true,
            );
        } catch (error) {
            clearFiberResults();
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
        managementValue('port-management-mode').textContent = 'Datos físicos, conexión y reserva en un solo lugar.';
        renderManagementSummary(port);
        fillManagementData(port);

        const canConnect = root.dataset.canConnect === 'true' && (
            port.estado === 'LIBRE' || port.estado === 'RESERVADO'
            || (port.estado === 'OCUPADO' && !port.conexion)
        );
        managementValue('port-connect-section').hidden = !canConnect;
        managementValue('port-disconnect-section').hidden = !port.conexion
            || root.dataset.canDisconnect !== 'true';
        managementValue('port-reservation-section').hidden = !['LIBRE', 'RESERVADO'].includes(port.estado);
        managementValue('port-management-reserve').hidden = port.estado !== 'LIBRE';
        managementValue('port-management-cancel-reserve').hidden = port.estado !== 'RESERVADO';
        managementValue('port-reservation-help').textContent = port.estado === 'RESERVADO'
            ? 'Puede cancelar la reserva o conectar una fibra para consumirla.'
            : 'Reserve el puerto sin crear una conexión de fibra.';
        managementValue('port-management-fiber-query').value = '';
        managementValue('port-management-route').value = '';
        clearFiberResults();
        managementValue('port-management-fiber').replaceChildren(
            new Option(emptyFiberPrompt(), ''),
        );
        appendNewFiberOption(managementValue('port-management-fiber'));
        managementValue('port-management-fiber').disabled = false;
        updateProvisionalFiberField();
        setManagementMessage('');
        setActiveManagementTab('data');
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
        if (creatingProvisional && !canCreateFiber) {
            setManagementMessage('No tiene permiso para registrar una fibra nueva. Seleccione una fibra existente.');
            return;
        }
        const provisionalNumber = managementValue('port-management-new-fiber')?.value.trim();
        if (creatingProvisional && !provisionalNumber) {
            setManagementMessage('Indique el número del hilo.');
            managementValue('port-management-new-fiber')?.focus();
            return;
        }
        const selectedFiber = fiberSelect.selectedOptions[0];
        const fiberLabel = creatingProvisional
            ? `${provisionalNumber} (troncal pendiente)`
            : selectedFiber.textContent;
        const endpoint = managementValue('port-management-end').value;
        if (!window.confirm(
            `¿Conectar ${fiberLabel} · extremo ${endpoint} con ${managedPort.odf} / puerto ${managedPort.puerto}?`
        )) return;
        const sincronizarFibra = !creatingProvisional
            && selectedFiber?.dataset.state === 'DISPONIBLE'
            && window.confirm(
                `La fibra ${selectedFiber?.dataset.number || fiberLabel} está Disponible. ¿Desea informar también su estado global como Ocupado?`
            );
        setManagementMessage('Conectando…');
        try {
            await postManagement('conectar', {
                fibra_id: creatingProvisional ? null : fiberId,
                fibra_numero: creatingProvisional ? provisionalNumber : '',
                extremo: endpoint,
                sincronizar_fibra: sincronizarFibra,
            });
        } catch (error) {
            setManagementMessage(error.message);
        }
    }

    async function disconnectManagedPort() {
        const fiberNumber = managedPort?.conexion?.fibra || 'conectada';
        if (!window.confirm(
            `¿Desconectar la fibra ${fiberNumber} de ${managedPort.odf} / puerto ${managedPort.puerto}?\n\nEl puerto quedará Libre.`
        )) return;
        const sincronizarFibra = window.confirm(
            `¿Desea informar también la fibra ${fiberNumber} como Disponible?`
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
        const confirmation = accion === 'reservar'
            ? `¿Reservar ${managedPort.odf} / puerto ${managedPort.puerto}? No se conectará ninguna fibra.`
            : `¿Cancelar la reserva de ${managedPort.odf} / puerto ${managedPort.puerto}? El puerto quedará Libre.`;
        if (!window.confirm(confirmation)) return;
        setManagementMessage('Aplicando cambio…');
        try {
            await postManagement(accion);
        } catch (error) {
            setManagementMessage(error.message);
        }
    }

    async function saveManagementData() {
        if (!managedPort) return;
        const payload = {
            id: managedPort.id,
            bandeja: managementValue('port-management-data-tray').value.trim(),
            tipo_conector: managementValue('port-management-data-connector').value.trim(),
            patchcord: managementValue('port-management-data-patchcord').value.trim(),
            destino: managementValue('port-management-data-destination').value.trim(),
            observaciones: managementValue('port-management-data-notes').value.trim(),
        };
        const original = Object.fromEntries(Object.keys(payload).map(key => [key, managedPort[key] === '—' ? '' : (managedPort[key] ?? '')]));
        original.tipo_conector = managedPort.conector === '—' ? '' : (managedPort.conector || '');
        original.patchcord = ['—', 'No'].includes(managedPort.patchcord) ? '' : (managedPort.patchcord || '');
        const changes = InventoryEdit.patch(payload, original, ['id']);
        if (!window.confirm(`¿Guardar los datos de ${managedPort.odf} / puerto ${managedPort.puerto}?`)) return;
        const csrf = elements.managementForm.querySelector('[name="csrfmiddlewaretoken"]').value;
        setManagementMessage('Guardando datos…');
        try {
            const response = await fetch(root.dataset.updateUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
                body: JSON.stringify(changes),
            });
            const result = await response.json();
            if (!response.ok || result.status !== 'success') throw new Error(result.message || 'No se pudieron guardar los datos');
            setManagementMessage(result.message, true);
            window.setTimeout(() => { closeManagement(); load(); }, 500);
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
        rankingOdf = '';
        rankingOdfId = '';
        syncRankingContext();
        page = 1;
        elements.query.focus();
        load();
    });
    elements.rankingContextClear?.addEventListener('click', () => clearRankingContext());
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
    managementValue('port-management-data-tab')?.addEventListener('click', () => setActiveManagementTab('data'));
    managementValue('port-management-connection-tab')?.addEventListener('click', () => setActiveManagementTab('connection'));
    managementValue('port-management-save-data')?.addEventListener('click', saveManagementData);
    managementValue('port-management-route')?.addEventListener('change', loadFibers);
    managementValue('port-management-fiber-search')?.addEventListener('click', loadFibers);
    managementValue('port-management-fiber-query')?.addEventListener('keydown', event => {
        if (event.key === 'Enter') {
            event.preventDefault();
            loadFibers();
        }
    });
    managementValue('port-management-fiber-query')?.addEventListener('input', () => {
        window.clearTimeout(fiberSearchTimer);
        fiberSearchTimer = window.setTimeout(() => {
            const value = managementValue('port-management-fiber-query').value.trim();
            if (value.length >= 2) loadFibers();
        }, 350);
    });
    managementValue('port-management-fiber')?.addEventListener('change', () => {
        updateProvisionalFiberField();
        suggestMissingEndpoint();
        syncFiberResultSelection();
    });
    managementValue('port-management-connect')?.addEventListener('click', connectManagedPort);
    managementValue('port-management-disconnect')?.addEventListener('click', disconnectManagedPort);
    managementValue('port-management-reserve')?.addEventListener('click', () => simpleManagementAction('reservar'));
    managementValue('port-management-cancel-reserve')?.addEventListener('click', () => simpleManagementAction('cancelar_reserva'));

    const incoming = new URLSearchParams(window.location.search);
    elements.query.value = incoming.get('q') || '';
    if (['LIBRE', 'OCUPADO', 'RESERVADO'].includes(incoming.get('estado'))) elements.status.value = incoming.get('estado');
    elements.site.value = incoming.get('site') || '';
    elements.room.value = incoming.get('sala') || '';
    elements.rack.value = incoming.get('rack') || '';
    if (['10', '25', '50', '100', '200'].includes(incoming.get('page_size'))) elements.pageSize.value = incoming.get('page_size');
    inspectorToggle?.addEventListener('click', () => setInspectorCollapsed(!inspector.classList.contains('is-collapsed')));
    setInspectorCollapsed(window.innerWidth <= 1180);
    load();
})();
