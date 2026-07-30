(function () {
    'use strict';

    const root = document.getElementById('odf-workbench');
    if (!root) return;

    const elements = {
        form: document.getElementById('odf-filters'),
        query: document.getElementById('odf-query'),
        site: document.getElementById('odf-site-filter'),
        room: document.getElementById('odf-room-filter'),
        rack: document.getElementById('odf-rack-filter'),
        status: document.getElementById('odf-status-filter'),
        capacity: document.getElementById('odf-capacity-filter'),
        pageSize: document.getElementById('odf-page-size'),
        clear: document.getElementById('odf-clear'),
        body: document.getElementById('odf-body'),
        message: document.getElementById('odf-message'),
        pageInfo: document.getElementById('odf-page-info'),
        pagination: document.getElementById('odf-pagination'),
        previous: document.getElementById('odf-previous'),
        next: document.getElementById('odf-next'),
        exportLink: document.getElementById('odf-export'),
        exportStatus: document.getElementById('odf-export-status'),
        newOdf: document.getElementById('odf-new'),
        editor: document.getElementById('odf-editor'),
        editorForm: document.getElementById('odf-editor-form'),
    };
    const stats = {
        total: document.getElementById('odf-stat-total'),
        capacity: document.getElementById('odf-stat-capacity'),
        used: document.getElementById('odf-stat-used'),
        free: document.getElementById('odf-stat-free'),
        reserved: document.getElementById('odf-stat-reserved'),
        sites: document.getElementById('odf-stat-sites'),
    };
    const numberFormat = new Intl.NumberFormat('es-PE');
    const percentFormat = new Intl.NumberFormat('es-PE', { maximumFractionDigits: 1 });
    let page = 1;
    let abortController = null;
    let debounceTimer = null;
    let exportObjectUrl = null;
    let pendingOdfId = null;
    const inspectorSummary = document.getElementById('odf-inspector-summary');
    const inspectorEmpty = document.getElementById('odf-inspector-empty');
    const inspectorDetail = document.getElementById('odf-inspector-detail');
    const inspector = document.getElementById('network-inspector');
    const inspectorToggle = document.getElementById('network-inspector-toggle');

    function setInspectorCollapsed(collapsed) {
        if (!inspector || !inspectorToggle) return;
        inspector.classList.toggle('is-collapsed', collapsed);
        inspectorToggle.setAttribute('aria-expanded', String(!collapsed));
        inspectorToggle.setAttribute('aria-label', collapsed ? 'Expandir panel' : 'Contraer panel');
    }

    function putText(id, value) {
        const node = document.getElementById(id);
        if (node) node.textContent = value;
    }

    function renderRanking(items) {
        const target = document.getElementById('odf-occupancy-ranking');
        if (!target) return;
        target.replaceChildren();
        if (!Array.isArray(items) || !items.length) {
            const empty = document.createElement('p');
            empty.className = 'network-inspector-empty';
            empty.textContent = 'No hay ODF con capacidad registrada.';
            target.appendChild(empty);
            return;
        }
        items.forEach((item) => {
            const rankingItem = document.createElement('div');
            rankingItem.className = 'network-ranking__item';
            rankingItem.tabIndex = 0;
            rankingItem.setAttribute('role', 'button');
            rankingItem.setAttribute('aria-label', `Filtrar ${item.odf}`);
            const description = document.createElement('div');
            description.className = 'network-ranking__label';
            const name = document.createElement('em');
            name.textContent = item.odf;
            name.title = item.odf;
            const percent = document.createElement('b');
            percent.textContent = `${percentFormat.format(item.porcentaje || 0)}%`;
            description.append(name, percent);
            const bar = document.createElement('div');
            bar.className = 'network-ranking__bar';
            const fill = document.createElement('i');
            fill.style.width = `${Math.max(0, Math.min(100, Number(item.porcentaje || 0)))}%`;
            bar.appendChild(fill);
            rankingItem.append(description, bar);
            const select = () => {
                pendingOdfId = Number(item.id);
                elements.query.value = item.odf;
                page = 1;
                load();
            };
            rankingItem.addEventListener('click', select);
            rankingItem.addEventListener('keydown', event => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    select();
                }
            });
            target.appendChild(rankingItem);
        });
    }

    function stateSummary(summary) {
        if (!inspectorSummary) return;
        const capacity = Number(summary?.capacidad || 0);
        const used = Number(summary?.ocupados || 0);
        const free = Number(summary?.libres || 0);
        const reserved = Number(summary?.reservados || 0);
        const usedPercent = capacity ? Math.min(100, (used / capacity) * 100) : 0;
        const reservedPercent = capacity ? Math.min(100 - usedPercent, (reserved / capacity) * 100) : 0;
        putText('odf-utilization-percent', `${percentFormat.format(usedPercent)}%`);
        putText('odf-utilization-used-label', `${numberFormat.format(used)} · ${percentFormat.format(usedPercent)}%`);
        putText('odf-utilization-reserved-label', `${numberFormat.format(reserved)} · ${percentFormat.format(reservedPercent)}%`);
        putText('odf-utilization-free-label', `${numberFormat.format(free)} · ${percentFormat.format(capacity ? (free / capacity) * 100 : 0)}%`);
        const usedBar = document.getElementById('odf-utilization-used');
        const reservedBar = document.getElementById('odf-utilization-reserved');
        if (usedBar) usedBar.style.width = `${usedPercent}%`;
        if (reservedBar) reservedBar.style.width = `${reservedPercent}%`;
        renderRanking(summary?.top_ocupacion || []);
        const donut = document.createElement('div');
        donut.className = 'network-donut';
        donut.style.background = `conic-gradient(#16a85f 0 ${usedPercent}%, #e88a08 ${usedPercent}% ${usedPercent + reservedPercent}%, #09a6b4 ${usedPercent + reservedPercent}% 100%)`;
        const percent = document.createElement('span');
        percent.textContent = `${percentFormat.format(usedPercent)}%`;
        const caption = document.createElement('small');
        caption.textContent = 'ocupados';
        donut.append(percent, caption);
        const list = document.createElement('ul');
        [['is-used', 'Ocupados', used], ['is-free', 'Libres', free], ['is-reserved', 'Reservados', reserved]]
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
    }

    function detailField(label, value) {
        const wrapper = document.createElement('div');
        const name = document.createElement('span');
        name.textContent = label;
        const content = document.createElement('b');
        content.textContent = value ?? '—';
        wrapper.append(name, content);
        return wrapper;
    }

    function selectOdf(odf, row) {
        if (!inspectorDetail || !inspectorEmpty) return;
        elements.body.querySelectorAll('tr.is-selected').forEach(item => item.classList.remove('is-selected'));
        row?.classList.add('is-selected');
        inspectorEmpty.hidden = true;
        inspectorDetail.hidden = false;
        setInspectorCollapsed(false);
        const title = document.createElement('h3');
        title.textContent = odf.odf;
        const status = document.createElement('span');
        status.className = 'network-asset-status';
        status.textContent = odf.estado || 'Sin estado';
        const grid = document.createElement('div');
        grid.className = 'network-asset-grid';
        grid.append(
            detailField('Site', odf.site),
            detailField('Sala / Rack', `${odf.sala} · ${odf.rack}`),
            detailField('Conector', odf.conector),
            detailField('Capacidad', numberFormat.format(odf.capacidad || 0)),
            detailField('Ocupados', numberFormat.format(odf.ocupados || 0)),
            detailField('Libres / Reservados', `${numberFormat.format(odf.libres || 0)} / ${numberFormat.format(odf.reservados || 0)}`),
        );
        const capacity = Number(odf.capacidad || 0);
        const usedPercent = capacity ? Math.min(100, (Number(odf.ocupados || 0) / capacity) * 100) : 0;
        const capacityBox = document.createElement('div');
        capacityBox.className = 'network-capacity';
        const capacityLabel = document.createElement('div');
        capacityLabel.className = 'network-capacity__label';
        capacityLabel.innerHTML = `<span>Ocupación</span><b>${percentFormat.format(usedPercent)}%</b>`;
        const track = document.createElement('div');
        track.className = 'network-capacity__track';
        const fill = document.createElement('i');
        fill.style.width = `${usedPercent}%`;
        fill.style.background = usedPercent >= 90 ? '#ef4e52' : (usedPercent >= 75 ? '#e88a08' : '#0f67df');
        track.appendChild(fill);
        capacityBox.append(capacityLabel, track);
        const actions = document.createElement('div');
        actions.className = 'network-asset-actions';
        const ports = document.createElement('a');
        ports.href = odf.ports_url;
        ports.textContent = 'Ver puertos';
        const asset = document.createElement('button');
        asset.type = 'button';
        asset.textContent = 'Ficha 360°';
        asset.addEventListener('click', () => openAsset(odf.detail_url));
        actions.append(ports, asset);
        inspectorDetail.replaceChildren(title, status, grid, capacityBox, actions);
    }

    function currentParams(includePage = true) {
        const params = new URLSearchParams();
        [
            ['q', elements.query.value.trim()],
            ['site', elements.site.value],
            ['sala', elements.room.value],
            ['rack', elements.rack.value],
            ['estado', elements.status.value],
            ['capacidad', elements.capacity.value],
        ].forEach(([name, value]) => { if (value) params.set(name, value); });
        params.set('page_size', elements.pageSize.value);
        if (includePage) params.set('page', String(page));
        return params;
    }

    function updateExportLink() {
        const url = new URL(root.dataset.exportUrl, window.location.origin);
        url.search = currentParams(false).toString();
        elements.exportLink.href = url.toString();
    }

    function td(value, className) {
        const cell = document.createElement('td');
        if (className) cell.className = className;
        cell.textContent = value ?? '—';
        return cell;
    }

    function iconButton(label, path, handler, className = '') {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `odf-icon-action ${className}`.trim();
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
        const normalized = String(value || '').toLocaleLowerCase('es');
        let modifier = '';
        if (normalized.includes('mantenimiento')) modifier = 'is-warning';
        else if (normalized.includes('fuera') || normalized.includes('inactivo')) modifier = 'is-danger';
        else if (!normalized.includes('activo') && !normalized.includes('operativo')) modifier = 'is-neutral';
        badge.className = `odf-status ${modifier}`.trim();
        badge.textContent = value || 'Sin estado';
        return badge;
    }

    function openAsset(url) {
        if (window.FiberGenius?.openAsset360) window.FiberGenius.openAsset360(url);
        else window.location.assign(url);
    }

    function renderRows(rows) {
        elements.body.replaceChildren();
        if (!rows.length) {
            const row = document.createElement('tr');
            const cell = td('No se encontraron ODF con los filtros seleccionados.', 'odf-table__empty');
            cell.colSpan = 11;
            row.appendChild(cell);
            elements.body.appendChild(row);
            return;
        }
        rows.forEach((odf) => {
            const row = document.createElement('tr');
            row.dataset.odfId = String(odf.id);
            row.tabIndex = 0;
            row.setAttribute('aria-label', `Ver detalle de ${odf.odf}`);
            row.addEventListener('click', () => selectOdf(odf, row));
            row.addEventListener('keydown', event => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    selectOdf(odf, row);
                }
            });
            [odf.site, odf.sala, odf.rack, odf.odf].forEach(value => row.appendChild(td(value)));
            [odf.capacidad, odf.ocupados, odf.libres, odf.reservados].forEach(value => row.appendChild(td(numberFormat.format(value || 0), 'odf-number')));
            row.appendChild(td(odf.conector));
            const stateCell = document.createElement('td');
            stateCell.appendChild(statusBadge(odf.estado));
            row.appendChild(stateCell);

            const actionsCell = document.createElement('td');
            const actions = document.createElement('div');
            actions.className = 'odf-row-actions';
            actions.appendChild(iconButton(
                `Abrir ficha 360° de ${odf.odf}`,
                '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/>',
                () => openAsset(odf.detail_url),
            ));
            if (root.dataset.canEdit === 'true') {
                actions.appendChild(iconButton(
                    `Editar ${odf.odf}`,
                    '<path d="m4 20 4-.8L19 8.2a2 2 0 0 0-3-3L5 16.2 4 20Z"/><path d="m14.5 6.5 3 3"/>',
                    () => openEditor(odf),
                ));
            }
            if (root.dataset.canDelete === 'true') {
                actions.appendChild(iconButton(
                    `Eliminar ${odf.odf}`,
                    '<path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5"/>',
                    () => deleteOdf(odf),
                    'is-delete',
                ));
            }
            actionsCell.appendChild(actions);
            row.appendChild(actionsCell);
            elements.body.appendChild(row);
            if (pendingOdfId && Number(odf.id) === pendingOdfId) {
                selectOdf(odf, row);
                pendingOdfId = null;
            }
        });
    }

    function updateStats(summary) {
        const values = {
            total: summary?.total || 0,
            capacity: summary?.capacidad || 0,
            used: summary?.ocupados || 0,
            free: summary?.libres || 0,
            reserved: summary?.reservados || 0,
            sites: summary?.sites || 0,
        };
        Object.entries(values).forEach(([key, value]) => {
            stats[key].textContent = numberFormat.format(value);
        });
        stateSummary(summary);
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
                ellipsis.className = 'odf-page-ellipsis';
                ellipsis.textContent = value;
                elements.pagination.appendChild(ellipsis);
                return;
            }
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `odf-page-number ${value === pagination.page ? 'is-current' : ''}`;
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
        elements.message.textContent = 'Consultando inventario ODF…';
        elements.previous.disabled = true;
        elements.next.disabled = true;
        updateExportLink();
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
            elements.message.textContent = 'No se pudo cargar el inventario ODF. Intenta nuevamente.';
            elements.body.replaceChildren();
            elements.pageInfo.textContent = 'Consulta no disponible';
            updateStats({});
        }
    }

    function editorValue(id) { return document.getElementById(id); }

    function openEditor(odf = null) {
        if (!elements.editor || !elements.editorForm) return;
        elements.editorForm.reset();
        editorValue('odf-editor-id').value = odf?.id || '';
        editorValue('odf-editor-title').textContent = odf ? 'Editar ODF' : 'Nuevo ODF / Rack';
        editorValue('odf-editor-mode').textContent = odf
            ? `Actualiza la ubicación y configuración de ${odf.odf}.`
            : 'Registra el equipo y su ubicación física.';
        editorValue('odf-editor-submit').textContent = odf ? 'Guardar cambios' : 'Crear ODF';
        editorValue('odf-editor-site').value = odf?.site === '—' ? '' : (odf?.site || '');
        editorValue('odf-editor-room').value = odf?.sala === '—' ? '' : (odf?.sala || '');
        editorValue('odf-editor-rack').value = odf?.rack === '—' ? '' : (odf?.rack || '');
        editorValue('odf-editor-name').value = odf?.odf || '';
        editorValue('odf-editor-capacity').value = odf?.capacidad ?? 0;
        editorValue('odf-editor-connector').value = odf?.conector === '—' ? '' : (odf?.conector || '');
        editorValue('odf-editor-status').value = odf?.estado === 'Sin estado' ? 'Operativo' : (odf?.estado || 'Operativo');
        editorValue('odf-editor-message').textContent = '';
        editorValue('odf-editor-message').classList.remove('is-success');
        elements.editor.showModal();
        editorValue('odf-editor-site').focus();
    }

    function closeEditor() {
        if (elements.editor?.open) elements.editor.close();
    }

    async function saveEditor(event) {
        event.preventDefault();
        if (!elements.editorForm.reportValidity()) return;
        const id = editorValue('odf-editor-id').value;
        const payload = {
            id,
            hub_site: editorValue('odf-editor-site').value.trim(),
            sala: editorValue('odf-editor-room').value.trim(),
            rack: editorValue('odf-editor-rack').value.trim(),
            odf: editorValue('odf-editor-name').value.trim(),
            capacidad_puertos: editorValue('odf-editor-capacity').value,
            tipo_conector: editorValue('odf-editor-connector').value.trim(),
            estado: editorValue('odf-editor-status').value.trim() || 'Operativo',
        };
        const message = editorValue('odf-editor-message');
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

    async function deleteOdf(odf) {
        const confirmed = window.confirm(`¿Eliminar ${odf.odf}? También se eliminarán sus puertos asociados. Esta acción no se puede deshacer.`);
        if (!confirmed) return;
        const csrf = elements.editorForm?.querySelector('[name="csrfmiddlewaretoken"]')?.value;
        try {
            const response = await fetch(root.dataset.deleteUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
                body: JSON.stringify({ id: odf.id }),
            });
            const result = await response.json();
            if (!response.ok || result.status !== 'success') throw new Error(result.message || 'No se pudo eliminar');
            elements.message.classList.remove('is-error');
            elements.message.textContent = result.message;
            load();
        } catch (error) {
            elements.message.classList.add('is-error');
            elements.message.textContent = error.message;
        }
    }

    function exportFilename(response) {
        const disposition = response.headers.get('Content-Disposition') || '';
        return disposition.match(/filename="?([^";]+)"?/i)?.[1] || 'FiberGenius_odfs.xlsx';
    }

    elements.exportLink.addEventListener('click', async event => {
        event.preventDefault();
        if (elements.exportLink.dataset.busy === 'true') return;
        const original = elements.exportLink.innerHTML;
        elements.exportLink.dataset.busy = 'true';
        elements.exportLink.setAttribute('aria-disabled', 'true');
        elements.exportLink.textContent = 'Preparando Excel…';
        elements.exportStatus.className = 'odf-export-status';
        elements.exportStatus.textContent = 'Generando archivo…';
        try {
            const response = await fetch(elements.exportLink.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
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
            const fallback = document.createElement('a');
            fallback.href = exportObjectUrl;
            fallback.download = filename;
            fallback.textContent = 'Descargar nuevamente';
            elements.exportStatus.className = 'odf-export-status is-success';
            elements.exportStatus.replaceChildren(document.createTextNode(`Archivo preparado: ${filename}. `), fallback);
        } catch (error) {
            elements.exportStatus.className = 'odf-export-status is-error';
            elements.exportStatus.textContent = 'No se pudo descargar el archivo. Intenta nuevamente.';
        } finally {
            elements.exportLink.dataset.busy = 'false';
            elements.exportLink.removeAttribute('aria-disabled');
            elements.exportLink.innerHTML = original;
        }
    });

    elements.form.addEventListener('submit', event => { event.preventDefault(); page = 1; load(); });
    elements.query.addEventListener('input', () => {
        window.clearTimeout(debounceTimer);
        debounceTimer = window.setTimeout(() => { page = 1; load(); }, 350);
    });
    [elements.site, elements.room, elements.rack, elements.status, elements.pageSize]
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
    elements.newOdf?.addEventListener('click', () => openEditor());
    elements.editorForm?.addEventListener('submit', saveEditor);
    document.getElementById('odf-editor-close')?.addEventListener('click', closeEditor);
    document.getElementById('odf-editor-cancel')?.addEventListener('click', closeEditor);
    inspectorToggle?.addEventListener('click', () => {
        setInspectorCollapsed(!inspector.classList.contains('is-collapsed'));
    });

    const incoming = new URLSearchParams(window.location.search);
    elements.query.value = incoming.get('q') || '';
    elements.site.value = incoming.get('site') || '';
    elements.room.value = incoming.get('sala') || '';
    elements.rack.value = incoming.get('rack') || '';
    elements.status.value = incoming.get('estado') || '';
    elements.capacity.value = incoming.get('capacidad') || '';
    if (['10', '25', '50', '100', '200'].includes(incoming.get('page_size'))) elements.pageSize.value = incoming.get('page_size');
    setInspectorCollapsed(window.innerWidth <= 1180);
    load();
})();
