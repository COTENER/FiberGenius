(function () {
    'use strict';

    function readJSON(id, fallback) {
        const node = document.getElementById(id);
        if (!node) return fallback;
        try {
            return JSON.parse(node.textContent);
        } catch (error) {
            console.warn(`No se pudo leer ${id}`, error);
            return fallback;
        }
    }

    function chartFont() {
        return getComputedStyle(document.body).fontFamily;
    }

    function chartColors() {
        const dark = document.documentElement.getAttribute('data-theme') === 'dark';
        return {
            dark,
            text: dark ? '#9fb0c7' : '#68778d',
            grid: dark ? 'rgba(158,183,216,.10)' : 'rgba(16,33,61,.08)',
        };
    }

    function commonChartOptions() {
        const colors = chartColors();
        return {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 450 },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: colors.dark ? '#0d1728' : '#10213d',
                    titleFont: { family: chartFont(), size: 11 },
                    bodyFont: { family: chartFont(), size: 11 },
                    padding: 10,
                    cornerRadius: 7,
                },
                datalabels: { display: false },
            },
        };
    }

    function initCharts() {
        if (!window.Chart) return;
        if (window.ChartDataLabels) Chart.register(window.ChartDataLabels);
        Chart.defaults.font.family = chartFont();
        Chart.defaults.color = chartColors().text;

        const inventoryCanvas = document.getElementById('inventoryTypeChartV3');
        if (inventoryCanvas) {
            const labels = readJSON('inventory-type-labels-v3', []);
            const values = readJSON('inventory-type-values-v3', []);
            const options = commonChartOptions();
            options.layout = { padding: { top: 14 } };
            options.scales = {
                x: { grid: { display: false }, border: { display: false }, ticks: { font: { size: 9 } } },
                y: {
                    beginAtZero: true,
                    grid: { color: chartColors().grid },
                    border: { display: false },
                    ticks: { precision: 0, font: { size: 9 } },
                },
            };
            options.plugins.datalabels = {
                display: context => context.dataset.data[context.dataIndex] > 0,
                anchor: 'end',
                align: 'top',
                color: chartColors().text,
                font: { family: chartFont(), size: 9, weight: '700' },
                formatter: value => new Intl.NumberFormat('es-PE').format(value),
            };
            new Chart(inventoryCanvas, {
                type: 'bar',
                data: {
                    labels,
                    datasets: [{
                        data: values,
                        backgroundColor: ['#1670e6', '#0ca69a', '#ef8d19', '#3a86e8', '#24ad69', '#8554d9'],
                        borderRadius: 5,
                        borderSkipped: false,
                        maxBarThickness: 38,
                    }],
                },
                options,
            });
        }

        const portsCanvas = document.getElementById('portStatusChartV3');
        if (portsCanvas) {
            const free = Number(portsCanvas.dataset.free || 0);
            const used = Number(portsCanvas.dataset.used || 0);
            const reserved = Number(portsCanvas.dataset.reserved || 0);
            const options = commonChartOptions();
            options.cutout = '70%';
            options.plugins.legend = {
                display: true,
                position: 'bottom',
                labels: {
                    usePointStyle: true,
                    pointStyle: 'circle',
                    boxWidth: 7,
                    padding: 14,
                    font: { family: chartFont(), size: 9, weight: '600' },
                },
            };
            options.plugins.tooltip.callbacks = {
                label: context => ` ${context.label}: ${new Intl.NumberFormat('es-PE').format(context.parsed)}`,
            };
            new Chart(portsCanvas, {
                type: 'doughnut',
                data: {
                    labels: ['Disponibles', 'Ocupados', 'Reservados'],
                    datasets: [{
                        data: [free, used, reserved],
                        backgroundColor: ['#24ad69', '#ef5b55', '#ef8d19'],
                        borderColor: chartColors().dark ? '#152238' : '#ffffff',
                        borderWidth: 3,
                        hoverOffset: 4,
                    }],
                },
                options,
            });
        }

        const activityCanvas = document.getElementById('loadActivityChartV3');
        if (activityCanvas) {
            const labels = readJSON('load-activity-labels-v3', []);
            const values = readJSON('load-activity-values-v3', []);
            const context = activityCanvas.getContext('2d');
            const fill = context.createLinearGradient(0, 0, 0, 230);
            fill.addColorStop(0, 'rgba(15, 166, 154, .24)');
            fill.addColorStop(1, 'rgba(15, 166, 154, 0)');
            const options = commonChartOptions();
            options.scales = {
                x: { grid: { display: false }, border: { display: false }, ticks: { font: { size: 9 } } },
                y: {
                    beginAtZero: true,
                    grid: { color: chartColors().grid },
                    border: { display: false },
                    ticks: { precision: 0, font: { size: 9 } },
                },
            };
            new Chart(activityCanvas, {
                type: 'line',
                data: {
                    labels,
                    datasets: [{
                        label: 'Registros procesados',
                        data: values,
                        borderColor: '#0ca69a',
                        backgroundColor: fill,
                        borderWidth: 2,
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        pointBackgroundColor: '#0ca69a',
                        tension: .35,
                        fill: true,
                    }],
                },
                options,
            });
        }
    }

    function popupForSite(site) {
        const wrapper = document.createElement('div');
        const name = document.createElement('strong');
        const detail = document.createElement('div');
        name.textContent = site.nombre || 'Site';
        detail.textContent = `${site.rutas_count || 0} rutas · ${site.odf_count || 0} ODF · ${site.hilos_count || 0} fibras`;
        detail.style.marginTop = '4px';
        wrapper.append(name, detail);
        return wrapper;
    }

    function initMap() {
        const target = document.getElementById('inventoryOverviewMapV3');
        if (!target || !window.L) return;
        let sites = readJSON('sites-map-data-v3', []);
        if (typeof sites === 'string') {
            try { sites = JSON.parse(sites); } catch (_error) { sites = []; }
        }
        if (!Array.isArray(sites) || !sites.length) return;

        const map = L.map(target, { zoomControl: true, scrollWheelZoom: false });
        const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        const tileUrl = isDark ? target.dataset.tileDark : target.dataset.tileLight;
        L.tileLayer(tileUrl, {
            attribution: target.dataset.attribution || '&copy; OpenStreetMap contributors',
            maxZoom: 20,
        }).addTo(map);

        const bounds = [];
        sites.forEach(site => {
            const lat = Number(site.lat);
            const lng = Number(site.lng);
            if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
            bounds.push([lat, lng]);
            L.circleMarker([lat, lng], {
                radius: 7,
                color: '#ffffff',
                weight: 2,
                fillColor: '#0ca69a',
                fillOpacity: 1,
            }).bindPopup(popupForSite(site)).addTo(map);
        });
        if (bounds.length === 1) map.setView(bounds[0], 12);
        else if (bounds.length) map.fitBounds(bounds, { padding: [28, 28] });
        setTimeout(() => map.invalidateSize(), 80);
    }

    function init() {
        initCharts();
        initMap();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
