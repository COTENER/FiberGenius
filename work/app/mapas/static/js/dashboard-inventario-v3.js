(function () {
    'use strict';

    let capacityCharts = [];
    let dashboardMap = null;
    let dashboardTileLayer = null;
    let dashboardMapTarget = null;

    function readJSON(id, fallback) {
        const node = document.getElementById(id);
        if (!node) return fallback;
        try {
            return JSON.parse(node.textContent);
        } catch (_error) {
            return fallback;
        }
    }

    function chartFont() {
        return getComputedStyle(document.body).fontFamily;
    }

    function formatDashboardCounts() {
        const formatter = new Intl.NumberFormat('en-US', {
            maximumFractionDigits: 0,
        });
        document.querySelectorAll('.inv-kpi__body > strong').forEach((node) => {
            node.textContent = node.textContent.replace(/\d+/g, (value) =>
                formatter.format(Number(value))
            );
        });
    }

    function chartColors() {
        const dark = document.documentElement.getAttribute('data-theme') === 'dark';
        return {
            dark,
            primary: dark ? '#f1f5f9' : '#111827',
            secondary: dark ? '#94a3b8' : '#64748b',
            text: dark ? '#cbd5e1' : '#68778d',
            border: dark ? '#152238' : '#ffffff',
        };
    }

    const capacityCenterText = {
        id: 'capacityCenterText',
        afterDraw(chart) {
            const arc = chart.getDatasetMeta(0)?.data?.[0];
            if (!arc) return;
            const rawTotal = Number(chart.canvas.dataset.total || 0);
            const total = Number.isFinite(rawTotal) ? rawTotal : 0;
            const colors = chartColors();
            const ctx = chart.ctx;

            ctx.save();
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillStyle = colors.primary;
            ctx.font = `800 17px ${chartFont()}`;
            ctx.fillText(new Intl.NumberFormat('es-PE').format(total), arc.x, arc.y - 5);
            ctx.fillStyle = colors.secondary;
            ctx.font = `600 10px ${chartFont()}`;
            ctx.fillText('Total', arc.x, arc.y + 12);
            ctx.restore();
        },
    };

    function createCapacityDonut(canvas) {
        if (!canvas || !window.Chart) return;

        const free = Number(canvas.dataset.free || 0);
        const used = Number(canvas.dataset.used || 0);
        const reserved = Number(canvas.dataset.reserved || 0);
        const total = free + used + reserved;
        if (!total) return;

        const colors = chartColors();
        const chart = new Chart(canvas, {
            type: 'doughnut',
            data: {
                labels: ['Disponibles', 'Ocupados', 'Reservados'],
                datasets: [{
                    data: [free, used, reserved],
                    backgroundColor: ['#20b276', '#2275e6', '#f19a27'],
                    borderColor: colors.border,
                    borderWidth: 3,
                    hoverOffset: 4,
                }],
            },
            plugins: [capacityCenterText],
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 350 },
                cutout: '69%',
                plugins: {
                    legend: {
                        display: true,
                        position: 'right',
                        labels: {
                            color: colors.text,
                            usePointStyle: true,
                            pointStyle: 'circle',
                            boxWidth: 7,
                            padding: 13,
                            font: { family: chartFont(), size: 9, weight: '600' },
                            generateLabels(chartInstance) {
                                const original = Chart.overrides.doughnut.plugins.legend.labels.generateLabels(chartInstance);
                                return original.map((item, index) => {
                                    const value = chartInstance.data.datasets[0].data[index];
                                    const percentage = total ? (value / total * 100).toFixed(1) : '0.0';
                                    item.text = `${item.text} ${new Intl.NumberFormat('es-PE').format(value)} · ${percentage}%`;
                                    return item;
                                });
                            },
                        },
                    },
                    tooltip: {
                        backgroundColor: colors.dark ? '#0d1728' : '#10213d',
                        titleFont: { family: chartFont(), size: 11 },
                        bodyFont: { family: chartFont(), size: 11 },
                        padding: 10,
                        cornerRadius: 7,
                        callbacks: {
                            label(context) {
                                const value = Number(context.parsed || 0);
                                const percentage = total ? (value / total * 100).toFixed(1) : '0.0';
                                return ` ${context.label}: ${new Intl.NumberFormat('es-PE').format(value)} (${percentage}%)`;
                            },
                        },
                    },
                },
            },
        });
        capacityCharts.push(chart);
    }

    function renderCharts() {
        if (!window.Chart) return;
        capacityCharts.forEach(chart => chart.destroy());
        capacityCharts = [];
        Chart.defaults.font.family = chartFont();
        Chart.defaults.color = chartColors().text;
        createCapacityDonut(document.getElementById('portStatusChartV3'));
        createCapacityDonut(document.getElementById('fiberStatusChartV3'));
    }

    function normalize(value) {
        return String(value || '')
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .trim()
            .toUpperCase();
    }

    function traceColor(value) {
        const type = normalize(value);
        if (type === 'AEREO') return '#2275e6';
        if (type === 'SOTERRADO') return '#f0822c';
        if (type === 'HIBRIDO') return '#8757df';
        return '#8794a7';
    }

    function initDashboardCustomization() {
        const root = document.querySelector('.inv-dashboard');
        const grid = document.getElementById('dashboard-customizable-grid');
        const editButton = document.getElementById('dashboard-layout-edit');
        const toolbar = document.getElementById('dashboard-layout-toolbar');
        const saveButton = document.getElementById('dashboard-layout-save');
        const cancelButton = document.getElementById('dashboard-layout-cancel');
        const resetButton = document.getElementById('dashboard-layout-reset');
        if (!root || !grid || !editButton || !toolbar || !saveButton || !cancelButton || !resetButton) return;

        const widgets = Array.from(grid.querySelectorAll('[data-dashboard-widget]'));
        const allowedSizes = ['normal', 'wide', 'full'];
        const defaultLayout = {
            order: widgets.map(widget => widget.dataset.dashboardWidget),
            sizes: Object.fromEntries(widgets.map(widget => [widget.dataset.dashboardWidget, 'normal'])),
        };
        const userKey = root.dataset.dashboardUser || 'anonymous';
        const storageKey = `fiber-genius.dashboard-layout.v1.${userKey}`;
        const mobileQuery = window.matchMedia('(max-width: 720px)');
        let editSnapshot = null;
        let draggingWidget = null;

        function normalizeLayout(layout) {
            const validIds = new Set(defaultLayout.order);
            const requestedOrder = Array.isArray(layout?.order) ? layout.order.filter(id => validIds.has(id)) : [];
            const order = [...new Set([...requestedOrder, ...defaultLayout.order])];
            const sizes = {};
            order.forEach(id => {
                const requestedSize = layout?.sizes?.[id];
                sizes[id] = allowedSizes.includes(requestedSize) ? requestedSize : 'normal';
            });
            return { order, sizes };
        }

        function readSavedLayout() {
            try {
                const stored = window.localStorage.getItem(storageKey);
                return stored ? normalizeLayout(JSON.parse(stored)) : defaultLayout;
            } catch (_error) {
                return defaultLayout;
            }
        }

        function captureLayout() {
            const currentWidgets = Array.from(grid.querySelectorAll('[data-dashboard-widget]'));
            return normalizeLayout({
                order: currentWidgets.map(widget => widget.dataset.dashboardWidget),
                sizes: Object.fromEntries(currentWidgets.map(widget => [
                    widget.dataset.dashboardWidget,
                    widget.dataset.dashboardSize || 'normal',
                ])),
            });
        }

        function scheduleChartResize() {
            window.requestAnimationFrame(() => {
                capacityCharts.forEach(chart => chart.resize());
            });
        }

        function updateControlLabels() {
            const labels = { normal: 'Normal', wide: 'Ancho', full: 'Completo' };
            Array.from(grid.querySelectorAll('[data-dashboard-widget]')).forEach(widget => {
                const sizeButton = widget.querySelector('[data-layout-action="size"]');
                if (!sizeButton) return;
                const size = widget.dataset.dashboardSize || 'normal';
                sizeButton.textContent = labels[size];
                sizeButton.title = `Tamaño actual: ${labels[size]}`;
                sizeButton.setAttribute('aria-label', `Cambiar tamaño. Tamaño actual: ${labels[size]}`);
            });
        }

        function applyLayout(layout) {
            const normalized = normalizeLayout(layout);
            normalized.order.forEach(id => {
                const widget = grid.querySelector(`[data-dashboard-widget="${id}"]`);
                if (widget) grid.appendChild(widget);
            });
            widgets.forEach(widget => {
                widget.dataset.dashboardSize = normalized.sizes[widget.dataset.dashboardWidget] || 'normal';
            });
            updateControlLabels();
            scheduleChartResize();
        }

        function persistLayout(layout) {
            try {
                window.localStorage.setItem(storageKey, JSON.stringify(normalizeLayout(layout)));
            } catch (_error) {
                // La personalización sigue funcionando durante la sesión aunque el navegador bloquee el almacenamiento.
            }
        }

        function createWidgetControls(widget) {
            const controls = document.createElement('div');
            controls.className = 'inv-widget-controls';

            const handle = document.createElement('span');
            handle.className = 'inv-widget-controls__handle';
            handle.title = 'Arrastrar panel';
            handle.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="8" cy="6" r="1.5"/><circle cx="16" cy="6" r="1.5"/><circle cx="8" cy="12" r="1.5"/><circle cx="16" cy="12" r="1.5"/><circle cx="8" cy="18" r="1.5"/><circle cx="16" cy="18" r="1.5"/></svg><span>Mover panel</span>';
            controls.appendChild(handle);

            [
                ['left', '←', 'Mover panel a la izquierda'],
                ['right', '→', 'Mover panel a la derecha'],
                ['size', 'Normal', 'Cambiar tamaño del panel'],
            ].forEach(([action, text, label]) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.dataset.layoutAction = action;
                button.textContent = text;
                button.title = label;
                button.setAttribute('aria-label', label);
                controls.appendChild(button);
            });

            widget.insertBefore(controls, widget.firstChild);
        }

        function setEditing(editing) {
            root.classList.toggle('is-layout-editing', editing);
            toolbar.hidden = !editing;
            editButton.setAttribute('aria-pressed', editing ? 'true' : 'false');
            editButton.lastChild.textContent = editing ? ' Editando' : ' Personalizar';
            widgets.forEach(widget => {
                widget.draggable = editing;
            });
            if (!editing) {
                draggingWidget = null;
                widgets.forEach(widget => widget.classList.remove('is-dragging', 'is-drag-over'));
            }
            scheduleChartResize();
        }

        widgets.forEach(widget => createWidgetControls(widget));
        applyLayout(mobileQuery.matches ? defaultLayout : readSavedLayout());
        setEditing(false);

        editButton.addEventListener('click', () => {
            if (mobileQuery.matches || root.classList.contains('is-layout-editing')) return;
            editSnapshot = captureLayout();
            setEditing(true);
            toolbar.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });

        saveButton.addEventListener('click', () => {
            persistLayout(captureLayout());
            editSnapshot = null;
            setEditing(false);
        });

        cancelButton.addEventListener('click', () => {
            if (editSnapshot) applyLayout(editSnapshot);
            editSnapshot = null;
            setEditing(false);
        });

        resetButton.addEventListener('click', () => {
            applyLayout(defaultLayout);
        });

        grid.addEventListener('click', event => {
            if (!root.classList.contains('is-layout-editing')) return;
            const button = event.target.closest('[data-layout-action]');
            const widget = event.target.closest('[data-dashboard-widget]');
            if (!button || !widget) return;

            const action = button.dataset.layoutAction;
            if (action === 'left') {
                const previous = widget.previousElementSibling;
                if (previous) grid.insertBefore(widget, previous);
            } else if (action === 'right') {
                const next = widget.nextElementSibling;
                if (next) grid.insertBefore(next, widget);
            } else if (action === 'size') {
                const currentIndex = allowedSizes.indexOf(widget.dataset.dashboardSize || 'normal');
                widget.dataset.dashboardSize = allowedSizes[(currentIndex + 1) % allowedSizes.length];
                updateControlLabels();
            }
            scheduleChartResize();
        });

        grid.addEventListener('dragstart', event => {
            if (!root.classList.contains('is-layout-editing')) {
                event.preventDefault();
                return;
            }
            draggingWidget = event.target.closest('[data-dashboard-widget]');
            if (!draggingWidget) return;
            draggingWidget.classList.add('is-dragging');
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData('text/plain', draggingWidget.dataset.dashboardWidget);
        });

        grid.addEventListener('dragover', event => {
            if (!draggingWidget) return;
            event.preventDefault();
            const targetWidget = event.target.closest('[data-dashboard-widget]');
            widgets.forEach(widget => widget.classList.remove('is-drag-over'));
            if (!targetWidget || targetWidget === draggingWidget) return;
            targetWidget.classList.add('is-drag-over');
            const bounds = targetWidget.getBoundingClientRect();
            const insertBefore = event.clientX < bounds.left + bounds.width / 2;
            grid.insertBefore(draggingWidget, insertBefore ? targetWidget : targetWidget.nextSibling);
        });

        grid.addEventListener('drop', event => {
            if (!draggingWidget) return;
            event.preventDefault();
            widgets.forEach(widget => widget.classList.remove('is-drag-over'));
            scheduleChartResize();
        });

        grid.addEventListener('dragend', () => {
            widgets.forEach(widget => widget.classList.remove('is-dragging', 'is-drag-over'));
            draggingWidget = null;
        });

        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && root.classList.contains('is-layout-editing')) {
                cancelButton.click();
            }
        });

        mobileQuery.addEventListener('change', event => {
            if (root.classList.contains('is-layout-editing')) {
                if (editSnapshot) applyLayout(editSnapshot);
                editSnapshot = null;
                setEditing(false);
            }
            applyLayout(event.matches ? defaultLayout : readSavedLayout());
        });
    }

    function popupContent(title, rows) {
        const wrapper = document.createElement('div');
        const heading = document.createElement('strong');
        heading.textContent = title;
        wrapper.appendChild(heading);
        rows.filter(Boolean).forEach(row => {
            const line = document.createElement('div');
            line.textContent = row;
            wrapper.appendChild(line);
        });
        return wrapper;
    }

    function siteIcon() {
        return L.divIcon({
            className: 'inv-dashboard-map-icon',
            html: '<span class="inv-dashboard-map-marker"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21h18M6 21V7l6-4 6 4v14M9 10h.01M15 10h.01M9 15h.01M15 15h.01"/></svg></span>',
            iconSize: [28, 28],
            iconAnchor: [14, 14],
        });
    }

    function odfIcon(count) {
        const safeCount = Number.isFinite(Number(count)) ? Number(count) : 0;
        return L.divIcon({
            className: 'inv-dashboard-map-icon',
            html: `<span class="inv-dashboard-map-marker is-odf">${safeCount}</span>`,
            iconSize: [23, 23],
            iconAnchor: [-7, 20],
        });
    }

    function reserveIcon() {
        return L.divIcon({
            className: 'inv-dashboard-map-icon',
            html: '<span class="inv-dashboard-map-marker is-reserve"></span>',
            iconSize: [15, 15],
            iconAnchor: [8, 8],
        });
    }

    function tileUrlForTheme(target) {
        const dark = document.documentElement.getAttribute('data-theme') === 'dark';
        return dark ? target.dataset.tileDark : target.dataset.tileLight;
    }

    function refreshMapTheme() {
        if (!dashboardMap || !dashboardMapTarget || !window.L) return;
        if (dashboardTileLayer) dashboardMap.removeLayer(dashboardTileLayer);
        dashboardTileLayer = L.tileLayer(tileUrlForTheme(dashboardMapTarget), {
            attribution: dashboardMapTarget.dataset.attribution || '&copy; OpenStreetMap contributors',
            maxZoom: 20,
        });
        dashboardTileLayer.addTo(dashboardMap);
        dashboardTileLayer.bringToBack();
    }

    function configureMapSearch(entries) {
        const input = document.getElementById('dashboard-map-search');
        const list = document.getElementById('dashboard-map-suggestions');
        if (!input || !list) return;

        list.replaceChildren();
        entries.slice(0, 300).forEach(entry => {
            const option = document.createElement('option');
            option.value = entry.label;
            list.appendChild(option);
        });

        function selectResult() {
            const query = normalize(input.value);
            if (!query) return;
            const match = entries.find(entry => normalize(entry.label) === query)
                || entries.find(entry => normalize(entry.label).includes(query));
            if (match) match.focus();
        }

        input.addEventListener('change', selectResult);
        input.addEventListener('keydown', event => {
            if (event.key === 'Enter') {
                event.preventDefault();
                selectResult();
            }
        });
    }

    async function initDashboardMap() {
        const target = document.getElementById('dashboardInventoryMap');
        if (!target || !window.L) return;
        dashboardMapTarget = target;

        target.addEventListener('wheel', event => {
            if (!event.ctrlKey) {
                event.stopImmediatePropagation();
                target.classList.add('is-wheel-hint-visible');
                window.clearTimeout(target._wheelHintTimer);
                target._wheelHintTimer = window.setTimeout(() => {
                    target.classList.remove('is-wheel-hint-visible');
                }, 1400);
            }
        }, { capture: true, passive: true });

        try {
            const response = await fetch(target.dataset.apiUrl, {
                headers: { Accept: 'application/json' },
                credentials: 'same-origin',
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const payload = await response.json();
            if (payload.status !== 'success') throw new Error('Respuesta inválida');

            const allowedRouteNames = new Set(readJSON('dashboard-route-names', []).map(normalize));
            const odfs = readJSON('dashboard-odfs-map-data', []);
            const selectedSite = normalize(target.dataset.site);
            const selectedTrace = normalize(target.dataset.trazado);
            const dashboardIsFiltered = Boolean(selectedSite || selectedTrace);
            const routes = (Array.isArray(payload.data) ? payload.data : []).filter(route => (
                (!dashboardIsFiltered && !allowedRouteNames.size)
                || allowedRouteNames.has(normalize(route.nombre))
            ));
            const sites = Array.isArray(payload.sites) ? payload.sites : [];

            dashboardMap = L.map(target, {
                zoomControl: true,
                scrollWheelZoom: true,
                preferCanvas: true,
            });
            const wheelHint = document.createElement('div');
            wheelHint.className = 'inv-map-wheel-hint';
            wheelHint.textContent = 'Ctrl + rueda para acercar o alejar';
            target.appendChild(wheelHint);
            refreshMapTheme();

            const routeLayer = L.layerGroup().addTo(dashboardMap);
            const siteLayer = L.layerGroup().addTo(dashboardMap);
            const odfLayer = L.layerGroup().addTo(dashboardMap);
            const reserveLayer = L.layerGroup().addTo(dashboardMap);
            const bounds = L.latLngBounds([]);
            const searchEntries = [];
            const relatedSites = new Set();

            routes.forEach(route => {
                const routeBounds = L.latLngBounds([]);
                const routeTramos = Array.isArray(route.tramos) ? route.tramos : [];
                routeTramos.forEach(tramo => {
                    if (tramo.hub_site) relatedSites.add(normalize(tramo.hub_site));
                    if (tramo.destino) relatedSites.add(normalize(tramo.destino));
                    if (selectedTrace && normalize(tramo.tipo_trazado) !== selectedTrace) return;
                    const coordinates = (Array.isArray(tramo.coordenadas) ? tramo.coordenadas : [])
                        .filter(point => Array.isArray(point) && Number.isFinite(Number(point[0])) && Number.isFinite(Number(point[1])))
                        .map(point => [Number(point[0]), Number(point[1])]);
                    if (coordinates.length < 2) return;
                    const polyline = L.polyline(coordinates, {
                        color: traceColor(tramo.tipo_trazado),
                        weight: 3,
                        opacity: .88,
                    }).bindPopup(popupContent(route.nombre || 'Ruta', [
                        `Trazado: ${tramo.tipo_trazado || 'Sin clasificar'}`,
                        `${tramo.hub_site || tramo.origen || 'Sin origen'} → ${tramo.destino || 'Sin destino'}`,
                    ])).addTo(routeLayer);
                    routeBounds.extend(polyline.getBounds());
                    bounds.extend(polyline.getBounds());
                });

                const reserves = Array.isArray(route.reservas_nodos) ? route.reservas_nodos : [];
                reserves.forEach(reserve => {
                    const lat = Number(reserve.lat);
                    const lon = Number(reserve.lon);
                    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
                    const marker = L.marker([lat, lon], { icon: reserveIcon() })
                        .bindPopup(popupContent(reserve.nombre || 'Reserva de cable', [
                            `Tipo: ${reserve.tipo || 'Sin clasificar'}`,
                            `Holgura: ${Number(reserve.reserva_m || 0).toLocaleString('es-PE')} m`,
                            `Ruta: ${route.nombre || 'Sin ruta'}`,
                        ]))
                        .addTo(reserveLayer);
                    bounds.extend(marker.getLatLng());
                });

                if (routeBounds.isValid()) {
                    searchEntries.push({
                        label: route.nombre || 'Ruta',
                        focus() {
                            dashboardMap.fitBounds(routeBounds, { padding: [45, 45], maxZoom: 16 });
                        },
                    });
                }
            });

            const odfCountBySite = new Map();
            odfs.forEach(odf => {
                const key = normalize(odf.hub_site);
                if (!key) return;
                odfCountBySite.set(key, (odfCountBySite.get(key) || 0) + 1);
            });

            sites.forEach(site => {
                const key = normalize(site.nombre);
                if (selectedSite && key !== selectedSite && !relatedSites.has(key)) return;
                if (selectedTrace && !relatedSites.has(key)) return;
                const lat = Number(site.lat);
                const lon = Number(site.lon);
                if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;

                const odfCount = odfCountBySite.get(key) || 0;
                const siteMarker = L.marker([lat, lon], { icon: siteIcon(), zIndexOffset: 100 })
                    .bindPopup(popupContent(site.nombre || 'Site', [
                        `${odfCount} ODF asociados`,
                    ]))
                    .addTo(siteLayer);
                bounds.extend(siteMarker.getLatLng());
                searchEntries.push({
                    label: site.nombre || 'Site',
                    focus() {
                        dashboardMap.setView([lat, lon], 15);
                        siteMarker.openPopup();
                    },
                });

                if (odfCount) {
                    L.marker([lat, lon], { icon: odfIcon(odfCount), zIndexOffset: 120 })
                        .bindPopup(popupContent(`ODF en ${site.nombre || 'Site'}`, [
                            `${odfCount} equipos asociados`,
                            'Ubicación representada mediante las coordenadas del Site.',
                        ]))
                        .addTo(odfLayer);
                }
            });

            L.control.layers(null, {
                'Sites / Hubs': siteLayer,
                'ODF en Sites': odfLayer,
                'Rutas / Tramos': routeLayer,
                'Reservas de cable': reserveLayer,
            }, {
                collapsed: window.innerWidth < 900,
                position: 'topright',
            }).addTo(dashboardMap);

            if (bounds.isValid()) {
                dashboardMap.fitBounds(bounds, { padding: [32, 32], maxZoom: 15 });
            } else {
                dashboardMap.setView([-23.65, -70.4], 11);
                const emptyState = document.createElement('div');
                emptyState.className = 'inv-map-empty';
                emptyState.textContent = 'No hay elementos georreferenciados para los filtros seleccionados.';
                target.appendChild(emptyState);
            }
            configureMapSearch(searchEntries);
            target.classList.add('is-ready');
            setTimeout(() => dashboardMap.invalidateSize(), 100);
        } catch (error) {
            console.warn('No se pudo cargar el mapa del dashboard', error);
            target.classList.add('is-error');
            const message = target.querySelector('.inv-map-loading');
            if (message) message.lastChild.textContent = 'No fue posible cargar el inventario geográfico.';
        }
    }

    function init() {
        formatDashboardCounts();
        renderCharts();
        initDashboardCustomization();
        const loadMap = () => initDashboardMap();
        if ('requestIdleCallback' in window) {
            window.requestIdleCallback(loadMap, { timeout: 900 });
        } else {
            window.setTimeout(loadMap, 120);
        }

        const themeObserver = new MutationObserver(mutations => {
            if (mutations.some(mutation => mutation.attributeName === 'data-theme')) {
                renderCharts();
                refreshMapTheme();
            }
        });
        themeObserver.observe(document.documentElement, {
            attributes: true,
            attributeFilter: ['data-theme'],
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
