(function () {
    'use strict';

    const FG = window.FiberGenius = window.FiberGenius || {};
    const search = document.querySelector('.global-search');
    const input = document.getElementById('global-search-input');
    const results = document.getElementById('global-search-results');
    const drawer = document.getElementById('asset-drawer');
    const backdrop = document.getElementById('asset-drawer-backdrop');
    const closeButton = document.getElementById('asset-drawer-close');
    const content = document.getElementById('asset-drawer-content');
    const title = document.getElementById('asset-drawer-title');
    const subtitle = document.getElementById('asset-drawer-subtitle');

    let debounceTimer = null;
    let controller = null;
    let selectedIndex = -1;
    let resultData = [];
    let previousFocus = null;

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    function setResultsOpen(open) {
        if (!results || !input) return;
        results.hidden = !open;
        input.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    function resultState(message) {
        results.replaceChildren(element('div', 'search-result-state', message));
        setResultsOpen(true);
    }

    function kindLabel(type) {
        return ({
            troncal: 'TR', odf: 'ODF', puerto: 'P', fibra: 'FO', site: 'S', elemento: 'PE',
        })[type] || 'FG';
    }

    function renderResults(data) {
        resultData = data;
        selectedIndex = -1;
        results.replaceChildren();
        if (!data.length) {
            resultState('No se encontraron activos con ese criterio.');
            return;
        }
        data.forEach((item, index) => {
            const button = element('button', 'search-result');
            button.type = 'button';
            button.setAttribute('role', 'option');
            button.setAttribute('aria-selected', 'false');
            button.dataset.index = String(index);
            button.appendChild(element('span', 'search-result__kind', kindLabel(item.type)));
            const copy = element('span');
            copy.appendChild(element('span', 'search-result__title', item.title));
            copy.appendChild(element('span', 'search-result__subtitle', item.subtitle));
            button.appendChild(copy);
            button.addEventListener('click', () => openAsset(item.detail_url));
            results.appendChild(button);
        });
        setResultsOpen(true);
    }

    async function runSearch() {
        const query = input.value.trim();
        if (query.length < 2) {
            resultData = [];
            setResultsOpen(false);
            return;
        }
        if (controller) controller.abort();
        controller = new AbortController();
        resultState('Buscando en el inventario…');
        try {
            const url = new URL(search.dataset.searchUrl, window.location.origin);
            url.searchParams.set('q', query);
            const response = await fetch(url, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                signal: controller.signal,
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const payload = await response.json();
            renderResults(payload.data || []);
        } catch (error) {
            if (error.name !== 'AbortError') resultState('No fue posible completar la búsqueda.');
        }
    }

    function updateSelection(nextIndex) {
        const buttons = Array.from(results.querySelectorAll('.search-result'));
        if (!buttons.length) return;
        selectedIndex = (nextIndex + buttons.length) % buttons.length;
        buttons.forEach((button, index) => button.setAttribute('aria-selected', index === selectedIndex ? 'true' : 'false'));
        buttons[selectedIndex].scrollIntoView({ block: 'nearest' });
    }

    function badgeClass(status) {
        const value = (status || '').toLowerCase();
        if (value.includes('ocup') || value.includes('fall') || value.includes('corte')) return 'status-badge--busy';
        if (value.includes('reserv')) return 'status-badge--reserved';
        if (value.includes('libre') || value.includes('activo') || value.includes('registr')) return 'status-badge--free';
        return 'status-badge--neutral';
    }

    function renderItems(items, container, groupClass) {
        (items || []).forEach((item) => {
            const wrapper = element('div', `${groupClass}__item`);
            wrapper.appendChild(element('span', `${groupClass}__label`, item.label));
            wrapper.appendChild(element('span', `${groupClass}__value`, item.value));
            container.appendChild(wrapper);
        });
    }

    function renderActions(asset, container) {
        if (!asset.actions || !asset.actions.length) return;
        const actions = element('div', 'asset-actions');
        asset.actions.forEach((action, index) => {
            const link = element('a', index === 0 ? 'btn btn--primary' : 'btn btn--secondary', action.label);
            link.href = action.url;
            actions.appendChild(link);
        });
        container.appendChild(actions);
    }

    function renderOdfAsset(asset, status) {
        const data = asset.odf_operational || {};
        const capacity = Number(data.capacity) || 0;
        const occupied = Number(data.occupied) || 0;
        const free = Number(data.free) || 0;
        const reserved = Number(data.reserved) || 0;
        const utilization = Number(data.utilization) || 0;
        const percentage = value => capacity ? Math.max(0, Math.min(100, (Number(value) || 0) * 100 / capacity)) : 0;

        const overview = element('section', 'asset-odf-overview');
        const identity = element('div', 'asset-odf-identity');
        identity.appendChild(element('span', 'asset-odf-identity__icon', 'ODF'));
        const identityCopy = element('div', 'asset-odf-identity__copy');
        identityCopy.appendChild(element('span', 'asset-odf-identity__label', 'Distribuidor óptico'));
        identityCopy.appendChild(status);
        identity.appendChild(identityCopy);
        overview.appendChild(identity);

        const hierarchy = element('div', 'asset-odf-hierarchy');
        (asset.chain || []).slice(0, 3).forEach((item, index) => {
            if (index) hierarchy.appendChild(element('span', 'asset-odf-hierarchy__separator', '›'));
            const node = element('span', 'asset-odf-hierarchy__node');
            node.appendChild(element('small', '', item.label));
            node.appendChild(element('strong', '', item.value));
            hierarchy.appendChild(node);
        });
        overview.appendChild(hierarchy);
        content.appendChild(overview);

        const capacityCard = element('section', 'asset-odf-capacity');
        const capacityHeader = element('header', 'asset-odf-section-heading');
        const capacityTitle = element('div');
        capacityTitle.appendChild(element('h3', '', 'Ocupación de puertos'));
        capacityTitle.appendChild(element('span', '', `${occupied} de ${capacity} puertos en uso`));
        capacityHeader.appendChild(capacityTitle);
        capacityHeader.appendChild(element('strong', 'asset-odf-capacity__percent', `${utilization.toLocaleString('es-PE')}%`));
        capacityCard.appendChild(capacityHeader);

        const track = element('div', 'asset-odf-capacity__track');
        [
            ['is-used', occupied],
            ['is-free', free],
            ['is-reserved', reserved],
        ].forEach(([className, value]) => {
            const segment = element('span', className);
            segment.style.width = `${percentage(value)}%`;
            track.appendChild(segment);
        });
        capacityCard.appendChild(track);

        const stats = element('div', 'asset-odf-capacity__stats');
        [
            ['is-used', 'Ocupados', occupied],
            ['is-free', 'Libres', free],
            ['is-reserved', 'Reservados', reserved],
        ].forEach(([className, label, value]) => {
            const item = element('div', `asset-odf-stat ${className}`);
            item.appendChild(element('i'));
            const copy = element('span');
            copy.appendChild(element('small', '', label));
            copy.appendChild(element('strong', '', String(value)));
            item.appendChild(copy);
            stats.appendChild(item);
        });
        capacityCard.appendChild(stats);
        content.appendChild(capacityCard);

        const portsCard = element('section', 'asset-odf-ports');
        const portsHeader = element('header', 'asset-odf-section-heading');
        const portsTitle = element('div');
        portsTitle.appendChild(element('h3', '', 'Vista de puertos'));
        portsTitle.appendChild(element('span', '', `${(data.ports || []).length} registrados de ${capacity}`));
        portsHeader.appendChild(portsTitle);
        portsCard.appendChild(portsHeader);
        const portsGrid = element('div', 'asset-odf-ports__grid');
        (data.ports || []).forEach(port => {
            const state = String(port.status || '').toLowerCase();
            const stateClass = state.includes('ocup') ? 'is-used' : state.includes('reserv') ? 'is-reserved' : 'is-free';
            const button = element('button', `asset-odf-port ${stateClass}`, port.label);
            button.type = 'button';
            button.title = `Puerto ${port.label}: ${port.status}`;
            button.setAttribute('aria-label', button.title);
            if (port.detail_url) button.addEventListener('click', () => openAsset(port.detail_url));
            portsGrid.appendChild(button);
        });
        if (!(data.ports || []).length) {
            portsGrid.appendChild(element('p', 'asset-odf-ports__empty', 'Aún no hay puertos individuales registrados.'));
        }
        portsCard.appendChild(portsGrid);
        const legend = element('div', 'asset-odf-ports__legend');
        [['is-used', 'Ocupado'], ['is-free', 'Libre'], ['is-reserved', 'Reservado']].forEach(([className, label]) => {
            const item = element('span', className);
            item.appendChild(element('i'));
            item.appendChild(document.createTextNode(label));
            legend.appendChild(item);
        });
        portsCard.appendChild(legend);
        content.appendChild(portsCard);

        const technical = element('section', 'asset-odf-technical');
        technical.appendChild(element('h3', '', 'Datos técnicos'));
        const technicalGrid = element('div', 'asset-odf-technical__grid');
        [['Tipo de conector', data.connector], ['Capacidad instalada', `${capacity} puertos`]].forEach(([label, value]) => {
            const item = element('div');
            item.appendChild(element('span', '', label));
            item.appendChild(element('strong', '', value || '—'));
            technicalGrid.appendChild(item);
        });
        technical.appendChild(technicalGrid);
        const source = element('div', 'asset-odf-source');
        source.appendChild(element('span', 'asset-odf-source__icon', 'i'));
        const sourceCopy = element('div');
        sourceCopy.appendChild(element('strong', '', 'Procedencia de la capacidad'));
        sourceCopy.appendChild(element('p', '', data.capacity_source || 'Sin procedencia documentada'));
        source.appendChild(sourceCopy);
        technical.appendChild(source);
        if (data.notes) {
            const notes = element('div', 'asset-odf-notes');
            notes.appendChild(element('span', '', 'Observaciones'));
            notes.appendChild(element('p', '', data.notes));
            technical.appendChild(notes);
        }
        if (data.related_trunks && data.related_trunks.length) {
            const related = element('div', 'asset-odf-related');
            related.appendChild(element('span', '', 'Troncales relacionadas'));
            related.appendChild(element('strong', '', data.related_trunks.join(' · ')));
            technical.appendChild(related);
        }
        content.appendChild(technical);
        renderActions(asset, content);
    }

    function renderPortAsset(asset, status) {
        const data = asset.port_operational || {};
        const fiber = data.fiber || null;
        const overview = element('section', 'asset-port-overview');
        const identity = element('div', 'asset-port-identity');
        identity.appendChild(element('span', 'asset-port-identity__icon', `P${data.port || ''}`));
        const identityCopy = element('div', 'asset-port-identity__copy');
        identityCopy.appendChild(element('span', 'asset-port-identity__label', 'Puerto ODF'));
        identityCopy.appendChild(status);
        identityCopy.appendChild(element(
            'small',
            'asset-port-identity__hint',
            fiber ? 'Conexión respaldada por terminación oficial' : 'Sin terminación óptica asociada',
        ));
        identity.appendChild(identityCopy);
        overview.appendChild(identity);

        const hierarchy = element('div', 'asset-port-hierarchy');
        (asset.chain || []).slice(0, 4).forEach(item => {
            const node = element('div', 'asset-port-hierarchy__node');
            node.appendChild(element('small', '', item.label));
            node.appendChild(element('strong', '', item.value || '—'));
            hierarchy.appendChild(node);
        });
        overview.appendChild(hierarchy);
        content.appendChild(overview);

        const connectionState = fiber ? 'is-connected' : data.is_reserved ? 'is-reserved' : data.is_free ? 'is-free' : 'is-warning';
        const connection = element('section', `asset-port-connection ${connectionState}`);
        const heading = element('header', 'asset-port-section-heading');
        const headingCopy = element('div');
        headingCopy.appendChild(element('h3', '', 'Conexión óptica'));
        headingCopy.appendChild(element('span', '', fiber ? 'Terminación registrada en el inventario' : 'Disponibilidad del puerto'));
        heading.appendChild(headingCopy);
        connection.appendChild(heading);

        const path = element('div', 'asset-port-path');
        const portNode = element('div', 'asset-port-path__node is-port');
        portNode.appendChild(element('span', '', 'Puerto'));
        portNode.appendChild(element('strong', '', `${data.odf || 'ODF'} · ${data.port || '—'}`));
        portNode.appendChild(element('small', '', data.tray ? `Bandeja ${data.tray}` : 'Sin bandeja informada'));
        path.appendChild(portNode);
        const link = element('div', 'asset-port-path__link');
        link.appendChild(element('span', '', fiber ? 'Conectado' : data.is_reserved ? 'Reservado' : data.is_free ? 'Disponible' : 'Sin vínculo'));
        path.appendChild(link);
        const fiberNode = element('div', `asset-port-path__node ${fiber ? 'is-fiber' : 'is-empty'}`);
        fiberNode.appendChild(element('span', '', fiber ? 'Fibra óptica' : data.is_reserved ? 'Reserva' : 'Fibra óptica'));
        fiberNode.appendChild(element('strong', '', fiber ? fiber.number : data.is_reserved ? 'Puerto apartado' : 'Sin fibra conectada'));
        fiberNode.appendChild(element('small', '', fiber ? (fiber.route || 'Troncal pendiente') : (data.destination || 'Sin destino asignado')));
        path.appendChild(fiberNode);
        connection.appendChild(path);

        const coherence = element('div', 'asset-port-coherence');
        if (fiber) {
            coherence.classList.add('is-ok');
            coherence.appendChild(element('strong', '', `${fiber.endpoint || 'Extremo'} confirmado`));
            coherence.appendChild(element('span', '', fiber.service ? `Servicio: ${fiber.service}` : 'Conexión oficial identificada'));
        } else if (String(data.state || '').toLowerCase().includes('ocup')) {
            coherence.classList.add('is-warning');
            coherence.appendChild(element('strong', '', 'Ocupado sin terminación oficial'));
            coherence.appendChild(element('span', '', 'La conexión debe revisarse antes de considerar el puerto documentado.'));
        } else if (data.is_reserved) {
            coherence.classList.add('is-reserved');
            coherence.appendChild(element('strong', '', 'Reserva registrada'));
            coherence.appendChild(element('span', '', data.destination || 'Aún no tiene fibra asociada.'));
        } else {
            coherence.classList.add('is-ok');
            coherence.appendChild(element('strong', '', 'Puerto disponible'));
            coherence.appendChild(element('span', '', 'No existe una terminación oficial vinculada.'));
        }
        connection.appendChild(coherence);

        const connectionActions = element('div', 'asset-port-connection__actions');
        if (fiber?.detail_url) {
            const fiberButton = element('button', 'btn btn-primary', 'Ver ficha de la fibra');
            fiberButton.type = 'button';
            fiberButton.addEventListener('click', () => openAsset(fiber.detail_url));
            connectionActions.appendChild(fiberButton);
        }
        if (data.odf_detail_url) {
            const odfButton = element('button', 'btn btn-secondary', 'Volver al ODF');
            odfButton.type = 'button';
            odfButton.addEventListener('click', () => openAsset(data.odf_detail_url));
            connectionActions.appendChild(odfButton);
        }
        if (connectionActions.children.length) connection.appendChild(connectionActions);
        content.appendChild(connection);

        const details = [
            ['Bandeja', data.tray],
            ['Tipo de conector', data.connector],
            ['Patchcord', data.patchcord],
            ['Destino', data.destination],
        ].filter(([, value]) => value !== null && value !== undefined && String(value).trim());
        if (details.length || data.notes) {
            const technical = element('section', 'asset-port-technical');
            technical.appendChild(element('h3', '', 'Datos del puerto'));
            if (details.length) {
                const grid = element('div', 'asset-port-technical__grid');
                details.forEach(([label, value]) => {
                    const item = element('div');
                    item.appendChild(element('span', '', label));
                    item.appendChild(element('strong', '', value));
                    grid.appendChild(item);
                });
                technical.appendChild(grid);
            }
            if (data.notes) {
                const notes = element('div', 'asset-port-notes');
                notes.appendChild(element('span', '', 'Observaciones'));
                notes.appendChild(element('p', '', data.notes));
                technical.appendChild(notes);
            }
            content.appendChild(technical);
        }
        renderActions(asset, content);
    }

    function renderAsset(asset) {
        drawer.dataset.assetType = asset.type || '';
        title.textContent = asset.title || 'Detalle del activo';
        subtitle.textContent = asset.subtitle || '';
        content.replaceChildren();

        const status = element('span', `status-badge asset-status ${badgeClass(asset.status)}`, asset.status || 'Sin estado');
        if (asset.type === 'odf') {
            renderOdfAsset(asset, status);
            return;
        } else if (asset.type === 'puerto') {
            renderPortAsset(asset, status);
            return;
        } else if (asset.type === 'fibra') {
            const overview = element('section', 'asset-overview asset-overview--fiber');
            const icon = element('span', 'asset-overview__icon', 'FO');
            icon.setAttribute('aria-hidden', 'true');
            const copy = element('div', 'asset-overview__copy');
            copy.appendChild(element('span', 'asset-overview__label', 'Estado global de la fibra'));
            copy.appendChild(status);
            copy.appendChild(element('small', 'asset-overview__hint', 'Información consolidada del inventario lógico'));
            overview.append(icon, copy);
            content.appendChild(overview);
        } else {
            content.appendChild(status);
        }

        if (asset.chain && asset.chain.length) {
            const chain = element('div', 'asset-chain');
            if (asset.type === 'fibra') chain.classList.add('asset-chain--fiber');
            chain.setAttribute('aria-label', 'Recorrido físico del activo');
            renderItems(asset.chain, chain, 'asset-chain');
            content.appendChild(chain);
        }

        if (asset.summary && asset.summary.length) {
            const summary = element('div', 'asset-summary');
            if (asset.type === 'fibra') summary.classList.add('asset-summary--fiber');
            renderItems(asset.summary, summary, 'asset-summary');
            content.appendChild(summary);
        }

        (asset.sections || []).forEach((section) => {
            const visibleItems = (section.items || []).filter((item) => {
                const value = item.value == null ? '' : String(item.value).trim();
                return value && value !== '—';
            });
            if (!visibleItems.length) return;

            const sectionNode = element('section', 'asset-section');
            const qualitySection = asset.type === 'fibra' && section.title === 'Calidad y validaciones';
            if (qualitySection) sectionNode.classList.add('asset-section--quality');
            sectionNode.appendChild(element('h3', '', section.title));
            const list = element('dl');
            visibleItems.forEach((item) => {
                const row = element('div', 'asset-section__row');
                if (qualitySection) {
                    const severity = String(item.label || '').toLowerCase();
                    row.classList.add(
                        severity.includes('error') ? 'is-danger'
                            : severity.includes('advert') ? 'is-warning'
                                : 'is-fact',
                    );
                }
                row.appendChild(element('dt', '', item.label));
                row.appendChild(element('dd', '', item.value));
                list.appendChild(row);
            });
            sectionNode.appendChild(list);
            content.appendChild(sectionNode);
        });

        renderActions(asset, content);
    }

    function openDrawer() {
        previousFocus = document.activeElement;
        drawer.classList.add('is-open');
        drawer.setAttribute('aria-hidden', 'false');
        backdrop.hidden = false;
        document.body.style.overflow = 'hidden';
        setResultsOpen(false);
        closeButton.focus();
    }

    function closeDrawer() {
        drawer.classList.remove('is-open');
        drawer.setAttribute('aria-hidden', 'true');
        backdrop.hidden = true;
        document.body.style.overflow = '';
        if (previousFocus && document.contains(previousFocus)) previousFocus.focus();
    }

    async function openAsset(detailUrl) {
        if (!detailUrl) return;
        openDrawer();
        delete drawer.dataset.assetType;
        title.textContent = 'Cargando activo…';
        subtitle.textContent = '';
        content.replaceChildren(element('div', 'asset-loading', 'Construyendo la vista operativa 360°…'));
        try {
            const response = await fetch(detailUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const payload = await response.json();
            renderAsset(payload.data);
        } catch (error) {
            title.textContent = 'No se pudo abrir el activo';
            content.replaceChildren(element('div', 'asset-loading', 'La ficha no está disponible o no tienes permiso para consultarla.'));
        }
    }

    FG.openAsset360 = openAsset;

    if (input && results && search) {
        input.addEventListener('input', () => {
            window.clearTimeout(debounceTimer);
            debounceTimer = window.setTimeout(runSearch, 220);
        });
        input.addEventListener('focus', () => {
            if (input.value.trim().length >= 2) runSearch();
        });
        input.addEventListener('keydown', (event) => {
            if (event.key === 'ArrowDown') {
                event.preventDefault();
                updateSelection(selectedIndex + 1);
            } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                updateSelection(selectedIndex - 1);
            } else if (event.key === 'Enter' && selectedIndex >= 0 && resultData[selectedIndex]) {
                event.preventDefault();
                openAsset(resultData[selectedIndex].detail_url);
            } else if (event.key === 'Escape') {
                setResultsOpen(false);
            }
        });

        document.addEventListener('click', (event) => {
            if (!search.contains(event.target)) setResultsOpen(false);
        });
        document.addEventListener('keydown', (event) => {
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
                event.preventDefault();
                input.focus();
                input.select();
            }
        });
    }

    if (closeButton) closeButton.addEventListener('click', closeDrawer);
    if (backdrop) backdrop.addEventListener('click', closeDrawer);
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && drawer && drawer.classList.contains('is-open')) {
            closeDrawer();
        }
        if (event.key === 'Tab' && drawer && drawer.classList.contains('is-open')) {
            const items = Array.from(drawer.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'));
            if (!items.length) return;
            const first = items[0];
            const last = items[items.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault(); last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault(); first.focus();
            }
        }
    });
})();
