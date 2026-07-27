(function() {
    const state = {
        map: null,
        layerGroups: {
            tramos: L.layerGroup(),
            sites: L.layerGroup(),
            routeSelection: L.layerGroup()
        },
        tileLayer: null,
        data: [],
        sites: [],
        filters: {
            'AEREO': true,
            'SOTERRADO': true,
            'HIBRIDO': true,
            'DESCONOCIDO': true
        },
        dataCache: {
            reservas: [] // { marker, rutaNombre }
        },
        rutasMostrandoReservas: new Set(),
        mostrarTodasReservas: false,
        mostrarTodasRutas: true,
        rutasPolylines: {}, // { 'Nombre Ruta': [polyline1, polyline2, ...] }
        rutasHalos: {},     // { 'Nombre Ruta': [halo1, halo2, ...] }
        routeLayerEntries: [],
        selectedRuta: null,
        renderedSelectionRuta: null,
        siteMarkers: {},
        selectedExternalMarker: null,
        routeSearch: {
            index: [],
            matches: [],
            activeIndex: -1,
            isOpen: false
        },
        navigation: {
            sitePayload: null,
            odf: null,
            portPage: 1,
            loadedOdfId: null,
            portAbortController: null
        }
    };

    const NETWORK_MARKER_ICONS = {
        site: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 21V9.5L12 5v16M12 11h8.5v10M7.5 12h.01M7.5 16h.01M16 15h.01M16 18h.01M9 5V2.8M7.6 3.5 9 2l1.4 1.5"/></svg>',
        odf: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="2.5" width="14" height="19" rx="2"/><path d="M8 6.5h8M8 17.5h8M9 21.5v1M15 21.5v1"/><circle cx="9" cy="10.5" r=".9"/><circle cx="15" cy="10.5" r=".9"/><circle cx="9" cy="14" r=".9"/><circle cx="15" cy="14" r=".9"/></svg>',
        poste: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v19M5 7h14M7.5 7v3M16.5 7v3M9 3.5h6M4 10.5l3.5-2M20 10.5l-3.5-2"/></svg>',
        mufa: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="8" y="3" width="8" height="18" rx="4"/><path d="M8 8h8M8 12h8M8 16h8M4 9h4M16 9h4M4 15h4M16 15h4"/></svg>',
        camara: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><ellipse cx="12" cy="12" rx="9" ry="7"/><ellipse cx="12" cy="12" rx="6.5" ry="4.7"/><path d="M6.5 9.5h11M6.5 14.5h11"/><circle cx="9" cy="12" r=".65" fill="currentColor" stroke="none"/><circle cx="15" cy="12" r=".65" fill="currentColor" stroke="none"/></svg>',
        reserva: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11.5" cy="12" r="7.5"/><circle cx="11.5" cy="12" r="5"/><circle cx="11.5" cy="12" r="2.5"/><path d="M18.5 14.5h2v2h1.5"/></svg>',
        torre: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 2 5.5 20h-11L12 2ZM8 16h8M9 12h6M10 8h4M5 6a9 9 0 0 1 14 0M2.5 3.5a13 13 0 0 1 19 0"/></svg>',
        default: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 10c0 5.5-8 12-8 12S4 15.5 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/></svg>'
    };

    function normalizeMarkerType(value) {
        const normalized = String(value || '')
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .toLowerCase()
            .trim();
        if (normalized.includes('poste')) return 'poste';
        if (normalized.includes('mufa') || normalized.includes('empalme') || normalized.includes('cierre')) return 'mufa';
        if (normalized.includes('camara') || normalized.includes('registro')) return 'camara';
        if (normalized.includes('reserva') || normalized.includes('bobina')) return 'reserva';
        if (normalized.includes('torre')) return 'torre';
        if (normalized.includes('odf')) return 'odf';
        if (normalized.includes('site') || normalized.includes('hub')) return 'site';
        return 'default';
    }

    function buildNetworkMarkerHtml(type, label, options) {
        const normalizedType = normalizeMarkerType(type);
        const settings = options || {};
        const classes = [
            'network-marker',
            settings.anchor ? 'network-marker--anchor' : 'network-marker--asset',
            `network-marker--${normalizedType}`
        ];
        if (settings.site) classes.push('site-marker');
        const labelClasses = settings.site
            ? 'network-marker-label site-marker-label'
            : 'network-marker-label';
        return `<div class="${classes.join(' ')}">${NETWORK_MARKER_ICONS[normalizedType] || NETWORK_MARKER_ICONS.default}<span class="${labelClasses}">${displayValue(label, type || 'Elemento')}</span></div>`;
    }

    function updateMarkerLabelVisibility() {
        const mapElement = document.getElementById('map');
        if (!mapElement || !state.map) return;
        const zoom = state.map.getZoom();
        mapElement.classList.toggle('map-site-labels-visible', zoom >= 14);
        mapElement.classList.toggle('map-asset-labels-visible', zoom >= 16);
    }

    function initMap() {
        const mapElement = document.getElementById('map');
        state.map = L.map('map', {
            zoomControl: false,
            attributionControl: false,
            preferCanvas: true
        }).setView([-12.046374, -77.042793], 6); // Centro general de Perú como default

        L.control.zoom({ position: 'bottomright' }).addTo(state.map);

        state.layerGroups.tramos.addTo(state.map);
        state.layerGroups.sites.addTo(state.map);
        state.layerGroups.routeSelection.addTo(state.map);

        const attribution = mapElement.dataset.tileAttribution || 'Cartografía configurable';
        const createLayer = (url) => url ? L.tileLayer(url, { maxZoom: 19, attribution }) : null;
        const mapClaro = createLayer(mapElement.dataset.tileLight);
        const mapOscuro = createLayer(mapElement.dataset.tileDark);
        const satelite = createLayer(mapElement.dataset.tileSatellite);
        const baseMaps = {};
        const basemapThemes = new Map();
        if (mapClaro) {
            baseMaps['Mapa claro'] = mapClaro;
            basemapThemes.set(mapClaro, 'light');
        }
        if (mapOscuro) {
            baseMaps['Mapa oscuro'] = mapOscuro;
            basemapThemes.set(mapOscuro, 'dark');
        }
        if (satelite) {
            baseMaps['Satélite'] = satelite;
            basemapThemes.set(satelite, 'satellite');
        }

        let tileErrors = 0;
        const showOfflineNotice = () => {
            if (document.getElementById('map-offline-notice')) return;
            const notice = document.createElement('div');
            notice.id = 'map-offline-notice';
            notice.className = 'map-offline-notice';
            notice.setAttribute('role', 'status');
            notice.textContent = 'Mapa base no disponible. El inventario de troncales continúa visible.';
            mapElement.parentElement.appendChild(notice);
        };
        Object.values(baseMaps).forEach((layer) => {
            layer.on('tileerror', () => {
                tileErrors += 1;
                if (tileErrors >= 3) showOfflineNotice();
            });
            layer.on('load', () => {
                mapElement.classList.remove('map-fallback-grid');
                const notice = document.getElementById('map-offline-notice');
                if (notice) notice.remove();
            });
        });

        const theme = document.documentElement.getAttribute('data-theme') || 'light';
        const isDark = theme === 'dark' || document.body.classList.contains('dark-mode');

        const preferredLayer = isDark ? (mapOscuro || mapClaro) : (mapClaro || mapOscuro);
        if (preferredLayer) {
            preferredLayer.addTo(state.map);
            state.tileLayer = preferredLayer;
            mapElement.dataset.basemapTheme = basemapThemes.get(preferredLayer) || (isDark ? 'dark' : 'light');
        }
        else showOfflineNotice();

        if (Object.keys(baseMaps).length) {
            L.control.layers(baseMaps, null, { position: 'topright', collapsed: true }).addTo(state.map);
            state.map.on('baselayerchange', (event) => {
                state.tileLayer = event.layer;
                mapElement.dataset.basemapTheme = basemapThemes.get(event.layer) || 'light';
                applyRouteFocusStyles();
            });
        }

        setupFullscreenControl();
        setupLayerPanel();
        setupMapState();
        setupFilters();
        setupSearch();
        setupNetworkNavigation();
        cargarDatosInventario();
        updateMarkerLabelVisibility();
        state.map.on('zoomend', updateMarkerLabelVisibility);

        // Cerrar popup draggable al hacer click en el mapa
        state.map.on('click', function(e) {
            // Evitar cerrar si el clic se originó dentro del popup (por si Leaflet lo captura globalmente)
            if (e.originalEvent && e.originalEvent.target && e.originalEvent.target.closest('#draggable-route-popup')) {
                return;
            }
            closeRoutePanel();
        });

        const panel = document.getElementById('draggable-route-popup');
        if (panel) {
            L.DomEvent.disableClickPropagation(panel);
            L.DomEvent.disableScrollPropagation(panel);
        }
    }

    function makeDraggable(element) {
        let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
        const header = element.querySelector('h3');
        if (header) {
            header.style.cursor = 'move';
            header.addEventListener('mousedown', dragMouseDown);
        } else {
            element.addEventListener('mousedown', dragMouseDown);
        }

        function dragMouseDown(e) {
            e = e || window.event;
            // Ignorar si se hace clic en botones, inputs, etc.
            if (e.target.closest('button') || e.target.closest('input') || e.target.closest('label') || e.target.closest('.toggle-switch')) return;
            e.preventDefault();
            pos3 = e.clientX;
            pos4 = e.clientY;
            
            // Usamos capture: true para garantizar que document capture el evento
            // antes de que Leaflet disableClickPropagation lo mate en el panel
            document.addEventListener('mouseup', closeDragElement, { capture: true });
            document.addEventListener('mousemove', elementDrag, { capture: true });
        }

        function elementDrag(e) {
            e = e || window.event;
            e.preventDefault();
            pos1 = pos3 - e.clientX;
            pos2 = pos4 - e.clientY;
            pos3 = e.clientX;
            pos4 = e.clientY;
            
            // Si el elemento aún tiene el transform: translateY(-50%) inicial
            if (element.style.transform) {
                // Calcular la posición visual real respecto al offsetParent sin usar viewport
                const actualTop = element.offsetTop - (element.offsetHeight / 2);
                const actualLeft = element.offsetLeft;
                
                element.style.transform = '';
                element.style.top = actualTop + 'px';
                element.style.left = actualLeft + 'px';
                element.style.right = 'auto'; // Limpiar la regla 'right'
            }
            
            element.style.top = (element.offsetTop - pos2) + "px";
            element.style.left = (element.offsetLeft - pos1) + "px";
        }

        function closeDragElement() {
            document.removeEventListener('mouseup', closeDragElement, { capture: true });
            document.removeEventListener('mousemove', elementDrag, { capture: true });
        }
    }

    function setupFullscreenControl() {
        const btn = document.getElementById('fullscreen-map-btn');
        if(!btn) return;
        btn.addEventListener('click', () => {
            const container = document.getElementById('map').parentElement;
            if (!document.fullscreenElement) {
                container.requestFullscreen().catch(err => {
                    console.error(`Error attempting to enable fullscreen: ${err.message}`);
                });
            } else {
                document.exitFullscreen();
            }
        });
    }

    function setupLayerPanel() {
        const panel = document.getElementById('map-layers-panel');
        const toggle = document.getElementById('map-layers-toggle');
        const close = document.getElementById('map-layers-close');
        if (!panel || !toggle || !close) return;

        const setOpen = (isOpen) => {
            panel.classList.toggle('is-open', isOpen);
            panel.setAttribute('aria-hidden', String(!isOpen));
            toggle.setAttribute('aria-expanded', String(isOpen));
            toggle.title = isOpen ? 'Cerrar capas' : 'Abrir capas';
        };

        toggle.addEventListener('click', () => setOpen(!panel.classList.contains('is-open')));
        close.addEventListener('click', () => setOpen(false));
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                setOpen(false);
                closeRoutePanel();
            }
        });

        L.DomEvent.disableClickPropagation(panel);
        L.DomEvent.disableScrollPropagation(panel);
    }

    function setupMapState() {
        const retry = document.getElementById('map-state-retry');
        if (retry) retry.addEventListener('click', cargarDatosInventario);
    }

    function setMapState(kind, title, text, canRetry) {
        const container = document.getElementById('map-state');
        const titleNode = document.getElementById('map-state-title');
        const textNode = document.getElementById('map-state-text');
        const retry = document.getElementById('map-state-retry');
        const mapElement = document.getElementById('map');
        if (!container || !titleNode || !textNode) return;

        container.classList.remove('map-state--loading', 'map-state--empty', 'map-state--error');
        if (!kind) {
            container.classList.remove('is-visible');
            if (mapElement) mapElement.setAttribute('aria-busy', 'false');
            return;
        }

        container.classList.add('is-visible', `map-state--${kind}`);
        titleNode.textContent = title;
        textNode.textContent = text;
        if (retry) retry.hidden = !canRetry;
        if (mapElement) mapElement.setAttribute('aria-busy', String(kind === 'loading'));
    }

    function closeRoutePanel() {
        const panel = document.getElementById('draggable-route-popup');
        if (!panel) return;
        panel.classList.remove('is-open');
        panel.setAttribute('aria-hidden', 'true');
    }

    function fitRouteBounds(nombreRuta) {
        const polylines = state.rutasPolylines[nombreRuta] || [];
        const bounds = L.latLngBounds([]);
        polylines.forEach((polyline) => bounds.extend(polyline.getBounds()));
        if (bounds.isValid()) {
            const isSmallScreen = window.matchMedia('(max-width: 700px)').matches;
            state.map.flyToBounds(bounds, {
                paddingTopLeft: isSmallScreen ? [28, 150] : [70, 90],
                paddingBottomRight: isSmallScreen ? [28, 230] : [430, 90],
                maxZoom: 16
            });
        }
    }

    function setupFilters() {
        const updateFilters = () => {
            state.filters['AEREO'] = document.getElementById('chk-aereo').checked;
            state.filters['SOTERRADO'] = document.getElementById('chk-soterrado').checked;
            state.filters['HIBRIDO'] = document.getElementById('chk-hibrido') ? document.getElementById('chk-hibrido').checked : true;
            state.filters['DESCONOCIDO'] = document.getElementById('chk-desconocido').checked;
            syncTramosVisibility();
        };

        $('#chk-aereo, #chk-soterrado, #chk-hibrido, #chk-desconocido').on('change', updateFilters);

        $('#chk-reservas-global').on('change', function() {
            state.mostrarTodasReservas = this.checked;
            renderReservasVisibilidad();
        });

        $('#chk-rutas-global').on('change', function() {
            state.mostrarTodasRutas = this.checked;
            if (this.checked) {
                state.selectedRuta = null;
                resetRouteSearchInput();
                closeRoutePanel();
            }
            syncTramosVisibility();
        });
    }


    function normalizeSearchText(value) {
        return String(value || '')
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .toLowerCase()
            .replace(/[_/\\-]+/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();
    }

    function getRouteSearchMeta(ruta) {
        const tramos = Array.isArray(ruta.tramos) ? ruta.tramos : [];
        const principal = tramos.find((tramo) =>
            Array.isArray(tramo.coordenadas) && tramo.coordenadas.length
        ) || tramos[0] || {};
        const uniqueValues = (field) => [...new Set(
            tramos.map((tramo) => String(tramo[field] || '').trim()).filter(Boolean)
        )];
        const nombre = String(ruta.nombre || 'Ruta sin nombre').trim();
        const codigo = nombre.includes('_') ? nombre.split('_')[0] : nombre;
        const origenes = uniqueValues('origen');
        const destinos = uniqueValues('destino');
        const sites = uniqueValues('hub_site');
        const odfs = uniqueValues('odf_nombre');
        const tiposFibra = uniqueValues('tipo_fibra');
        const seriales = uniqueValues('serial');
        const tipos = [...new Set(tramos.map((tramo) => getFilterCat(tramo.tipo_trazado)))];
        const estados = uniqueValues('estado');
        const origen = principal.hub_site || principal.origen || ruta.otu || 'Origen no especificado';
        const destino = principal.destino || ruta.olt || 'Destino no especificado';
        const distanciaM = tramos.reduce((total, tramo) => {
            const value = Number(tramo.distancia_m);
            return total + (Number.isFinite(value) ? value : 0);
        }, 0);
        const searchText = normalizeSearchText([
            nombre,
            codigo,
            ruta.otu,
            ruta.olt,
            ruta.pon,
            ...origenes,
            ...destinos,
            ...sites,
            ...odfs,
            ...tiposFibra,
            ...seriales,
            ...tipos,
            ...estados
        ].join(' '));

        return {
            ruta,
            nombre,
            codigo,
            origen: String(origen),
            destino: String(destino),
            tipo: tipos.length === 1 ? tipos[0] : (tipos.length > 1 ? 'HIBRIDO' : 'DESCONOCIDO'),
            estado: estados[0] || 'Sin estado',
            distanciaM,
            searchText
        };
    }

    function buildRouteSearchIndex() {
        state.routeSearch.index = state.data.map(getRouteSearchMeta);
        state.routeSearch.matches = [];
        state.routeSearch.activeIndex = -1;
    }

    function findRouteMatches(query) {
        const normalized = normalizeSearchText(query);
        if (normalized.length < 2) return [];
        const terms = normalized.split(' ').filter(Boolean);
        return state.routeSearch.index
            .filter((item) => terms.every((term) => item.searchText.includes(term)))
            .sort((a, b) => {
                const aName = normalizeSearchText(a.nombre);
                const bName = normalizeSearchText(b.nombre);
                const aCode = normalizeSearchText(a.codigo);
                const bCode = normalizeSearchText(b.codigo);
                const score = (name, code) => {
                    if (name === normalized || code === normalized) return 0;
                    if (name.startsWith(normalized) || code.startsWith(normalized)) return 1;
                    return 2;
                };
                return score(aName, aCode) - score(bName, bCode) ||
                    a.nombre.localeCompare(b.nombre, 'es', { sensitivity: 'base' });
            })
            .slice(0, 8);
    }

    function routeSearchElements() {
        return {
            container: document.getElementById('route-search'),
            input: document.getElementById('route-search-input'),
            results: document.getElementById('route-search-results'),
            status: document.getElementById('route-search-status'),
            clear: document.getElementById('clear-search-btn')
        };
    }

    function setRouteSearchOpen(isOpen) {
        const { input, results } = routeSearchElements();
        if (!input || !results) return;
        state.routeSearch.isOpen = Boolean(isOpen);
        results.hidden = !state.routeSearch.isOpen;
        input.setAttribute('aria-expanded', String(state.routeSearch.isOpen));
        if (!state.routeSearch.isOpen) {
            state.routeSearch.activeIndex = -1;
            input.removeAttribute('aria-activedescendant');
        }
    }

    function updateRouteSearchActiveOption() {
        const { input, results } = routeSearchElements();
        if (!input || !results) return;
        const options = [...results.querySelectorAll('[role="option"]')];
        options.forEach((option, index) => {
            const isActive = index === state.routeSearch.activeIndex;
            option.classList.toggle('is-active', isActive);
            option.setAttribute('aria-selected', String(isActive));
            if (isActive) {
                input.setAttribute('aria-activedescendant', option.id);
                option.scrollIntoView({ block: 'nearest' });
            }
        });
    }

    function renderRouteSearchResults(query) {
        const { results, status } = routeSearchElements();
        if (!results || !status) return;
        const normalized = normalizeSearchText(query);
        results.replaceChildren();
        state.routeSearch.activeIndex = -1;
        state.routeSearch.matches = findRouteMatches(query);

        if (normalized.length < 2) {
            status.textContent = 'Escribe al menos 2 caracteres.';
            setRouteSearchOpen(false);
            return;
        }

        if (!state.routeSearch.matches.length) {
            const empty = document.createElement('div');
            empty.className = 'route-search-empty';
            empty.textContent = 'No hay troncales que coincidan.';
            results.appendChild(empty);
            status.textContent = 'Sin coincidencias.';
            setRouteSearchOpen(true);
            return;
        }

        state.routeSearch.matches.forEach((item, index) => {
            const option = document.createElement('button');
            option.type = 'button';
            option.id = `route-search-option-${index}`;
            option.className = 'route-search-result';
            option.setAttribute('role', 'option');
            option.setAttribute('aria-selected', 'false');
            option.title = item.nombre;

            const heading = document.createElement('span');
            heading.className = 'route-search-result__heading';
            const code = document.createElement('strong');
            code.textContent = item.codigo;
            const type = document.createElement('span');
            type.className = `route-search-result__type route-search-result__type--${item.tipo.toLowerCase()}`;
            type.textContent = item.tipo === 'DESCONOCIDO' ? 'Sin clasificar' : item.tipo;
            heading.append(code, type);

            const path = document.createElement('span');
            path.className = 'route-search-result__path';
            path.textContent = `${item.origen} → ${item.destino}`;

            const meta = document.createElement('span');
            meta.className = 'route-search-result__meta';
            const distance = item.distanciaM > 0
                ? `${(item.distanciaM / 1000).toLocaleString('es-PE', { maximumFractionDigits: 2 })} km`
                : 'Distancia no registrada';
            meta.textContent = `${distance} · ${item.estado}`;

            option.append(heading, path, meta);
            option.addEventListener('mouseenter', () => {
                state.routeSearch.activeIndex = index;
                updateRouteSearchActiveOption();
            });
            option.addEventListener('click', () => selectRoute(item.ruta));
            results.appendChild(option);
        });

        status.textContent = `${state.routeSearch.matches.length} coincidencias mostradas.`;
        setRouteSearchOpen(true);
    }

    function resetRouteSearchInput() {
        const { input, clear, status } = routeSearchElements();
        if (input) {
            input.value = '';
            delete input.dataset.selectedRoute;
        }
        if (clear) clear.style.display = 'none';
        if (status) status.textContent = '';
        setRouteSearchOpen(false);
    }

    function clearRouteSearch(options) {
        const settings = options || {};
        const needsRender = !state.mostrarTodasRutas;
        resetRouteSearchInput();
        state.selectedRuta = null;
        state.mostrarTodasRutas = true;
        const allRoutes = document.getElementById('chk-rutas-global');
        if (allRoutes) allRoutes.checked = true;
        closeRoutePanel();
        if (needsRender) syncTramosVisibility();
        else applyRouteFocusStyles();
        if (settings.fit !== false) fitBoundsToData();
    }

    function selectRoute(ruta, tramo, options) {
        if (!ruta) return;
        const settings = options || {};
        let needsRender = !state.mostrarTodasRutas;
        const filterIds = {
            AEREO: 'chk-aereo',
            SOTERRADO: 'chk-soterrado',
            HIBRIDO: 'chk-hibrido',
            DESCONOCIDO: 'chk-desconocido'
        };
        const routeCategories = new Set(
            (ruta.tramos || []).map((item) => getFilterCat(item.tipo_trazado))
        );
        routeCategories.forEach((category) => {
            if (!state.filters[category]) {
                state.filters[category] = true;
                needsRender = true;
                const checkbox = document.getElementById(filterIds[category]);
                if (checkbox) checkbox.checked = true;
            }
        });
        const meta = state.routeSearch.index.find((item) => item.ruta === ruta) || getRouteSearchMeta(ruta);
        const { input, clear } = routeSearchElements();
        state.selectedRuta = ruta.nombre;
        state.mostrarTodasRutas = true;
        const allRoutes = document.getElementById('chk-rutas-global');
        if (allRoutes) allRoutes.checked = true;
        if (input) {
            input.value = meta.codigo;
            input.dataset.selectedRoute = ruta.nombre;
        }
        if (clear) clear.style.display = 'flex';
        setRouteSearchOpen(false);

        if (needsRender) syncTramosVisibility();
        else applyRouteFocusStyles();
        if (settings.fit !== false) fitRouteBounds(ruta.nombre);

        const tramoPrincipal = tramo || (ruta.tramos || []).find((item) =>
            Array.isArray(item.coordenadas) && item.coordenadas.length > 0
        ) || (ruta.tramos || [])[0];
        if (tramoPrincipal && settings.openPanel !== false) {
            openRoutePanel(ruta, tramoPrincipal);
        }
    }

    function setupSearch() {
        const { container, input, results, clear: clearBtn } = routeSearchElements();
        const btn = document.getElementById('search-btn');
        if (!container || !input || !results || !btn || !clearBtn) return;

        btn.addEventListener('click', buscarYResaltarRuta);
        input.addEventListener('keydown', (event) => {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                if (!state.routeSearch.isOpen) renderRouteSearchResults(input.value);
                if (!state.routeSearch.matches.length) return;
                event.preventDefault();
                const direction = event.key === 'ArrowDown' ? 1 : -1;
                const total = state.routeSearch.matches.length;
                state.routeSearch.activeIndex =
                    (state.routeSearch.activeIndex + direction + total) % total;
                updateRouteSearchActiveOption();
            } else if (event.key === 'Enter') {
                event.preventDefault();
                if (state.routeSearch.isOpen && state.routeSearch.matches.length) {
                    const index = state.routeSearch.activeIndex >= 0
                        ? state.routeSearch.activeIndex : 0;
                    selectRoute(state.routeSearch.matches[index].ruta);
                } else {
                    buscarYResaltarRuta();
                }
            } else if (event.key === 'Escape' && state.routeSearch.isOpen) {
                event.preventDefault();
                event.stopPropagation();
                setRouteSearchOpen(false);
            }
        });

        input.addEventListener('input', () => {
            delete input.dataset.selectedRoute;
            clearBtn.style.display = input.value ? 'flex' : 'none';
            renderRouteSearchResults(input.value);
        });
        input.addEventListener('focus', () => {
            if (normalizeSearchText(input.value).length >= 2 && !input.dataset.selectedRoute) {
                renderRouteSearchResults(input.value);
            }
        });

        clearBtn.addEventListener('click', () => clearRouteSearch({ fit: true }));
        document.addEventListener('click', (event) => {
            if (!container.contains(event.target)) setRouteSearchOpen(false);
        });
    }

    function buscarYResaltarRuta() {
        const { input } = routeSearchElements();
        if (!input) return;
        const query = input.value.trim();
        
        if (!query) {
            clearRouteSearch({ fit: false });
            return;
        }

        const selectedName = input.dataset.selectedRoute;
        const normalized = normalizeSearchText(query);
        const exact = state.routeSearch.index.find((item) =>
            item.nombre === selectedName ||
            normalizeSearchText(item.nombre) === normalized ||
            normalizeSearchText(item.codigo) === normalized
        );
        const matches = exact ? [exact] : findRouteMatches(query);
        
        if (matches.length === 1 || exact) {
            selectRoute(matches[0].ruta);
        } else if (matches.length > 1) {
            renderRouteSearchResults(query);
            input.focus();
        } else {
            if (window.FiberGenius && window.FiberGenius.notify) {
                window.FiberGenius.notify(
                    normalized.length < 2
                        ? 'Escribe al menos 2 caracteres para buscar.'
                        : 'Troncal no encontrada en el mapa.',
                    'warning'
                );
            }
        }
    }

    function limpiarResaltadoRuta() {
        state.selectedRuta = null;
        applyRouteFocusStyles();
    }

    // Funcion expuesta globalmente para el checkbox dentro del popup de leaflet
    window.toggleReservasRuta = function(nombreRuta, isChecked) {
        if (isChecked) {
            state.rutasMostrandoReservas.add(nombreRuta);
        } else {
            state.rutasMostrandoReservas.delete(nombreRuta);
        }
        renderReservasVisibilidad();
    };

    function renderReservasVisibilidad() {
        state.dataCache.reservas.forEach(item => {
            const debeMostrar = state.mostrarTodasReservas || state.rutasMostrandoReservas.has(item.rutaNombre);
            if (debeMostrar) {
                if (!state.map.hasLayer(item.marker)) {
                    item.marker.addTo(state.map);
                }
            } else {
                if (state.map.hasLayer(item.marker)) {
                    item.marker.remove();
                }
            }
        });
    }

    function cargarDatosInventario() {
        let basePath = '';
        if (window.location.pathname.includes('/fg-6/')) {
            basePath = '/fg-6';
        }

        setMapState('loading', 'Cargando red…', 'Preparando troncales y puntos geográficos.', false);
        
        $.ajax({
            url: basePath + '/api/inventario/datos/',
            method: 'GET',
            success: function(response) {
                if(response.status === 'success') {
                    state.data = Array.isArray(response.data) ? response.data : [];
                    state.sites = Array.isArray(response.sites) ? response.sites : [];
                    buildRouteSearchIndex();
                    renderTramos();
                    fitBoundsToData();
                    updateSummaryCards();

                    const hasCoordinates = state.data.some((ruta) =>
                        (ruta.tramos || []).some((tramo) =>
                            Array.isArray(tramo.coordenadas) && tramo.coordenadas.length > 0
                        )
                    );
                    if (!state.data.length && !state.sites.length) {
                        setMapState(
                            'empty',
                            'Aún no hay troncales registradas',
                            'Cuando cargues el inventario, las rutas y sus indicadores aparecerán aquí.',
                            false
                        );
                    } else if (!hasCoordinates && !state.sites.length) {
                        setMapState(
                            'empty',
                            'Las troncales no tienen coordenadas',
                            'Completa la georreferenciación para dibujar la red sin cambiar la estructura de carga.',
                            false
                        );
                    } else {
                        setMapState(null);
                    }

                    // Verificar si venimos redirigidos desde el Dashboard con una ruta específica
                    const urlParams = new URLSearchParams(window.location.search);
                    const rutaUrl = urlParams.get('ruta');
                    if (rutaUrl) {
                        const input = document.getElementById('route-search-input');
                        if (input) {
                            input.value = rutaUrl;
                            buscarYResaltarRuta();
                        }
                    }
                } else {
                    setMapState(
                        'error',
                        'No pudimos interpretar el inventario',
                        response.message || 'El servidor devolvió una respuesta inesperada.',
                        true
                    );
                }
            },
            error: function(err) {
                console.error("Error cargando inventario:", err);
                setMapState(
                    'error',
                    'No pudimos cargar la red',
                    'Revisa la conexión con Fiber Genius y vuelve a intentarlo.',
                    true
                );
            }
        });
    }

    function updateSummaryCards() {
        let total = state.data.length;
        let aereas = 0;
        let soterradas = 0;
        let hibridas = 0;

        state.data.forEach(ruta => {
            let hasAereo = false;
            let hasSoterrado = false;
            let hasHibrido = false;
            
            // Analizar todos los tramos de la ruta para clasificarla
            (ruta.tramos || []).forEach(tramo => {
                let tipo = (tramo.tipo_trazado || '').toUpperCase().trim();
                if (tipo === 'AEREO') hasAereo = true;
                if (tipo === 'SOTERRADO') hasSoterrado = true;
                if (tipo === 'HIBRIDO') hasHibrido = true;
            });

            if (hasHibrido || (hasAereo && hasSoterrado)) hibridas++;
            else if (hasAereo) aereas++;
            else if (hasSoterrado) soterradas++;
        });

        // Actualizar UI con efectos de conteo si es necesario
        $('#count-total').text(total);
        $('#count-aereas').text(aereas);
        $('#count-soterradas').text(soterradas);
        $('#count-hibridas').text(hibridas);
    }

    function getColorForTrazado(tipo) {
        if (!tipo) return '#94a3b8'; // SLATE 400 (Desconocido)
        tipo = tipo.toUpperCase().trim();
        if (tipo === 'AEREO') return '#3b82f6'; // BLUE 500
        if (tipo === 'SOTERRADO') return '#f59e0b'; // AMBER 500
        if (tipo === 'HIBRIDO') return '#8b5cf6'; // VIOLET 500
        return '#94a3b8'; // Default Desconocido
    }
    
    function getFilterCat(tipo) {
        if (!tipo) return 'DESCONOCIDO';
        tipo = tipo.toUpperCase().trim();
        if (tipo === 'AEREO') return 'AEREO';
        if (tipo === 'SOTERRADO') return 'SOTERRADO';
        if (tipo === 'HIBRIDO') return 'HIBRIDO';
        return 'DESCONOCIDO';
    }

    function calcularDistanciaCoordenadas(coords) {
        if (!coords || coords.length < 2) return 0;
        let dist = 0;
        for (let i = 0; i < coords.length - 1; i++) {
            const p1 = L.latLng(coords[i][0], coords[i][1]);
            const p2 = L.latLng(coords[i+1][0], coords[i+1][1]);
            dist += p1.distanceTo(p2);
        }
        return dist;
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function displayValue(value, fallback) {
        const normalized = value == null ? '' : String(value).trim();
        return escapeHtml(normalized || fallback || 'N/A');
    }

    function buildPopupHtml(ruta, tramo) {
        let estadoBadge = 'badge--info';
        if (tramo.estado) {
            const eMay = tramo.estado.toUpperCase();
            if (eMay.includes('OPERATIV')) estadoBadge = 'badge--success';
            if (eMay.includes('MANTENIMIENTO')) estadoBadge = 'badge--warning';
            if (eMay.includes('CAIDO') || eMay.includes('CORTE')) estadoBadge = 'badge--danger';
        }

        const trazadoCategoria = getFilterCat(tramo.tipo_trazado).toLowerCase();

        const origenMostrar = tramo.origen ? tramo.origen : ruta.otu;
        const destinoMostrar = tramo.destino ? tramo.destino : ruta.olt;
        const hubSiteOrigen = tramo.hub_site || origenMostrar;

        // Calcular conteo de reservas por tipo y suma total de metros de reserva
        const conteoReservas = {};
        let sumaReservasM = 0;
        if (ruta.reservas_nodos) {
            ruta.reservas_nodos.forEach(res => {
                const tipo = res.tipo || 'Otro';
                conteoReservas[tipo] = (conteoReservas[tipo] || 0) + 1;
                sumaReservasM += parseFloat(res.reserva_m || 0);
            });
        }

        let reservasResumenHtml = '';
        const tiposReservas = Object.keys(conteoReservas);
        if (tiposReservas.length > 0) {
            reservasResumenHtml = '<div class="popup-section-title">Detalle de Reservas</div><div class="popup-grid-2col">';
            tiposReservas.forEach(tipo => {
                reservasResumenHtml += `<div class="popup-item"><span class="label">${displayValue(tipo, 'Otro')}</span><span class="value">${conteoReservas[tipo]}</span></div>`;
            });
            reservasResumenHtml += '</div>';
        }

        // Obtener la distancia con fallback
        let distanciaMetros = parseFloat(tramo.distancia_m || 0);
        if (!distanciaMetros && ruta.distancia_m) {
            distanciaMetros = parseFloat(ruta.distancia_m);
        }
        if (!distanciaMetros && tramo.coordenadas && tramo.coordenadas.length >= 2) {
            distanciaMetros = calcularDistanciaCoordenadas(tramo.coordenadas);
        }
        const distanciaTexto = distanciaMetros > 0 ? (distanciaMetros / 1000).toFixed(2) + ' km' : 'N/A';

        return `
            <div class="popup-inventario route-detail">
                <header class="route-detail__header">
                    <div class="route-detail__heading">
                        <span class="route-detail__eyebrow">Troncal seleccionada</span>
                        <h3>${displayValue(ruta.nombre, 'Ruta sin nombre')}</h3>
                        <p class="route-detail__path">
                            <span>${displayValue(hubSiteOrigen)}</span>
                            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>
                            <span>${displayValue(destinoMostrar)}</span>
                        </p>
                    </div>
                    <button class="route-detail__close" type="button" data-route-close aria-label="Cerrar detalle de troncal">
                        <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
                    </button>
                </header>
                <div class="route-detail__body">
                    <div class="popup-section-title">Información general</div>
                    <div class="popup-grid-2col">
                        <div class="popup-item"><span class="label">Site de origen</span><span class="value">${displayValue(hubSiteOrigen)}</span></div>
                        <div class="popup-item"><span class="label">Destino</span><span class="value">${displayValue(destinoMostrar)}</span></div>
                        <div class="popup-item"><span class="label">Trazado</span><span class="value"><span class="route-type-pill route-type-pill--${trazadoCategoria}">${displayValue(tramo.tipo_trazado)}</span></span></div>
                        <div class="popup-item"><span class="label">Estado</span><span class="value"><span class="badge ${estadoBadge}">${displayValue(tramo.estado)}</span></span></div>
                    </div>

                    ${reservasResumenHtml}

                    <div class="popup-section-title">Composición física</div>
                    <div class="popup-metrics-grid">
                        <div class="popup-metric-card">
                            <div class="metric-val">${displayValue(distanciaTexto)}</div>
                            <div class="metric-lbl">Distancia</div>
                        </div>
                        <div class="popup-metric-card">
                            <div class="metric-val">${sumaReservasM.toFixed(1)} m</div>
                            <div class="metric-lbl">Reservas</div>
                        </div>
                    </div>

                    <div class="popup-section-title">Conectividad</div>
                    <div class="popup-metrics-grid">
                        <div class="popup-metric-card"><div class="metric-val">${displayValue(tramo.capacidad)}</div><div class="metric-lbl">Capacidad</div></div>
                        <div class="popup-metric-card"><div class="metric-val">${displayValue(tramo.tipo_fibra)}</div><div class="metric-lbl">Tipo fibra</div></div>
                        <div class="popup-metric-card"><div class="metric-val">${displayValue(tramo.marca_modelo)}</div><div class="metric-lbl">Marca / modelo</div></div>
                        <div class="popup-metric-card"><div class="metric-val">${displayValue(tramo.serial)}</div><div class="metric-lbl">Serial</div></div>
                    </div>

                    <div class="popup-toggle-row">
                        <span style="font-size:12px; font-weight:600; color:var(--text-primary);">Mostrar elementos externos de esta ruta</span>
                        <label class="toggle-switch">
                            <input class="js-route-reserves" type="checkbox" ${state.rutasMostrandoReservas.has(ruta.nombre) ? 'checked' : ''}>
                            <span class="toggle-slider"></span>
                        </label>
                    </div>
                    <div class="route-detail__actions">
                        <button class="route-detail__fit" type="button" data-route-fit>
                            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v4"/><path d="M12 18v4"/><path d="m4.93 4.93 2.83 2.83"/><path d="m16.24 16.24 2.83 2.83"/><path d="M2 12h4"/><path d="M18 12h4"/><path d="m4.93 19.07 2.83-2.83"/><path d="m16.24 7.76 2.83-2.83"/></svg>
                            Centrar
                        </button>
                        <button class="route-detail__clear" type="button" data-route-clear>
                            Limpiar selección
                        </button>
                    </div>
                </div>
            </div>
        `;
    }

    function openRoutePanel(ruta, tramo) {
        const panel = document.getElementById('draggable-route-popup');
        if (!panel) return;

        panel.innerHTML = buildPopupHtml(ruta, tramo);
        panel.classList.add('is-open');
        panel.setAttribute('aria-hidden', 'false');

        const close = panel.querySelector('[data-route-close]');
        const fit = panel.querySelector('[data-route-fit]');
        const clear = panel.querySelector('[data-route-clear]');
        const reserves = panel.querySelector('.js-route-reserves');
        if (close) close.addEventListener('click', closeRoutePanel);
        if (fit) fit.addEventListener('click', () => fitRouteBounds(ruta.nombre));
        if (clear) clear.addEventListener('click', () => clearRouteSearch({ fit: true }));
        if (reserves) {
            reserves.addEventListener('change', () => {
                window.toggleReservasRuta(ruta.nombre, reserves.checked);
            });
        }
    }

    function applyRouteFocusStyles() {
        const hasSelection = Boolean(state.selectedRuta);
        const basemapTheme = document.getElementById('map')?.dataset.basemapTheme || 'light';
        const selectionHaloColor = ['dark', 'satellite'].includes(basemapTheme)
            ? '#f8fafc'
            : '#0f172a';
        Object.keys(state.rutasPolylines).forEach((nombreRuta) => {
            const isSelected = state.selectedRuta === nombreRuta;
            const isDimmed = hasSelection && !isSelected;
            const lineStyle = isSelected
                ? { weight: 8.5, opacity: 1 }
                : isDimmed
                    ? { weight: 4, opacity: 0.13 }
                    : { weight: 5.5, opacity: 0.9 };
            const haloStyle = isSelected
                ? { color: selectionHaloColor, weight: 16, opacity: 0.82 }
                : { color: '#0ea5e9', weight: 11, opacity: 0 };

            (state.rutasHalos[nombreRuta] || []).forEach((halo) => {
                const routeIsVisible = halo._routePolyline &&
                    state.layerGroups.tramos.hasLayer(halo._routePolyline);
                if (isSelected && routeIsVisible) {
                    if (!state.layerGroups.tramos.hasLayer(halo)) {
                        halo.addTo(state.layerGroups.tramos);
                    }
                } else if (state.layerGroups.tramos.hasLayer(halo)) {
                    state.layerGroups.tramos.removeLayer(halo);
                }
                halo.setStyle(haloStyle);
                if (isSelected && halo.bringToBack) halo.bringToBack();
            });
            (state.rutasPolylines[nombreRuta] || []).forEach((polyline) => {
                polyline.setStyle({
                    ...lineStyle,
                    color: polyline._routeBaseColor
                });
                if (isSelected && polyline.bringToFront) polyline.bringToFront();
            });
        });
        updateRouteSelectionOverlay();
    }

    function updateRouteSelectionOverlay() {
        if (state.renderedSelectionRuta === state.selectedRuta) return;
        state.layerGroups.routeSelection.clearLayers();
        Object.values(state.siteMarkers).forEach((marker) => {
            marker.getElement()?.querySelector('.network-marker')?.classList.remove('is-route-endpoint');
        });
        state.renderedSelectionRuta = state.selectedRuta;
        if (!state.selectedRuta) return;

        const ruta = state.data.find((item) => item.nombre === state.selectedRuta);
        if (!ruta) return;
        const coordinates = [];
        (ruta.tramos || []).forEach((tramo) => {
            if (Array.isArray(tramo.coordenadas)) {
                tramo.coordenadas.forEach((coordinate) => {
                    if (Array.isArray(coordinate) && coordinate.length >= 2) {
                        coordinates.push(coordinate);
                    }
                });
            }
        });
        if (!coordinates.length) return;

        const meta = state.routeSearch.index.find((item) => item.ruta === ruta) || getRouteSearchMeta(ruta);
        const start = coordinates[0];
        const end = coordinates[coordinates.length - 1];
        const middle = coordinates[Math.floor(coordinates.length / 2)];
        const endpointIcon = (label) => L.divIcon({
            className: 'route-selection-endpoint-icon',
            html: `<span class="route-selection-endpoint" aria-hidden="true"></span><span class="sr-only">${label}</span>`,
            iconSize: [12, 12],
            iconAnchor: [6, 6]
        });
        const nearbySiteMarker = (coordinate) => {
            const point = L.latLng(coordinate);
            let nearest = null;
            let nearestDistance = Number.POSITIVE_INFINITY;
            state.sites.forEach((site) => {
                const lat = Number(site.lat);
                const lon = Number(site.lon);
                if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
                const distance = point.distanceTo(L.latLng(lat, lon));
                if (distance < nearestDistance) {
                    nearestDistance = distance;
                    nearest = state.siteMarkers[site.id] || null;
                }
            });
            return nearestDistance <= 35 ? nearest : null;
        };
        const addEndpoint = (coordinate, label) => {
            const siteMarker = nearbySiteMarker(coordinate);
            if (siteMarker) {
                siteMarker.getElement()?.querySelector('.network-marker')?.classList.add('is-route-endpoint');
                return;
            }
            L.marker(coordinate, {
                icon: endpointIcon(label),
                interactive: false,
                keyboard: false,
                zIndexOffset: 800
            }).addTo(state.layerGroups.routeSelection);
        };

        addEndpoint(start, 'Inicio de la troncal');
        if (String(start) !== String(end)) addEndpoint(end, 'Fin de la troncal');
        L.marker(middle, {
            icon: L.divIcon({
                className: 'route-selection-label-icon',
                html: `<span class="route-selection-label"><b>${displayValue(meta.codigo)}</b><small>Seleccionada</small></span>`,
                iconSize: [190, 42],
                iconAnchor: [95, 54]
            }),
            interactive: false,
            keyboard: false,
            zIndexOffset: 850
        }).addTo(state.layerGroups.routeSelection);
    }

    function syncTramosVisibility() {
        state.routeLayerEntries.forEach((entry) => {
            const routeIsVisible = state.mostrarTodasRutas ||
                state.selectedRuta === entry.rutaNombre;
            const shouldShow = Boolean(state.filters[entry.category]) && routeIsVisible;
            const hasPolyline = state.layerGroups.tramos.hasLayer(entry.polyline);

            if (shouldShow && !hasPolyline) {
                entry.polyline.addTo(state.layerGroups.tramos);
            } else if (!shouldShow && hasPolyline) {
                state.layerGroups.tramos.removeLayer(entry.polyline);
            }
            if (!shouldShow && state.layerGroups.tramos.hasLayer(entry.halo)) {
                state.layerGroups.tramos.removeLayer(entry.halo);
            }
        });
        applyRouteFocusStyles();
        renderReservasVisibilidad();
    }

    function renderTramos() {
        state.layerGroups.tramos.clearLayers();
        
        // Limpiamos los marcadores de reservas anteriores del mapa
        state.dataCache.reservas.forEach(item => {
            if (state.map.hasLayer(item.marker)) {
                item.marker.remove();
            }
        });
        state.dataCache.reservas = [];
        state.selectedExternalMarker = null;
        state.rutasPolylines = {};
        state.rutasHalos = {};
        state.routeLayerEntries = [];

        state.data.forEach(ruta => {
            state.rutasPolylines[ruta.nombre] = [];
            state.rutasHalos[ruta.nombre] = [];

            ruta.tramos.forEach(tramo => {
                const cat = getFilterCat(tramo.tipo_trazado);

                if (tramo.coordenadas && tramo.coordenadas.length > 0) {
                    // El halo conserva el mismo efecto, pero solo se renderiza
                    // durante hover o cuando la ruta está seleccionada.
                    const halo = L.polyline(tramo.coordenadas, {
                        color: '#0ea5e9', // Light blue halo
                        weight: 11,
                        opacity: 0,
                        lineJoin: 'round'
                    });
                    
                    const polyline = L.polyline(tramo.coordenadas, {
                        color: getColorForTrazado(tramo.tipo_trazado),
                        weight: 5.5,
                        opacity: 0.9,
                        lineJoin: 'round'
                    });
                    polyline._routeBaseColor = getColorForTrazado(tramo.tipo_trazado);
                    halo._routePolyline = polyline;

                    polyline.on('click', function(e) {
                        L.DomEvent.stopPropagation(e);
                        selectRoute(ruta, tramo, { fit: false });
                    });
                    
                    polyline.on('mouseover', function() {
                        const isDimmed = state.selectedRuta && state.selectedRuta !== ruta.nombre;
                        if (!state.layerGroups.tramos.hasLayer(halo)) {
                            halo.addTo(state.layerGroups.tramos);
                        }
                        halo.setStyle({ opacity: isDimmed ? 0.22 : 0.45 });
                        if (halo.bringToBack) halo.bringToBack();
                        this.setStyle({ weight: 7, opacity: 1 });
                    });
                    polyline.on('mouseout', function() {
                        applyRouteFocusStyles();
                    });

                    state.rutasPolylines[ruta.nombre].push(polyline);
                    state.rutasHalos[ruta.nombre].push(halo);
                    state.routeLayerEntries.push({
                        rutaNombre: ruta.nombre,
                        category: cat,
                        polyline,
                        halo
                    });
                }
            });

            // Preparar Reservas de esta ruta
            if (ruta.reservas_nodos && ruta.reservas_nodos.length > 0) {
                ruta.reservas_nodos.forEach(res => {
                    const assetType = normalizeMarkerType(res.tipo);
                    const customIcon = L.divIcon({
                        className: 'custom-network-marker-icon custom-external-icon',
                        html: buildNetworkMarkerHtml(assetType, res.nombre, { anchor: false }),
                        iconSize: [42, 70],
                        iconAnchor: [21, 21],
                        popupAnchor: [0, -22]
                    });
                    const reservaMetros = Number(res.reserva_m || 0);
                    const reservaDetalle = reservaMetros > 0
                        ? `<div style="font-size:12px; margin-bottom:4px;"><strong>Reserva de cable:</strong> ${displayValue(reservaMetros)} m</div>`
                        : '';
                    const resPopup = `
                        <div style="font-family: var(--font-sans); padding: 5px;">
                            <h4 style="margin:0 0 8px 0; color:var(--primary); font-size:14px; border-bottom:1px solid var(--border-color); padding-bottom:4px;">${displayValue(res.nombre, 'Elemento de red')}</h4>
                            <div style="font-size:12px; margin-bottom:4px;"><strong>Tipo:</strong> ${displayValue(res.tipo)}</div>
                            ${reservaDetalle}
                            <div style="font-size:12px; color:var(--text-secondary);">Pertenece a: ${displayValue(ruta.nombre)}</div>
                        </div>
                    `;
                    
                    const marker = L.marker([res.lat, res.lon], {
                        icon: customIcon,
                        title: `${res.tipo || 'Elemento'}: ${res.nombre || 'Sin nombre'}`,
                        riseOnHover: true,
                        zIndexOffset: 500
                    })
                        .bindPopup(resPopup);

                    marker.on('popupopen', () => {
                        if (state.selectedExternalMarker && state.selectedExternalMarker !== marker) {
                            state.selectedExternalMarker.getElement()?.querySelector('.network-marker')?.classList.remove('is-selected');
                        }
                        state.selectedExternalMarker = marker;
                        marker.getElement()?.querySelector('.network-marker')?.classList.add('is-selected');
                    });
                    marker.on('popupclose', () => {
                        marker.getElement()?.querySelector('.network-marker')?.classList.remove('is-selected');
                        if (state.selectedExternalMarker === marker) state.selectedExternalMarker = null;
                    });

                    // Solo guardamos en caché en vez de añadir al mapa directamente
                    state.dataCache.reservas.push({
                        marker,
                        rutaNombre: ruta.nombre,
                        tipo: assetType
                    });
                });
            }
        });
        
        renderSiteMarkers();
        syncTramosVisibility();
    }

    function renderSiteMarkers() {
        state.layerGroups.sites.clearLayers();
        state.siteMarkers = {};
        state.sites.forEach((site) => {
            if (!Number.isFinite(Number(site.lat)) || !Number.isFinite(Number(site.lon))) return;
            const icon = L.divIcon({
                className: 'custom-network-marker-icon custom-site-icon',
                html: buildNetworkMarkerHtml('site', site.nombre, { anchor: true, site: true }),
                iconSize: [44, 72],
                iconAnchor: [22, 22]
            });
            const marker = L.marker([Number(site.lat), Number(site.lon)], {
                icon,
                title: String(site.nombre || 'Site'),
                riseOnHover: true
            });
            marker.on('click', (event) => {
                L.DomEvent.stopPropagation(event);
                closeRoutePanel();
                openSiteNavigation(site.id, site.nombre);
            });
            marker.addTo(state.layerGroups.sites);
            state.siteMarkers[site.id] = marker;
        });
        state.renderedSelectionRuta = null;
    }

    function fitBoundsToData() {
        const bounds = L.latLngBounds([]);
        let hasPoints = false;
        state.data.forEach(ruta => {
            ruta.tramos.forEach(tramo => {
                if (tramo.coordenadas && tramo.coordenadas.length > 0) {
                    tramo.coordenadas.forEach(coord => {
                        bounds.extend(coord);
                        hasPoints = true;
                    });
                }
            });
        });
        state.sites.forEach((site) => {
            if (Number.isFinite(Number(site.lat)) && Number.isFinite(Number(site.lon))) {
                bounds.extend([Number(site.lat), Number(site.lon)]);
                hasPoints = true;
            }
        });
        
        if (hasPoints && bounds.isValid()) {
            state.map.fitBounds(bounds, { padding: [50, 50] });
        }
    }

    // Inicializar cuando el DOM esté listo
    $(document).ready(function() {
        initMap();
    });

    // --- Lógica del Panel Lateral SaaS de Puertos ODF ---
    window.closeOdfPanel = function() {
        const panel = document.getElementById('odf-side-panel');
        if (panel) panel.classList.remove('open');
    };

    window.abrirPanelODF = function(hubSite, odfNombre) {
        const panel = document.getElementById('odf-side-panel');
        if (!panel) return;

        // UI Reset
        document.getElementById('sp-odf-name').innerText = odfNombre;
        document.getElementById('sp-hub-name').innerText = hubSite;
        
        const troncalSpan = document.getElementById('sp-troncal-name');
        if (troncalSpan) {
            troncalSpan.innerText = 'Troncal: Cargando...';
        }
        
        document.getElementById('sp-loading').style.display = 'flex';
        document.getElementById('sp-content').style.display = 'none';
        
        panel.classList.add('open');

        let basePath = '';
        if (window.location.pathname.includes('/fg-6/')) {
            basePath = '/fg-6';
        }

        $.ajax({
            url: basePath + '/api/inventario/puertos-odf-mapa/',
            data: {
                'hub_site': hubSite,
                'odf_nombre': odfNombre
            },
            method: 'GET',
            success: function(res) {
                document.getElementById('sp-loading').style.display = 'none';
                
                if (res.status === 'success') {
                    document.getElementById('sp-content').style.display = 'block';
                    document.getElementById('sp-total-ports').innerText = `Total: ${res.capacidad}`;
                    
                    const troncalSpan = document.getElementById('sp-troncal-name');
                    if (troncalSpan) {
                        troncalSpan.innerText = `Troncal: ${res.troncal || 'N/A'}`;
                    }
                    
                    const grid = document.getElementById('sp-ports-grid');
                    grid.innerHTML = '';
                    
                    let ocupados = 0;
                    let libres = 0;
                    let reservados = 0;
                    
                    res.puertos.forEach(p => {
                        const isOcupado = p.estado && p.estado.toLowerCase().includes('ocupado');
                        const isReservado = p.estado && p.estado.toLowerCase().includes('reservado');
                        if (isOcupado) ocupados++;
                        else if (isReservado) reservados++;
                        else libres++;
                        
                        const boxClass = isOcupado ? 'ocupado' : (isReservado ? 'reservado' : 'libre');
                        const box = document.createElement('div');
                        box.className = `port-box ${boxClass}`;
                        box.innerText = p.puerto;
                        
                        box.addEventListener('mouseenter', (e) => showPortTooltip(e, p, isOcupado || isReservado));
                        box.addEventListener('mousemove', (e) => movePortTooltip(e));
                        box.addEventListener('mouseleave', () => hidePortTooltip());
                        
                        box.addEventListener('click', () => {
                            // Remover selección previa
                            document.querySelectorAll('.port-box').forEach(el => el.style.border = '');
                            // Seleccionar actual
                            box.style.border = '2px solid var(--primary)';
                            
                            if (troncalSpan) {
                                troncalSpan.innerText = `Troncal: ${p.ruta_troncal || 'N/A'}`;
                            }
                        });
                        
                        grid.appendChild(box);
                    });
                    
                    document.getElementById('sp-count-ocupados').innerText = ocupados;
                    document.getElementById('sp-count-libres').innerText = libres;
                    document.getElementById('sp-count-reservados').innerText = reservados;
                    
                } else {
                    document.getElementById('sp-content').style.display = 'block';
                    setPortsGridMessage(res.message || 'No se encontraron puertos', false);
                    document.getElementById('sp-count-ocupados').innerText = '0';
                    document.getElementById('sp-count-libres').innerText = '0';
                    document.getElementById('sp-count-reservados').innerText = '0';
                    document.getElementById('sp-total-ports').innerText = 'Total: 0';
                }
            },
            error: function() {
                document.getElementById('sp-loading').style.display = 'none';
                document.getElementById('sp-content').style.display = 'block';
                setPortsGridMessage('Error al cargar datos del servidor', true);
            }
        });
    };

    function setPortsGridMessage(message, isError) {
        const grid = document.getElementById('sp-ports-grid');
        if (!grid) return;
        const notice = document.createElement('div');
        notice.style.gridColumn = '1 / -1';
        notice.style.color = isError ? 'var(--text-danger)' : 'var(--text-secondary)';
        notice.style.textAlign = 'center';
        notice.style.padding = '20px';
        notice.textContent = message;
        grid.replaceChildren(notice);
    }

    function showPortTooltip(e, portData, isOcupado) {
        const tooltip = document.getElementById('port-tooltip');
        if (!tooltip) return;
        
        let html = `<div style="font-weight: 700; margin-bottom: 4px;">Puerto ${displayValue(portData.puerto)}</div>`;
        html += `<div><strong>Estado:</strong> ${displayValue(portData.estado, 'Libre')}</div>`;
        if (isOcupado) {
            html += `<div><strong>Destino:</strong> <span style="color:var(--primary); font-weight:600;">${displayValue(portData.destino)}</span></div>`;
            if (portData.fibra) html += `<div><strong>Fibra:</strong> ${displayValue(portData.fibra)}</div>`;
        }
        if (portData.tipo_conector) html += `<div><strong>Conector:</strong> ${displayValue(portData.tipo_conector)}</div>`;
        
        tooltip.innerHTML = html;
        tooltip.style.opacity = '1';
        movePortTooltip(e);
    }
    
    function movePortTooltip(e) {
        const tooltip = document.getElementById('port-tooltip');
        if (!tooltip) return;
        tooltip.style.left = (e.pageX - 150) + 'px'; // Desplazar un poco a la izquierda para que no choque con el cursor
        tooltip.style.top = (e.pageY + 15) + 'px';
    }
    
    function hidePortTooltip() {
        const tooltip = document.getElementById('port-tooltip');
        if (tooltip) tooltip.style.opacity = '0';
    }

    // Navegación contextual Site -> ODF -> Puertos. Los niveles se consultan
    // de forma progresiva para no cargar miles de puertos junto con el mapa.
    const networkNumber = new Intl.NumberFormat('es-PE');

    function networkElement(id) {
        return document.getElementById(id);
    }

    function setupNetworkNavigation() {
        const panel = networkElement('network-nav-panel');
        if (!panel) return;

        networkElement('network-nav-close').addEventListener('click', closeNetworkNavigation);
        panel.addEventListener('click', (event) => {
            const link = event.target.closest('[data-open-asset-360]');
            if (!link || !panel.contains(link) || link.getAttribute('href') === '#') return;

            const openAsset360 = window.FiberGenius && window.FiberGenius.openAsset360;
            if (typeof openAsset360 !== 'function') return;

            event.preventDefault();
            openAsset360(link.href);
        });
        networkElement('network-tab-summary').addEventListener('click', () => setOdfTab('summary'));
        networkElement('network-tab-ports').addEventListener('click', () => setOdfTab('ports'));
        networkElement('network-port-prev').addEventListener('click', () => {
            if (state.navigation.portPage > 1) {
                loadOdfPorts(state.navigation.odf, state.navigation.portPage - 1);
            }
        });
        networkElement('network-port-next').addEventListener('click', () => {
            loadOdfPorts(state.navigation.odf, state.navigation.portPage + 1);
        });

        L.DomEvent.disableClickPropagation(panel);
        L.DomEvent.disableScrollPropagation(panel);
    }

    function closeNetworkNavigation() {
        const panel = networkElement('network-nav-panel');
        if (!panel) return;
        if (state.navigation.portAbortController) {
            state.navigation.portAbortController.abort();
            state.navigation.portAbortController = null;
        }
        panel.classList.remove('is-open');
        panel.setAttribute('aria-hidden', 'true');
        Object.values(state.siteMarkers).forEach((marker) => {
            const element = marker.getElement();
            if (element) element.querySelector('.site-marker')?.classList.remove('is-selected');
        });
    }

    function showNetworkLoading(message) {
        networkElement('network-nav-loading').hidden = false;
        const label = networkElement('network-nav-loading').querySelector('strong');
        if (label) label.textContent = message || 'Cargando inventario…';
        networkElement('network-nav-error').hidden = true;
        networkElement('network-site-view').hidden = true;
        networkElement('network-odf-view').hidden = true;
    }

    function showNetworkError(message) {
        networkElement('network-nav-loading').hidden = true;
        networkElement('network-site-view').hidden = true;
        networkElement('network-odf-view').hidden = true;
        const error = networkElement('network-nav-error');
        error.textContent = message || 'No fue posible consultar el inventario.';
        error.hidden = false;
    }

    function setNetworkBreadcrumb(level) {
        const breadcrumb = networkElement('network-nav-breadcrumb');
        breadcrumb.replaceChildren();
        const payload = state.navigation.sitePayload;
        if (!payload) return;

        const siteButton = document.createElement('button');
        siteButton.type = 'button';
        siteButton.textContent = payload.site.nombre;
        siteButton.addEventListener('click', () => renderSiteView(payload));
        breadcrumb.appendChild(siteButton);

        if (level === 'odf' && state.navigation.odf) {
            const separator = document.createElement('span');
            separator.textContent = '›';
            breadcrumb.appendChild(separator);
            const current = document.createElement('strong');
            current.textContent = state.navigation.odf.odf;
            breadcrumb.appendChild(current);
        }
    }

    async function openSiteNavigation(siteId, siteName) {
        const panel = networkElement('network-nav-panel');
        if (!panel) return;
        panel.classList.add('is-open');
        panel.setAttribute('aria-hidden', 'false');
        showNetworkLoading('Cargando ' + (siteName || 'Site') + '…');

        Object.values(state.siteMarkers).forEach((marker) => {
            const element = marker.getElement();
            if (element) element.querySelector('.site-marker')?.classList.remove('is-selected');
        });
        const selected = state.siteMarkers[siteId]?.getElement();
        if (selected) selected.querySelector('.site-marker')?.classList.add('is-selected');

        const template = panel.dataset.siteUrlTemplate;
        const url = template.replace(/\/0\/?$/, '/' + encodeURIComponent(siteId) + '/');
        try {
            const response = await fetch(url, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                credentials: 'same-origin'
            });
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const payload = await response.json();
            if (payload.status !== 'success') throw new Error(payload.message || 'Respuesta no válida');
            state.navigation.sitePayload = payload;
            state.navigation.odf = null;
            state.navigation.loadedOdfId = null;
            renderSiteView(payload);
        } catch (error) {
            if (error.name !== 'AbortError') {
                showNetworkError('No se pudo cargar el Site. Verifica tus permisos e inténtalo nuevamente.');
            }
        }
    }

    function renderSiteView(payload) {
        state.navigation.sitePayload = payload;
        state.navigation.odf = null;
        networkElement('network-nav-loading').hidden = true;
        networkElement('network-nav-error').hidden = true;
        networkElement('network-odf-view').hidden = true;
        networkElement('network-site-view').hidden = false;
        setNetworkBreadcrumb('site');

        const site = payload.site;
        const summary = payload.summary;
        networkElement('network-site-name').textContent = site.nombre;
        const metaParts = [];
        if (site.direccion && site.direccion !== '—') metaParts.push(site.direccion);
        metaParts.push(networkNumber.format(site.salas) + ' salas');
        metaParts.push(networkNumber.format(site.racks) + ' racks');
        metaParts.push(networkNumber.format(site.troncales) + ' troncales');
        networkElement('network-site-meta').textContent = metaParts.join(' · ');
        networkElement('network-site-odfs').textContent = networkNumber.format(summary.total);
        networkElement('network-site-capacity').textContent = networkNumber.format(summary.capacidad);
        networkElement('network-site-free').textContent = networkNumber.format(summary.libres);
        networkElement('network-site-reserved').textContent = networkNumber.format(summary.reservados);
        networkElement('network-site-odf-count').textContent = networkNumber.format(summary.total);
        networkElement('network-odf-truncated').hidden = !payload.truncated;
        networkElement('network-site-360').href = site.detail_url;
        networkElement('network-site-inventory').href = site.odfs_url;

        const list = networkElement('network-odf-list');
        list.replaceChildren();
        if (!payload.odfs.length) {
            const empty = document.createElement('div');
            empty.className = 'network-nav__empty';
            empty.textContent = 'Este Site todavía no tiene ODF asociados.';
            list.appendChild(empty);
            return;
        }

        payload.odfs.forEach((odf) => {
            const card = document.createElement('button');
            card.type = 'button';
            card.className = 'network-odf-card';
            card.setAttribute('aria-label', 'Abrir ODF ' + odf.odf);
            card.innerHTML =
                '<span class="network-odf-card__head"><span class="network-odf-card__icon" aria-hidden="true">' +
                NETWORK_MARKER_ICONS.odf + '</span><strong>' + displayValue(odf.odf) + '</strong>' +
                '<span>' + displayValue(odf.sala) + ' · ' + displayValue(odf.rack) + ' ›</span></span>' +
                '<span class="network-odf-card__counts">' +
                '<span><b>' + networkNumber.format(odf.ocupados) + '</b>Ocupados</span>' +
                '<span><b>' + networkNumber.format(odf.libres) + '</b>Libres</span>' +
                '<span><b>' + networkNumber.format(odf.reservados) + '</b>Reservados</span>' +
                '</span>';
            card.addEventListener('click', () => openOdfNavigation(odf));
            list.appendChild(card);
        });
    }

    function networkPercent(value, total) {
        if (!total) return 0;
        return Math.min(100, Math.max(0, (Number(value || 0) / Number(total)) * 100));
    }

    function openOdfNavigation(odf) {
        state.navigation.odf = odf;
        state.navigation.loadedOdfId = null;
        state.navigation.portPage = 1;
        networkElement('network-site-view').hidden = true;
        networkElement('network-odf-view').hidden = false;
        setNetworkBreadcrumb('odf');

        const accounted = Number(odf.ocupados || 0) + Number(odf.libres || 0) + Number(odf.reservados || 0);
        const capacity = Math.max(Number(odf.capacidad || 0), accounted);
        const usedPercent = networkPercent(odf.ocupados, capacity);
        networkElement('network-odf-name').textContent = odf.odf;
        networkElement('network-odf-location').textContent =
            odf.site + ' → ' + odf.sala + ' → ' + odf.rack;
        networkElement('network-odf-status').textContent = odf.estado || 'Sin estado';
        networkElement('network-odf-capacity').textContent = networkNumber.format(capacity) + ' puertos';
        networkElement('network-odf-used-label').textContent = networkNumber.format(odf.ocupados);
        networkElement('network-odf-free-label').textContent = networkNumber.format(odf.libres);
        networkElement('network-odf-reserved-label').textContent = networkNumber.format(odf.reservados);
        networkElement('network-odf-used-progress').value = usedPercent;
        networkElement('network-odf-free-progress').value = networkPercent(odf.libres, capacity);
        networkElement('network-odf-reserved-progress').value = networkPercent(odf.reservados, capacity);
        networkElement('network-odf-connector').textContent = odf.conector || '—';
        networkElement('network-odf-utilization').textContent = usedPercent.toFixed(1) + '%';
        networkElement('network-odf-360').href = odf.detail_url;
        networkElement('network-odf-full-ports').href = odf.ports_url;
        networkElement('network-odf-fibers').href = odf.fibers_url;
        networkElement('network-odf-fibers').hidden = !state.navigation.sitePayload.permissions.view_fibers;
        networkElement('network-tab-ports').hidden = !state.navigation.sitePayload.permissions.view_ports;
        networkElement('network-odf-full-ports').hidden = !state.navigation.sitePayload.permissions.view_ports;
        setOdfTab('summary');
    }

    function setOdfTab(tab) {
        if (tab === 'ports' && state.navigation.sitePayload &&
                !state.navigation.sitePayload.permissions.view_ports) {
            return;
        }
        const isPorts = tab === 'ports';
        const summaryButton = networkElement('network-tab-summary');
        const portsButton = networkElement('network-tab-ports');
        summaryButton.classList.toggle('is-active', !isPorts);
        portsButton.classList.toggle('is-active', isPorts);
        summaryButton.setAttribute('aria-selected', String(!isPorts));
        portsButton.setAttribute('aria-selected', String(isPorts));
        networkElement('network-odf-summary').hidden = isPorts;
        networkElement('network-odf-ports').hidden = !isPorts;
        if (isPorts && state.navigation.odf &&
                state.navigation.loadedOdfId !== state.navigation.odf.id) {
            loadOdfPorts(state.navigation.odf, 1);
        }
    }

    function setPortGridMessage(message, isError) {
        const grid = networkElement('network-port-grid');
        const empty = document.createElement('div');
        empty.className = 'network-nav__empty';
        if (isError) empty.classList.add('network-nav__empty--error');
        empty.textContent = message;
        grid.replaceChildren(empty);
    }

    async function loadOdfPorts(odf, page) {
        if (!odf || page < 1) return;
        if (state.navigation.portAbortController) {
            state.navigation.portAbortController.abort();
        }
        const controller = new AbortController();
        state.navigation.portAbortController = controller;
        setPortGridMessage('Cargando puertos…', false);
        networkElement('network-port-detail').hidden = true;

        const endpoint = networkElement('network-nav-panel').dataset.portsUrl;
        const url = new URL(endpoint, window.location.origin);
        url.searchParams.set('odf_id', odf.id);
        url.searchParams.set('page', page);
        url.searchParams.set('page_size', 48);
        try {
            const response = await fetch(url.toString(), {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                credentials: 'same-origin',
                signal: controller.signal
            });
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const payload = await response.json();
            if (payload.status !== 'success') throw new Error(payload.message || 'Respuesta no válida');
            state.navigation.portPage = payload.pagination.page;
            state.navigation.loadedOdfId = odf.id;
            renderOdfPorts(payload);
        } catch (error) {
            if (error.name !== 'AbortError') {
                setPortGridMessage('No se pudieron cargar los puertos. Inténtalo nuevamente.', true);
            }
        } finally {
            if (state.navigation.portAbortController === controller) {
                state.navigation.portAbortController = null;
            }
        }
    }

    function renderOdfPorts(payload) {
        const summary = payload.summary;
        const pagination = payload.pagination;
        networkElement('network-ports-used').textContent = networkNumber.format(summary.ocupados);
        networkElement('network-ports-free').textContent = networkNumber.format(summary.libres);
        networkElement('network-ports-reserved').textContent = networkNumber.format(summary.reservados);
        networkElement('network-port-total').textContent = networkNumber.format(pagination.total);
        networkElement('network-port-page').textContent =
            'Página ' + networkNumber.format(pagination.page) + ' de ' + networkNumber.format(pagination.total_pages);
        networkElement('network-port-prev').disabled = !pagination.has_previous;
        networkElement('network-port-next').disabled = !pagination.has_next;

        const start = pagination.total ? ((pagination.page - 1) * pagination.page_size) + 1 : 0;
        const end = Math.min(pagination.total, pagination.page * pagination.page_size);
        networkElement('network-port-range').textContent =
            pagination.total ? 'Puertos ' + start + '–' + end : 'Puertos';

        const grid = networkElement('network-port-grid');
        grid.replaceChildren();
        if (!payload.data.length) {
            setPortGridMessage('Este ODF no tiene puertos cargados.', false);
            return;
        }

        payload.data.forEach((port) => {
            const button = document.createElement('button');
            button.type = 'button';
            const status = String(port.estado || '').toLowerCase();
            const stateClass = status.includes('reserv') ? 'is-reserved'
                : status.includes('ocup') ? 'is-used' : 'is-free';
            button.className = 'network-port ' + stateClass;
            button.textContent = port.puerto || String(port.id);
            button.title = 'Puerto ' + (port.puerto || port.id) + ' · ' + (port.estado || 'Libre');
            button.addEventListener('click', () => {
                grid.querySelectorAll('.network-port').forEach((item) => item.classList.remove('is-selected'));
                button.classList.add('is-selected');
                renderPortDetail(port);
            });
            grid.appendChild(button);
        });
    }

    function renderPortDetail(port) {
        const detail = networkElement('network-port-detail');
        detail.innerHTML =
            '<h4>Puerto ' + displayValue(port.puerto, port.id) + '</h4>' +
            '<dl>' +
            '<dt>Estado</dt><dd>' + displayValue(port.estado, 'Libre') + '</dd>' +
            '<dt>Fibra</dt><dd>' + displayValue(port.fibra) + '</dd>' +
            '<dt>Destino</dt><dd>' + displayValue(port.destino) + '</dd>' +
            '<dt>Conector</dt><dd>' + displayValue(port.conector) + '</dd>' +
            '<dt>Patchcord</dt><dd>' + displayValue(port.patchcord) + '</dd>' +
            '</dl><a data-open-asset-360 href="' + displayValue(port.detail_url, '#') + '">Abrir ficha 360° →</a>';
        detail.hidden = false;
    }

    // Compatibilidad con los accesos existentes desde los popups de troncales.
    window.closeOdfPanel = closeNetworkNavigation;
    window.abrirPanelODF = async function(hubSite, odfNombre) {
        const site = state.sites.find((item) => item.nombre === hubSite);
        if (!site) return;
        await openSiteNavigation(site.id, site.nombre);
        const odf = state.navigation.sitePayload?.odfs.find((item) => item.odf === odfNombre);
        if (odf) openOdfNavigation(odf);
    };

})();
