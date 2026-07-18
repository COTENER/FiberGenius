(function() {
    const state = {
        map: null,
        layerGroups: {
            tramos: L.layerGroup()
        },
        tileLayer: null,
        data: [],
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
        selectedRuta: null
    };

    function initMap() {
        const mapElement = document.getElementById('map');
        state.map = L.map('map', {
            zoomControl: false,
            attributionControl: false
        }).setView([-12.046374, -77.042793], 6); // Centro general de Perú como default

        L.control.zoom({ position: 'topright' }).addTo(state.map);

        state.layerGroups.tramos.addTo(state.map);

        const attribution = mapElement.dataset.tileAttribution || 'Cartografía configurable';
        const createLayer = (url) => url ? L.tileLayer(url, { maxZoom: 19, attribution }) : null;
        const mapClaro = createLayer(mapElement.dataset.tileLight);
        const mapOscuro = createLayer(mapElement.dataset.tileDark);
        const satelite = createLayer(mapElement.dataset.tileSatellite);
        const baseMaps = {};
        if (mapClaro) baseMaps['Mapa claro'] = mapClaro;
        if (mapOscuro) baseMaps['Mapa oscuro'] = mapOscuro;
        if (satelite) baseMaps['Satélite'] = satelite;

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
        if (preferredLayer) preferredLayer.addTo(state.map);
        else showOfflineNotice();

        if (Object.keys(baseMaps).length) {
            L.control.layers(baseMaps, null, { position: 'topright', collapsed: false }).addTo(state.map);
        }

        setupFullscreenControl();
        setupFilters();
        setupSearch();
        cargarDatosInventario();

        // Cerrar popup draggable al hacer click en el mapa
        state.map.on('click', function(e) {
            // Evitar cerrar si el clic se originó dentro del popup (por si Leaflet lo captura globalmente)
            if (e.originalEvent && e.originalEvent.target && e.originalEvent.target.closest('#draggable-route-popup')) {
                return;
            }
            const panel = document.getElementById('draggable-route-popup');
            if (panel) panel.style.display = 'none';
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

    function setupFilters() {
        const updateFilters = () => {
            state.filters['AEREO'] = document.getElementById('chk-aereo').checked;
            state.filters['SOTERRADO'] = document.getElementById('chk-soterrado').checked;
            state.filters['HIBRIDO'] = document.getElementById('chk-hibrido') ? document.getElementById('chk-hibrido').checked : true;
            state.filters['DESCONOCIDO'] = document.getElementById('chk-desconocido').checked;
            renderTramos();
        };

        $('#chk-aereo, #chk-soterrado, #chk-hibrido, #chk-desconocido').on('change', updateFilters);

        $('#chk-reservas-global').on('change', function() {
            state.mostrarTodasReservas = this.checked;
            renderReservasVisibilidad();
        });

        $('#chk-rutas-global').on('change', function() {
            state.mostrarTodasRutas = this.checked;
            if (this.checked) state.selectedRuta = null; // Al activar "todas", limpiamos seleccion individual
            renderTramos();
        });
    }


    function setupSearch() {
        const input = document.getElementById('route-search-input');
        const btn = document.getElementById('search-btn');
        const clearBtn = document.getElementById('clear-search-btn');
        if (!input || !btn || !clearBtn) return;

        btn.addEventListener('click', buscarYResaltarRuta);
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') buscarYResaltarRuta();
        });

        input.addEventListener('input', () => {
            clearBtn.style.display = input.value ? 'flex' : 'none';
        });

        clearBtn.addEventListener('click', () => {
            input.value = '';
            clearBtn.style.display = 'none';
            state.selectedRuta = null;
            renderTramos();
            fitBoundsToData();
        });
    }

    function buscarYResaltarRuta() {
        const query = document.getElementById('route-search-input').value.trim();
        
        if (!query) {
            // Si el buscador está vacío, restauramos el estado de "mostrar todas" si el toggle está activo
            state.selectedRuta = null;
            renderTramos();
            return;
        }

        // Primero verificamos si la ruta existe en los datos cargados
        const rutaExiste = state.data.some(r => r.nombre === query);
        
        if (rutaExiste) {
            state.selectedRuta = query;
            
            // 1. Re-renderizar tramos para que SOLO se vea esta ruta en el mapa
            renderTramos();

            // 2. Obtener las NUEVAS referencias de polylines y halos después del render
            const polylines = state.rutasPolylines[query];
            const halos = state.rutasHalos[query];

            if (polylines && polylines.length > 0) {
                const bounds = L.latLngBounds([]);

                // 3. Resaltar los segmentos
                halos.forEach(h => h.setStyle({ opacity: 0.6 }));
                polylines.forEach(p => {
                    p.setStyle({ weight: 9, opacity: 1 });
                    bounds.extend(p.getBounds());
                });

                // 4. Volar a la ruta
                state.map.flyToBounds(bounds, { padding: [100, 100], maxZoom: 16 });

                // 5. Abrir popup del primer tramo después de la animación de vuelo
                setTimeout(() => {
                    if (polylines[0]) polylines[0].openPopup();
                }, 800);
            }
        } else {
            if (window.FiberGenius && window.FiberGenius.notify) {
                window.FiberGenius.notify('Troncal no encontrada en el mapa.', 'warning');
            }
        }
    }

    function limpiarResaltadoRuta() {
        if (state.selectedRuta && state.rutasPolylines[state.selectedRuta]) {
            state.rutasHalos[state.selectedRuta].forEach(h => h.setStyle({ opacity: 0 }));
            state.rutasPolylines[state.selectedRuta].forEach(p => p.setStyle({ weight: 6, opacity: 0.9 }));
        }
        state.selectedRuta = null;
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
        
        $.ajax({
            url: basePath + '/api/inventario/datos/',
            method: 'GET',
            success: function(response) {
                if(response.status === 'success') {
                    state.data = response.data;
                    renderTramos();
                    fitBoundsToData();
                    updateSummaryCards();

                    // Verificar si venimos redirigidos desde el Dashboard con una ruta específica
                    const urlParams = new URLSearchParams(window.location.search);
                    const rutaUrl = urlParams.get('ruta');
                    if (rutaUrl) {
                        const input = document.getElementById('route-search-input');
                        const chkAll = document.getElementById('chk-rutas-global');
                        if (input) {
                            input.value = rutaUrl;
                            state.mostrarTodasRutas = false; // Desactivar "Mostrar Todas" para aislar
                            if (chkAll) {
                                chkAll.checked = false;
                                chkAll.dispatchEvent(new Event('change'));
                            }
                            buscarYResaltarRuta();
                        }
                    }
                }
            },
            error: function(err) {
                console.error("Error cargando inventario:", err);
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
            
            // Analizar todos los tramos de la ruta para clasificarla
            ruta.tramos.forEach(tramo => {
                let tipo = (tramo.tipo_trazado || '').toUpperCase().trim();
                if (tipo === 'AEREO') hasAereo = true;
                if (tipo === 'SOTERRADO') hasSoterrado = true;
            });

            if (hasAereo && hasSoterrado) hibridas++;
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
        if (tipo === 'SOTERRADO') return '#ef4444'; // RED 500
        if (tipo === 'HIBRIDO') return '#f59e0b'; // AMBER 500
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

    function buildPopupHtml(ruta, tramo) {
        let estadoBadge = 'badge--info';
        if (tramo.estado) {
            const eMay = tramo.estado.toUpperCase();
            if (eMay.includes('OPERATIV')) estadoBadge = 'badge--success';
            if (eMay.includes('MANTENIMIENTO')) estadoBadge = 'badge--warning';
            if (eMay.includes('CAIDO') || eMay.includes('CORTE')) estadoBadge = 'badge--danger';
        }

        let trazadoBadge = 'badge--info'; // Default blue (Aéreo)
        if (tramo.tipo_trazado) {
            const tMay = tramo.tipo_trazado.toUpperCase();
            if (tMay.includes('SOTERRADO')) trazadoBadge = 'badge--danger'; // Red
            else if (tMay.includes('AEREO')) trazadoBadge = 'badge--info'; // Blue
            else if (tMay.includes('HIBRIDO')) trazadoBadge = 'badge--warning'; // Yellow
        }

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
                reservasResumenHtml += `<div class="popup-item"><span class="label">${tipo}</span><span class="value">${conteoReservas[tipo]}</span></div>`;
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
            <div class="popup-inventario" style="position:relative;">
                <button onclick="document.getElementById('draggable-route-popup').style.display='none'" style="position:absolute; top:12px; right:12px; background:none; border:none; cursor:pointer; color:var(--text-secondary); padding:4px; border-radius:4px;" onmouseover="this.style.background='var(--bg-surface-2)'" onmouseout="this.style.background='none'">
                    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
                </button>
                <h3 style="padding-right: 30px;">${ruta.nombre}</h3>
                
                <div class="popup-section-title">Información General</div>
                <div class="popup-grid-2col">
                    <div class="popup-item"><span class="label">Site de origen</span><span class="value">${hubSiteOrigen}</span></div>
                    <div class="popup-item"><span class="label">Destino</span><span class="value">${destinoMostrar}</span></div>
                    <div class="popup-item"><span class="label">Trazado</span><span class="value"><span class="badge ${trazadoBadge}" style="font-weight: 700; text-transform: uppercase;">${tramo.tipo_trazado}</span></span></div>
                    <div class="popup-item"><span class="label">Estado</span><span class="value"><span class="badge ${estadoBadge}">${tramo.estado}</span></span></div>
                </div>

                ${reservasResumenHtml}

                <div class="popup-section-title">Composición Física</div>
                <div class="popup-metrics-grid" style="grid-template-columns: 1fr 1fr;">
                    <div class="popup-metric-card">
                        <div class="metric-val">${distanciaTexto}</div>
                        <div class="metric-lbl">Distancia</div>
                    </div>
                    <div class="popup-metric-card">
                        <div class="metric-val">${sumaReservasM.toFixed(1)} m</div>
                        <div class="metric-lbl">Reservas</div>
                    </div>
                </div>

                <div class="popup-section-title">Conectividad</div>
                <div class="popup-metrics-grid">
                    <div class="popup-metric-card">
                        <div class="metric-val">${tramo.capacidad || 'N/A'}</div>
                        <div class="metric-lbl">Capacidad</div>
                    </div>
                    <div class="popup-metric-card">
                        <div class="metric-val">${tramo.tipo_fibra || 'N/A'}</div>
                        <div class="metric-lbl">Tipo Fibra</div>
                    </div>
                    <div class="popup-metric-card">
                        <div class="metric-val" style="font-size:11px;">${tramo.marca_modelo || 'N/A'}</div>
                        <div class="metric-lbl">Marca/Mod</div>
                    </div>
                    <div class="popup-metric-card">
                        <div class="metric-val" style="font-size:11px;">${tramo.serial || 'N/A'}</div>
                        <div class="metric-lbl">Serial</div>
                    </div>
                </div>
                
                <div class="popup-toggle-row">
                    <span style="font-size:12px; font-weight:600; color:var(--text-primary);">Mostrar reservas de esta ruta</span>
                    <label class="toggle-switch">
                        <input type="checkbox" onchange="window.toggleReservasRuta('${ruta.nombre}', this.checked)" ${state.rutasMostrandoReservas.has(ruta.nombre) ? 'checked' : ''}>
                        <span class="toggle-slider"></span>
                    </label>
                </div>
            </div>
        `;
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
        state.rutasPolylines = {};
        state.rutasHalos = {};

        const datalist = document.getElementById('routes-datalist');
        if (datalist) datalist.innerHTML = '';

        state.data.forEach(ruta => {
            // Lógica de visibilidad: 
            // Mostramos la ruta si:
            // 1. Es la ruta seleccionada específicamente por el buscador.
            // 2. No hay ninguna ruta seleccionada y el toggle de "Mostrar Todas las Rutas" está activado.
            const debeMostrarRuta = (state.selectedRuta === ruta.nombre) || (state.selectedRuta === null && state.mostrarTodasRutas);

            if (datalist) {
                const option = document.createElement('option');
                option.value = ruta.nombre;
                datalist.appendChild(option);
            }

            state.rutasPolylines[ruta.nombre] = [];
            state.rutasHalos[ruta.nombre] = [];

            ruta.tramos.forEach(tramo => {
                const cat = getFilterCat(tramo.tipo_trazado);
                
                // Si la categoría está filtrada por los checkboxes de trazado, no la procesamos
                if (!state.filters[cat]) return;
                
                if (tramo.coordenadas && tramo.coordenadas.length > 0) {
                    // Contorno/Perímetro (Halo) invisible inicialmente
                    const halo = L.polyline(tramo.coordenadas, {
                        color: '#0ea5e9', // Light blue halo
                        weight: 12,
                        opacity: 0,
                        lineJoin: 'round'
                    });
                    
                    const polyline = L.polyline(tramo.coordenadas, {
                        color: getColorForTrazado(tramo.tipo_trazado),
                        weight: 6, // Más ancha
                        opacity: 0.9,
                        lineJoin: 'round'
                    });

                    polyline.on('click', function(e) {
                        L.DomEvent.stopPropagation(e);
                        const panel = document.getElementById('draggable-route-popup');
                        if (panel) {
                            panel.innerHTML = buildPopupHtml(ruta, tramo);
                            panel.style.display = 'block';
                            // Reset position to right-middle
                            panel.style.top = '50%';
                            panel.style.right = '20px';
                            panel.style.left = 'auto';
                            panel.style.transform = 'translateY(-50%)';
                            makeDraggable(panel);
                        }
                    });
                    
                    polyline.on('mouseover', function(e) {
                        halo.setStyle({ opacity: 0.4 });
                        this.setStyle({ weight: 7, opacity: 1 });
                    });
                    polyline.on('mouseout', function(e) {
                        halo.setStyle({ opacity: 0 });
                        this.setStyle({ weight: 6, opacity: 0.9 });
                    });

                    // SOLO añadimos al mapa si cumple la condición de visibilidad
                    if (debeMostrarRuta) {
                        halo.addTo(state.layerGroups.tramos);
                        polyline.addTo(state.layerGroups.tramos);
                    }

                    state.rutasPolylines[ruta.nombre].push(polyline);
                    state.rutasHalos[ruta.nombre].push(halo);
                    
                    // Colocar un icono en el Origen (Hub) en el primer tramo (secuencia 1) o primer punto
                    if (tramo.tramo_secuencia === 1 && tramo.coordenadas.length > 0) {
                        const hubText = tramo.hub_site || tramo.origen || ruta.otu || 'HUB';
                        const hubIcon = L.divIcon({
                            className: 'custom-hub-icon',
                            html: `<div class="hub-pin"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg></div>
                                   <div class="hub-label">${hubText}</div>`,
                            iconSize: [40, 60],
                            iconAnchor: [20, 60]
                        });
                        
                        let markerCoords = tramo.coordenadas[0];
                        if (tramo.hub_site_lat && tramo.hub_site_lon) {
                            markerCoords = [tramo.hub_site_lat, tramo.hub_site_lon];
                        }

                        const hubMarker = L.marker(markerCoords, { icon: hubIcon });
                        
                        if (debeMostrarRuta) {
                            hubMarker.addTo(state.layerGroups.tramos);
                        }
                        
                        const odfName = tramo.odf_nombre || ruta.otu || '';
                        hubMarker.on('click', function() {
                            if(odfName) {
                                window.abrirPanelODF(hubText, odfName);
                            }
                        });
                    }
                }
            });

            // Preparar Reservas de esta ruta
            if (ruta.reservas_nodos && ruta.reservas_nodos.length > 0) {
                ruta.reservas_nodos.forEach(res => {
                    const tipo = (res.tipo || 'default').toLowerCase();
                    const iconUrl = `/static/img/iconos/${tipo}-icono.png`;
                    const customIcon = L.icon({ 
                        iconUrl: iconUrl, 
                        iconSize: [32, 32], 
                        iconAnchor: [16, 32], 
                        popupAnchor: [0, -30] 
                    });
                    
                    const resPopup = `
                        <div style="font-family: var(--font-sans); padding: 5px;">
                            <h4 style="margin:0 0 8px 0; color:var(--primary); font-size:14px; border-bottom:1px solid var(--border-color); padding-bottom:4px;">${res.nombre}</h4>
                            <div style="font-size:12px; margin-bottom:4px;"><strong>Tipo:</strong> ${res.tipo}</div>
                            <div style="font-size:12px; margin-bottom:4px;"><strong>Reserva Fija:</strong> ${res.reserva_m} m</div>
                            <div style="font-size:12px; color:var(--text-secondary);">Pertenece a: ${ruta.nombre}</div>
                        </div>
                    `;
                    
                    const marker = L.marker([res.lat, res.lon], { icon: customIcon })
                        .bindPopup(resPopup);
                    
                    // Solo guardamos en caché en vez de añadir al mapa directamente
                    state.dataCache.reservas.push({ marker: marker, rutaNombre: ruta.nombre });
                });
            }
        });
        
        // Evaluar qué reservas mostrar luego de cargar todas
        renderReservasVisibilidad();
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
                    
                    res.puertos.forEach(p => {
                        const isOcupado = p.estado && p.estado.toLowerCase().includes('ocupado');
                        if (isOcupado) ocupados++; else libres++;
                        
                        const boxClass = isOcupado ? 'ocupado' : 'libre';
                        const box = document.createElement('div');
                        box.className = `port-box ${boxClass}`;
                        box.innerText = p.puerto;
                        
                        box.addEventListener('mouseenter', (e) => showPortTooltip(e, p, isOcupado));
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
                    
                } else {
                    document.getElementById('sp-content').style.display = 'block';
                    document.getElementById('sp-ports-grid').innerHTML = `<div style="grid-column: 1/-1; color: var(--text-secondary); text-align: center; padding: 20px;">${res.message || 'No se encontraron puertos'}</div>`;
                    document.getElementById('sp-count-ocupados').innerText = '0';
                    document.getElementById('sp-count-libres').innerText = '0';
                    document.getElementById('sp-total-ports').innerText = 'Total: 0';
                }
            },
            error: function() {
                document.getElementById('sp-loading').style.display = 'none';
                document.getElementById('sp-content').style.display = 'block';
                document.getElementById('sp-ports-grid').innerHTML = `<div style="grid-column: 1/-1; color: var(--text-danger); text-align: center; padding: 20px;">Error al cargar datos del servidor</div>`;
            }
        });
    };

    function showPortTooltip(e, portData, isOcupado) {
        const tooltip = document.getElementById('port-tooltip');
        if (!tooltip) return;
        
        let html = `<div style="font-weight: 700; margin-bottom: 4px;">Puerto ${portData.puerto}</div>`;
        html += `<div><strong>Estado:</strong> ${portData.estado || 'Libre'}</div>`;
        if (isOcupado) {
            html += `<div><strong>Destino:</strong> <span style="color:var(--primary); font-weight:600;">${portData.destino || 'N/A'}</span></div>`;
            if (portData.fibra) html += `<div><strong>Fibra:</strong> ${portData.fibra}</div>`;
        }
        if (portData.tipo_conector) html += `<div><strong>Conector:</strong> ${portData.tipo_conector}</div>`;
        
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

})();
