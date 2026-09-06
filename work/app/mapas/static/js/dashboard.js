// static/js/dashboard.js

(function() {

    // =====================================================================
    // 1. ESTADO Y CONFIGURACIÓN GLOBAL DE LA APLICACIÓN
    // =====================================================================
    
    const isDarkMode = document.documentElement.getAttribute('data-theme') === 'dark';
    const uiMode = {
        text: isDarkMode ? '#94a3b8' : '#64748b',
        grid: isDarkMode ? '#334155' : '#f1f5f9',
        theme: isDarkMode ? 'dark' : 'light',
        heatmapZero: isDarkMode ? '#1e293b' : '#f1f5f9'
    };

    const charts = {
        severidadChart: null, 
        eventosChart: null, 
        estadoChart: null,
        top5Chart: null,
        yoyChart: null,
        heatmapChart: null,
        gaugeChart: null,
        timeChart: null,
        timeEvent: null,
        eventosPorTipoYAnio: null,
        sparks: []
    };

    const config = {
        colorMapSeveridad: {
            'CRITICAL': '#ef4444', // Red NOC
            'MAJOR': '#f59e0b',    // Amber
            'MINOR': '#10b981'     // Green
        },
        colorMapEventos: {
            'FIBER_CUT': '#ef4444',        // Red
            'ATTENUATION': '#f97316',      // Orange
            'INJECTION': '#3b82f6',        // Blue
            'DISCONNECTIONS': '#06b6d4',   // Cyan
            'REFLEXION': '#8b5cf6'         // Purple
        },
        commonOptions: {
            chart: {
                fontFamily: 'Inter, system-ui, sans-serif',
                toolbar: { show: false },
                animations: { enabled: true, easing: 'easeinout', speed: 400, animateGradually: { enabled: true, delay: 150 }, dynamicAnimation: { enabled: true, speed: 350 } },
                foreColor: uiMode.text
            },
            dataLabels: { enabled: true, style: { fontSize: '11px', fontWeight: 600 } },
            plotOptions: { bar: { borderRadius: 4, dataLabels: { position: 'top' } } },
            xaxis: {
                labels: { style: { colors: uiMode.text, fontSize: '11px', fontWeight: 500 } },
                axisBorder: { show: false }, axisTicks: { show: false }
            },
            yaxis: {
                labels: { style: { colors: uiMode.text, fontSize: '11px' } }
            },
            grid: { borderColor: uiMode.grid, strokeDashArray: 4, padding: { top: 10, right: 10, bottom: 0, left: 10 } }
        }
    };
    
    // =====================================================================
    // 2. INICIALIZACIÓN
    // =====================================================================

    document.addEventListener('DOMContentLoaded', function() {
        const initialDataElement = document.getElementById('django-data');
        if (!initialDataElement) {
            console.error('No se encontraron los datos de Django.');
            return;
        }
        const initialData = JSON.parse(initialDataElement.textContent);

        initAllCharts(initialData);
        bindEvents();
        renderActiveFilters();
    });

    function initAllCharts(data) {
        createTop5Chart('top5Chart', data.top_5_rutas_labels, data.top_5_rutas_values);
        createSeveridadChart('severidadChart', data.severidad_labels, data.severidad_values);
        createGaugeChart('gaugeChart', data.criticidad_pct);
        createYoYChart('yoyChart', data.yoy_series);
        createEstadoChart('estadoChart', data.estado_labels, data.estado_values);
        
        createTimeChart('timeChart', data.time_chart_data);
        createTimeEventChart('timeEvent', data.time_event_data);
        createEventosPorAnioChart('eventosPorTipoYAnio', data.eventos_por_tipo_y_anio);

        createHeatmapChart('heatmapChart', data.heatmap_series);
        createEventosChart('eventosChart', data.eventos_labels, data.eventos_values);
        
        createSparklines(data);
    }
    
    // =====================================================================
    // 3. FUNCIONES DE CREACIÓN DE GRÁFICOS (APEXCHARTS)
    // =====================================================================

    function createSparklines(data) {
        charts.sparks.forEach(s => s.destroy());
        charts.sparks = [];
        const sparkOptions = {
            chart: { type: 'area', height: 40, sparkline: { enabled: true } },
            stroke: { curve: 'smooth', width: 2 },
            fill: { opacity: 0.1, type: 'solid' },
            tooltip: { fixed: { enabled: false }, x: { show: false }, y: { title: { formatter: function (seriesName) { return '' } } }, marker: { show: false } }
        };
        
        // Mock data for sparks for aesthetics
        const s1 = new ApexCharts(document.querySelector("#spark1"), { ...sparkOptions, series: [{ data: [12, 14, 2, 47, 42, 15, 47] }], colors: ['#3b82f6'] });
        const s2 = new ApexCharts(document.querySelector("#spark2"), { ...sparkOptions, series: [{ data: [47, 45, 74, 14, 56, 37, 48] }], colors: ['#6366f1'] });
        const s3 = new ApexCharts(document.querySelector("#spark3"), { ...sparkOptions, series: [{ data: [15, 75, 47, 65, 14, 2, 41] }], colors: ['#10b981'] });
        const s4 = new ApexCharts(document.querySelector("#spark4"), { ...sparkOptions, series: [{ data: [47, 15, 30, 24, 14, 56, 30] }], colors: ['#f59e0b'] });
        
        s1.render(); s2.render(); s3.render(); s4.render();
        charts.sparks.push(s1, s2, s3, s4);
    }

    function createTop5Chart(containerId, labels, values) {
        if (charts.top5Chart) charts.top5Chart.destroy();
        const options = {
            ...config.commonOptions,
            series: [{ name: 'Eventos', data: values }],
            chart: { ...config.commonOptions.chart, type: 'bar', height: 280 },
            plotOptions: { bar: { horizontal: true, borderRadius: 4, barHeight: '60%', distributed: true, dataLabels: { position: 'center' } } },
            colors: ['#0f62fe', '#f57c00', '#0288d1', '#d32f2f', '#388e3c'],
            dataLabels: {
                enabled: true, textAnchor: 'middle',
                style: { colors: ['#fff'] },
                formatter: function (val) { 
                    return val;
                },
                offsetX: 0
            },
            xaxis: { ...config.commonOptions.xaxis, categories: labels },
            yaxis: { 
                show: true, 
                labels: { 
                    show: true, 
                    maxWidth: 300, 
                    style: { 
                        colors: uiMode.text, 
                        fontSize: '11px', 
                        fontFamily: 'Inter, sans-serif', 
                        fontWeight: 600 
                    } 
                } 
            },
            tooltip: { theme: uiMode.theme, y: { title: { formatter: function () { return '' } } } },
            legend: { show: false }
        };
        charts.top5Chart = new ApexCharts(document.querySelector("#" + containerId), options);
        charts.top5Chart.render();
    }

    function createSeveridadChart(containerId, labels, values) {
        if (charts.severidadChart) charts.severidadChart.destroy();
        const colors = labels.map(l => config.colorMapSeveridad[l] || '#94a3b8');
        
        const options = {
            ...config.commonOptions,
            series: [{ name: 'Eventos', data: values }],
            chart: { 
                ...config.commonOptions.chart, 
                type: 'bar', height: 280,
                events: {
                    dataPointSelection: function(e, chart, opts) {
                        const selectedLabel = opts.w.globals.labels[opts.dataPointIndex];
                        applyCrossFilter('severidad', selectedLabel);
                    }
                }
            },
            colors: colors,
            plotOptions: { bar: { ...config.commonOptions.plotOptions.bar, distributed: true } },
            legend: { show: false },
            xaxis: { ...config.commonOptions.xaxis, categories: labels },
            dataLabels: { ...config.commonOptions.dataLabels, formatter: function (val) { return val; }, offsetY: -20, style: { colors: [uiMode.text] } },
            tooltip: { theme: uiMode.theme, y: { formatter: function (val) { return val + " eventos" } } }
        };

        charts.severidadChart = new ApexCharts(document.querySelector("#" + containerId), options);
        charts.severidadChart.render();
    }

    function createGaugeChart(containerId, value) {
        if (charts.gaugeChart) charts.gaugeChart.destroy();
        
        let color = '#3b82f6'; // Blue Base
        if (value > 15) color = '#ef4444'; // Red
        else if (value > 5) color = '#f59e0b'; // Amber

        const options = {
            series: [value],
            chart: { type: 'radialBar', height: 320, sparkline: { enabled: true } },
            plotOptions: {
                radialBar: {
                    startAngle: -135, endAngle: 135,
                    hollow: { size: '65%' },
                    track: { 
                        background: isDarkMode ? "#334155" : "#e2e8f0", 
                        strokeWidth: '100%', 
                        margin: 0,
                        dropShadow: { enabled: false } 
                    },
                    dataLabels: {
                        show: true,
                        name: { show: true, fontSize: '13px', color: uiMode.text, offsetY: 25 },
                        value: { 
                            offsetY: -10, 
                            fontSize: '36px', 
                            fontWeight: 700, 
                            color: isDarkMode ? '#f8fafc' : '#0f172a',
                            formatter: function (val) { return val + "%"; } 
                        }
                    }
                }
            },
            grid: { padding: { top: 20, bottom: 20 } },
            fill: { type: 'solid', colors: [color] },
            stroke: { lineCap: 'round' },
            labels: ['Pendientes'],
        };
        charts.gaugeChart = new ApexCharts(document.querySelector("#" + containerId), options);
        charts.gaugeChart.render();
    }

    function createYoYChart(containerId, series) {
        if (charts.yoyChart) charts.yoyChart.destroy();
        const options = {
            ...config.commonOptions,
            series: series,
            chart: { ...config.commonOptions.chart, type: 'area', height: 280 },
            colors: ['#3b82f6', '#94a3b8'], // Current year (Blue), Prev Year (Gray)
            dataLabels: { enabled: false },
            stroke: { curve: 'smooth', width: [3, 2], dashArray: [0, 4] },
            xaxis: { ...config.commonOptions.xaxis, categories: ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'] },
            fill: { type: ['gradient', 'solid'], gradient: { shadeIntensity: 1, opacityFrom: 0.4, opacityTo: 0.05, stops: [0, 90, 100] }, opacity: [1, 0.1] },
            legend: { position: 'top', horizontalAlign: 'right' },
            tooltip: { theme: 'light', shared: true, intersect: false }
        };
        charts.yoyChart = new ApexCharts(document.querySelector("#" + containerId), options);
        charts.yoyChart.render();
    }

    function createHeatmapChart(containerId, series) {
        if (charts.heatmapChart) charts.heatmapChart.destroy();
        const options = {
            series: series,
            chart: { ...config.commonOptions.chart, type: 'heatmap', height: 280 },
            dataLabels: { enabled: true, style: { colors: ['#fff'] } },
            colors: ["#3b82f6"],
            plotOptions: {
                heatmap: {
                    shadeIntensity: 0.5, radius: 4, useFillColorAsStroke: false,
                    colorScale: {
                        ranges: [
                            { from: 0, to: 0, name: 'Cero', color: uiMode.heatmapZero },
                            { from: 1, to: 2, name: 'Bajo', color: '#fcd34d' },
                            { from: 3, to: 10, name: 'Medio', color: '#fb923c' },
                            { from: 11, to: 100, name: 'Alto', color: '#ef4444' }
                        ]
                    }
                }
            },
            xaxis: { ...config.commonOptions.xaxis },
            tooltip: { theme: uiMode.theme }
        };
        charts.heatmapChart = new ApexCharts(document.querySelector("#" + containerId), options);
        charts.heatmapChart.render();
    }

    function createEventosChart(containerId, labels, values) {
        if (charts.eventosChart) charts.eventosChart.destroy();
        const colors = labels.map(l => config.colorMapEventos[l] || '#94a3b8');
        
        const options = {
            ...config.commonOptions,
            series: [{ name: 'Eventos', data: values }],
            chart: { 
                ...config.commonOptions.chart, 
                type: 'bar', height: 280,
                events: {
                    dataPointSelection: function(e, chart, opts) {
                        const selectedLabel = opts.w.globals.labels[opts.dataPointIndex];
                        applyCrossFilter('tipo_evento', selectedLabel);
                    }
                }
            },
            colors: colors,
            plotOptions: { bar: { ...config.commonOptions.plotOptions.bar, distributed: true } },
            legend: { show: false },
            xaxis: { ...config.commonOptions.xaxis, categories: labels },
            dataLabels: { ...config.commonOptions.dataLabels, formatter: function (val) { return val; }, offsetY: -20, style: { colors: [uiMode.text] } },
            tooltip: { theme: uiMode.theme, y: { formatter: function (val) { return val + " eventos" } } }
        };

        charts.eventosChart = new ApexCharts(document.querySelector("#" + containerId), options);
        charts.eventosChart.render();
    }

    function createEstadoChart(containerId, labels, values) {
        if (charts.estadoChart) charts.estadoChart.destroy();
        const options = {
            series: values,
            labels: labels,
            chart: { 
                ...config.commonOptions.chart, 
                type: 'donut', height: 280
            },
            colors: ['#10b981', '#ef4444'], // CLEARED (Green), UNCLEARED (Red)
            dataLabels: { enabled: true, formatter: function (val) { return val.toFixed(1) + "%" } },
            legend: { position: 'bottom', horizontalAlign: 'center', markers: { radius: 12 }, labels: { colors: uiMode.text } },
            stroke: { show: true, colors: isDarkMode ? '#1e293b': '#ffffff', width: 2 },
            tooltip: { theme: uiMode.theme, y: { formatter: function (val) { return val + " eventos" } } },
            plotOptions: { pie: { donut: { size: '65%', labels: { show: true, name: { show: true }, value: { show: true } } } } }
        };

        charts.estadoChart = new ApexCharts(document.querySelector("#" + containerId), options);
        charts.estadoChart.render();
    }
    
    function createTimeChart(containerId, timeData) {
        if (charts.timeChart) charts.timeChart.destroy();
        const categories = []; 
        const severidades = ['CRITICAL', 'MAJOR', 'MINOR'];
        const seriesData = { 'CRITICAL': [], 'MAJOR': [], 'MINOR': [] };

        for (const anio in timeData) {
            for (const trimestre in timeData[anio]) {
                categories.push(`Q${trimestre} ${anio}`);
                severidades.forEach(sev => {
                    seriesData[sev].push(timeData[anio][trimestre][sev] || 0);
                });
            }
        }
        const series = severidades.map(sev => ({ name: sev, data: seriesData[sev] }));

        const options = {
            ...config.commonOptions,
            series: series,
            chart: { ...config.commonOptions.chart, type: 'bar', height: 280, stacked: true },
            colors: [config.colorMapSeveridad.CRITICAL, config.colorMapSeveridad.MAJOR, config.colorMapSeveridad.MINOR],
            plotOptions: { bar: { horizontal: false, borderRadius: 2 } },
            xaxis: { ...config.commonOptions.xaxis, categories: categories },
            dataLabels: { enabled: true, style: { fontSize: '12px' } },
            legend: { position: 'bottom', horizontalAlign: 'center', labels: { colors: uiMode.text } },
            fill: { opacity: 1 },
            tooltip: { theme: uiMode.theme }
        };

        charts.timeChart = new ApexCharts(document.querySelector("#" + containerId), options);
        charts.timeChart.render();
    }

    function createTimeEventChart(containerId, timeEventData) {
        if (charts.timeEvent) charts.timeEvent.destroy();
        const categories = []; 
        const eventos = ['FIBER_CUT', 'ATTENUATION', 'INJECTION', 'DISCONNECTIONS', 'REFLEXION'];
        const seriesData = { 'FIBER_CUT': [], 'ATTENUATION': [], 'INJECTION': [], 'DISCONNECTIONS': [], 'REFLEXION': [] };

        for (const anio in timeEventData) {
            for (const trimestre in timeEventData[anio]) {
                categories.push(`Q${trimestre} ${anio}`);
                eventos.forEach(ev => {
                    seriesData[ev].push(timeEventData[anio][trimestre][ev] || 0);
                });
            }
        }
        const series = eventos.map(ev => ({ name: ev, data: seriesData[ev] }));

        const options = {
            ...config.commonOptions,
            series: series,
            chart: { ...config.commonOptions.chart, type: 'bar', height: 280, stacked: true },
            colors: [config.colorMapEventos.FIBER_CUT, config.colorMapEventos.ATTENUATION, config.colorMapEventos.INJECTION, config.colorMapEventos.DISCONNECTIONS, config.colorMapEventos.REFLEXION],
            plotOptions: { bar: { horizontal: false, borderRadius: 2 } },
            xaxis: { ...config.commonOptions.xaxis, categories: categories },
            dataLabels: { enabled: true, style: { fontSize: '12px' } },
            legend: { position: 'bottom', horizontalAlign: 'center', labels: { colors: uiMode.text } },
            fill: { opacity: 1 },
            tooltip: { theme: uiMode.theme }
        };

        charts.timeEvent = new ApexCharts(document.querySelector("#" + containerId), options);
        charts.timeEvent.render();
    }

    function createEventosPorAnioChart(containerId, eventosPorAnio) {
        if (charts.eventosPorTipoYAnio) charts.eventosPorTipoYAnio.destroy();
        
        const anios = Object.keys(eventosPorAnio); 
        if (anios.length === 0) return; 
        
        const tiposEventos = ['FIBER_CUT', 'ATTENUATION', 'INJECTION', 'DISCONNECTIONS', 'REFLEXION'];
        const series = anios.map(anio => { 
            const data = tiposEventos.map(tipo => eventosPorAnio[anio][tipo] || 0); 
            return { name: anio, data: data }; 
        });

        const options = {
            ...config.commonOptions,
            series: series,
            chart: { ...config.commonOptions.chart, type: 'bar', height: 280 },
            plotOptions: { bar: { horizontal: false, columnWidth: '55%', borderRadius: 2 } },
            dataLabels: { enabled: true, style: { fontSize: '12px' } },
            stroke: { show: true, width: 2, colors: ['transparent'] },
            xaxis: { ...config.commonOptions.xaxis, categories: tiposEventos },
            colors: ['#60a5fa', '#34d399', '#fbbf24', '#f87171', '#c084fc'],
            fill: { opacity: 1 },
            tooltip: { theme: uiMode.theme, y: { formatter: function (val) { return val + " eventos" } } },
            legend: { position: 'bottom', labels: { colors: uiMode.text } }
        };

        charts.eventosPorTipoYAnio = new ApexCharts(document.querySelector("#" + containerId), options);
        charts.eventosPorTipoYAnio.render();
    }
    
    // =====================================================================
    // 4. LÓGICA DE EVENTOS (CROSS-FILTERING ASÍNCRONO)
    // =====================================================================

    function bindEvents() {
        $('#filtro-form').on('submit', function(e) { e.preventDefault(); fetchData(); });
        $('#filtro-form select').on('change', function() { fetchData(); });
        $('#btn-reset-filters').on('click', clearAllFilters);
        
        // Export PDF
        $('#export-all-charts-btn').on('click', function() {
            // Force redraw of charts to fix asymmetries in PDF layout
            window.dispatchEvent(new Event('resize'));
            setTimeout(function() {
                window.print();
            }, 600);
        });
    }
    
    function applyCrossFilter(filterKey, filterValue) {
        const input = document.getElementById(`input-${filterKey}`);
        if(input.value === filterValue) {
            input.value = ''; // Deselect si ya estaba clickeado
        } else {
            input.value = filterValue; // Apply
        }
        renderActiveFilters();
        fetchData();
    }
    
    window.removeFilter = function(filterKey) {
        document.getElementById(`input-${filterKey}`).value = '';
        renderActiveFilters();
        fetchData();
    }
    
    function clearAllFilters() {
        document.getElementById('input-severidad').value = '';
        document.getElementById('input-tipo_evento').value = '';
        $('#anio').val(''); $('#otu').val(''); $('#ruta').val('');
        
        renderActiveFilters();
        fetchData();
    }
    
    function renderActiveFilters() {
        const severidad = document.getElementById('input-severidad').value;
        const tipoEvento = document.getElementById('input-tipo_evento').value;
        const container = document.getElementById('active-filters');
        const resetBtn = document.getElementById('btn-reset-filters');

        function createFilterBadge(label, value, filterKey) {
            const badge = document.createElement('div');
            badge.className = 'filter-badge';

            const description = document.createElement('span');
            description.append(`${label}: `);
            const strong = document.createElement('strong');
            strong.textContent = value;
            description.appendChild(strong);

            const clear = document.createElement('button');
            clear.type = 'button';
            clear.className = 'filter-badge__clear';
            clear.setAttribute('aria-label', `Quitar filtro ${label}`);
            clear.textContent = '×';
            clear.addEventListener('click', () => window.removeFilter(filterKey));

            badge.append(description, clear);
            return badge;
        }

        const badges = [];
        if (severidad) badges.push(createFilterBadge('Severidad', severidad, 'severidad'));
        if (tipoEvento) badges.push(createFilterBadge('Evento', tipoEvento, 'tipo_evento'));

        container.replaceChildren(...badges);
        const hasFilters = badges.length > 0;
        resetBtn.style.display = hasFilters ? 'flex' : 'none';
        
        // Soft opacity indicator for loading
        $('.dash-charts').css('transition', 'opacity 0.2s').css('opacity', '0.7');
    }

    function fetchData() {
        const queryString = $('#filtro-form').serialize();
        
        fetch(window.location.pathname + '?' + queryString, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
        .then(response => response.json())
        .then(data => {
            updateDashboardUI(data);
            history.pushState(null, '', '?' + queryString);
            $('.dash-charts').css('opacity', '1');
        })
        .catch(error => {
            console.error("Error fetching data:", error);
            $('.dash-charts').css('opacity', '1');
        });
    }
    
    function updateDashboardUI(data) {
        // Update top KPIs directly
        document.querySelector('.data-total-eventos').textContent = data.cantidad_eventos;
        
        // Update route select keeping state
        const rutaSelect = document.getElementById('ruta');
        const rutaSeleccionada = rutaSelect.value;
        const routeOptions = [new Option('Todas', '')];
        data.rutas_disponibles.forEach(r => {
            routeOptions.push(new Option(String(r), String(r)));
        });
        rutaSelect.replaceChildren(...routeOptions);
        if (data.rutas_disponibles.includes(rutaSeleccionada)) {
            rutaSelect.value = rutaSeleccionada;
        }

        // UPDATE CHARTS (Animations!)
        // Top 5
        if(charts.top5Chart) {
            charts.top5Chart.updateOptions({ xaxis: { categories: data.top_5_rutas_labels } }, false, false);
            charts.top5Chart.updateSeries([{ data: data.top_5_rutas_values }]);
        }
        
        // Severidad
        if(charts.severidadChart) {
            charts.severidadChart.updateOptions({
                xaxis: { categories: data.severidad_labels },
                colors: data.severidad_labels.map(l => config.colorMapSeveridad[l] || '#94a3b8')
            }, false, false);
            charts.severidadChart.updateSeries([{ data: data.severidad_values }]);
        }

        // Tipo Evento
        if(charts.eventosChart) {
            charts.eventosChart.updateOptions({
                xaxis: { categories: data.eventos_labels },
                colors: data.eventos_labels.map(l => config.colorMapEventos[l] || '#94a3b8')
            }, false, false);
            charts.eventosChart.updateSeries([{ data: data.eventos_values }]);
        }
        
        // Estado (Donut requires replacing series completely)
        if(charts.estadoChart) {
            charts.estadoChart.updateOptions({ labels: data.estado_labels }, false, false);
            charts.estadoChart.updateSeries(data.estado_values);
        }

        // YoY
        if(charts.yoyChart) {
            charts.yoyChart.updateSeries(data.yoy_series);
        }

        // Heatmap
        if(charts.heatmapChart) {
            charts.heatmapChart.updateSeries(data.heatmap_series);
        }
        
        // Gauge
        if(charts.gaugeChart) {
            let color = '#3b82f6';
            if (data.criticidad_pct > 15) color = '#ef4444';
            else if (data.criticidad_pct > 5) color = '#f59e0b';
            charts.gaugeChart.updateOptions({
                fill: { colors: [color] }
            }, false, false);
            charts.gaugeChart.updateSeries([data.criticidad_pct]);
        }
        
        // Restore History Charts explicitly (destroy/create structure due to dynamic categories)
        setTimeout(() => {
            createTimeChart('timeChart', data.time_chart_data);
            createTimeEventChart('timeEvent', data.time_event_data);
            createEventosPorAnioChart('eventosPorTipoYAnio', data.eventos_por_tipo_y_anio);
        }, 100);
    }

})();
