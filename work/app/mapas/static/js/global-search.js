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

    function renderItems(items, container, itemClass) {
        (items || []).forEach((item) => {
            const wrapper = element('div', itemClass);
            wrapper.appendChild(element('span', `${itemClass}__label`, item.label));
            wrapper.appendChild(element('span', `${itemClass}__value`, item.value));
            container.appendChild(wrapper);
        });
    }

    function renderAsset(asset) {
        title.textContent = asset.title || 'Detalle del activo';
        subtitle.textContent = asset.subtitle || '';
        content.replaceChildren();

        const status = element('span', `status-badge asset-status ${badgeClass(asset.status)}`, asset.status || 'Sin estado');
        content.appendChild(status);

        if (asset.chain && asset.chain.length) {
            const chain = element('div', 'asset-chain');
            chain.setAttribute('aria-label', 'Recorrido físico del activo');
            renderItems(asset.chain, chain, 'asset-chain__item');
            content.appendChild(chain);
        }

        if (asset.summary && asset.summary.length) {
            const summary = element('div', 'asset-summary');
            renderItems(asset.summary, summary, 'asset-summary__item');
            content.appendChild(summary);
        }

        (asset.sections || []).forEach((section) => {
            const sectionNode = element('section', 'asset-section');
            sectionNode.appendChild(element('h3', '', section.title));
            const list = element('dl');
            (section.items || []).forEach((item) => {
                const row = element('div', 'asset-section__row');
                row.appendChild(element('dt', '', item.label));
                row.appendChild(element('dd', '', item.value));
                list.appendChild(row);
            });
            sectionNode.appendChild(list);
            content.appendChild(sectionNode);
        });

        if (asset.actions && asset.actions.length) {
            const actions = element('div', 'asset-actions');
            asset.actions.forEach((action, index) => {
                const link = element('a', index === 0 ? 'btn btn--primary' : 'btn btn--secondary', action.label);
                link.href = action.url;
                actions.appendChild(link);
            });
            content.appendChild(actions);
        }
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
