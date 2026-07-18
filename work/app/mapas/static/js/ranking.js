/**
 * Ranking.js - UI y Lógica para Analytics Proactivo
 */

(function() {
    let lastQueryString = '';
    let dtRegular = null;
    let dtOptical = null;
    let isDarkMode = document.documentElement.getAttribute('data-theme') === 'dark';

    document.addEventListener('DOMContentLoaded', function() {
        const initialDataElement = document.getElementById('django-data');
        let initialData = null;
        if (initialDataElement) {
            initialData = JSON.parse(initialDataElement.textContent);
        }

        initDataTables();
        bindEvents();
    });

    // Escuchar cambios de tema visual
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            if (mutation.attributeName === "data-theme") {
                isDarkMode = document.documentElement.getAttribute('data-theme') === 'dark';
            }
        });
    });
    observer.observe(document.documentElement, { attributes: true });

    function initDataTables() {
        const commonConfig = {
            pageLength: 5,
            lengthMenu: [[5, 10, 20, -1], [5, 10, 20, "Todas"]],
            language: {
                search: "",
                searchPlaceholder: "Buscar ruta, OTU...",
                lengthMenu: "Mostrar _MENU_",
                info: "Mostrando _START_ a _END_ de _TOTAL_ rutas",
                infoEmpty: "No hay datos para mostrar",
                infoFiltered: "(filtrado de _MAX_ totales)",
                paginate: {
                    first: "Primero",
                    last: "Último",
                    next: "Siguientes",
                    previous: "Anteriores"
                }
            },
            // Layout SaaS custom
            dom: '<"rank-dt-top"lf>rt<"rank-dt-bottom"ip>',
            destroy: true
        };

        if (document.getElementById('regular-dt-table')) {
            dtRegular = $('#regular-dt-table').DataTable({
                ...commonConfig,
                order: [[6, 'desc']] // Sort by Total Eventos descending
            });
        }

        if (document.getElementById('optical-dt-table')) {
            dtOptical = $('#optical-dt-table').DataTable({
                ...commonConfig,
                order: [[2, 'desc']] // Sort by Fallos descending
            });
        }
    }


    function bindEvents() {
        $('#filtro-form').on('submit', function(e) {
            e.preventDefault();
            fetchData();
        });
        $('#filtro-form select').on('change', function() {
            fetchData();
        });

        $('#export-regular-csv-btn').on('click', function(e) {
            e.preventDefault();
            const baseUrl = $(this).attr('href');
            let formData = $('#filtro-form').serializeArray();
            formData.push({ name: 'show_all_regulares', value: 'true' });
            window.location.href = `${baseUrl}?${$.param(formData)}`;
        });

        $('#export-optical-csv-btn').on('click', function(e) {
            e.preventDefault();
            const baseUrl = $(this).attr('href');
            let formData = $('#filtro-form').serializeArray();
            formData.push({ name: 'show_all_opticos', value: 'true' });
            window.location.href = `${baseUrl}?${$.param(formData)}`;
        });
    }

    function fetchData() {
        let formData = $('#filtro-form').serializeArray();
        
        // Force server to send ALL data so DataTables can paginate client-side
        formData.push({ name: 'show_all_regulares', value: 'true' });
        formData.push({ name: 'show_all_opticos', value: 'true' });

        const queryString = $.param(formData);

        if (queryString === lastQueryString) return;
        lastQueryString = queryString;

        $.ajax({
            url: window.location.pathname,
            type: 'GET',
            data: queryString,
            success: function(response) {
                updateRankingUI(response);
                history.pushState(null, '', '?' + queryString);
            },
            error: function(xhr, status, error) {
                console.error("Error al filtrar:", error);
                lastQueryString = '';
            }
        });
    }

    function updateRankingUI(response) {
        // 1. Update Filters
        const rutaSelect = document.getElementById('ruta');
        if (rutaSelect && response.rutas_disponibles) {
            const currentRoute = rutaSelect.value;
            rutaSelect.innerHTML = '<option value="">Todas las Rutas</option>';
            response.rutas_disponibles.forEach(r => {
                const opt = document.createElement('option');
                opt.value = opt.textContent = r;
                rutaSelect.appendChild(opt);
            });
            rutaSelect.value = currentRoute;
        }

        // 2. Update KPIs
        if (response.top_rutas_data) {
            let totalEventos = response.top_rutas_data.reduce((sum, item) => sum + item.total, 0);
            $('#kpi-total-eventos').text(totalEventos);
            $('#kpi-rutas-afectadas').text(response.top_rutas_data.length);
        }

        const linkIconSVG = `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" x2="21" y1="14" y2="3"/></svg>`;

        // 3. Update Regular DataTables
        if (dtRegular && response.top_rutas_data) {
            dtRegular.clear();
            const rowsRegular = response.top_rutas_data.map((item, index) => {
                let badgeClass = item.fiber_cut > 2 ? 'critical' : (item.fiber_cut > 0 ? 'high' : 'low');
                let tagStr = index === 0 ? `<span class="badge-critical-tag">MÁS CRÍTICA</span>` : '';
                return [
                    `<div style="display:flex;align-items:center;">
                        <a href="${URL_MAPA}?buscar_ruta=${encodeURIComponent(item.ruta)}" class="item-link">${item.ruta} ${linkIconSVG}</a>
                        ${tagStr}
                    </div>`,
                    item.otu,
                    item.attenuation,
                    item.injection,
                    item.reflexion,
                    `<span class="severity-badge ${badgeClass}">${item.fiber_cut}</span>`,
                    item.disconnections,
                    `<span class="total-highlight">${item.total}</span>`
                ];
            });
            dtRegular.rows.add(rowsRegular).draw();
        }

        // 4. Update Optical DataTables
        if (dtOptical && response.top_opticos_data) {
            dtOptical.clear();
            const rowsOptical = response.top_opticos_data.map(item => {
                let badgeClass = item.fail_count > 20 ? 'critical' : (item.fail_count > 10 ? 'high' : (item.fail_count > 0 ? 'medium' : 'low'));
                return [
                    `<a href="${URL_MAPA}?buscar_ruta=${encodeURIComponent(item.ruta)}" class="item-link">${item.ruta} ${linkIconSVG}</a>`,
                    item.otu,
                    `<span class="severity-badge ${badgeClass}">${item.fail_count}</span>`,
                    item.first_connector_count,
                    item.switch_count,
                    item.reflection_count,
                    item.splice_count,
                    item.fiber_end_count,
                    `<span class="total-highlight">${item.total}</span>`
                ];
            });
            dtOptical.rows.add(rowsOptical).draw();
        }
    }
})();
