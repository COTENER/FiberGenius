const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(app, 'mapas/static/js', file), 'utf8');
const source = read('dashboard-inventario-v3.js');
let checks = 0;
const eq = (a, b) => { assert.deepEqual(a, b); checks++; };
function extract(start, end) { return source.slice(source.indexOf(start), source.indexOf(end)); }
const c = {};
vm.createContext(c);
vm.runInContext(extract('    function relatedSiteNames(', '    async function initDashboardMap('), c);
eq(Array.from(c.relatedSiteNames({ sites_relacionados: ['SITE-A'], tramos: [{ hub_site: 'VIEJO' }] })), ['SITE-A']);
eq(Array.from(c.relatedSiteNames({ sites_relacionados: [], tramos: [{ hub_site: 'VIEJO' }] })), []);
eq(Array.from(c.relatedSiteNames({ tramos: [{ hub_site: 'SITE-A', destino: 'SITE-B' }] })), ['SITE-A', 'SITE-B']);

const warning = { hidden: true };
const layers = [];
const context = {
    window: { L: true },
    document: { getElementById: () => warning },
    tileUrlForTheme: () => 'local-tiles',
    L: { tileLayer: () => {
        const layer = { handlers: {}, on(event, fn) { this.handlers[event] = fn; }, addTo() {}, bringToBack() {} };
        layers.push(layer); return layer;
    } },
};
vm.createContext(context);
vm.runInContext('let dashboardMap={removeLayer(){}}, dashboardMapTarget={dataset:{}}, dashboardTileLayer=null;\n' + extract('    function refreshMapTheme(', '    function configureMapSearch('), context);
context.refreshMapTheme();
layers[0].handlers.loading(); layers[0].handlers.tileerror();
eq(warning.hidden, false);
layers[0].handlers.load(); eq(warning.hidden, false);
layers[0].handlers.loading(); layers[0].handlers.load(); eq(warning.hidden, true);
context.refreshMapTheme();
layers[0].handlers.tileerror(); eq(warning.hidden, true); // Evento de la capa anterior.
layers[1].handlers.tileerror(); eq(warning.hidden, false);

eq(source.includes("labels.push('Sin estado informado')"), true);
eq(source.includes('Sin cobertura / estado'), false);
eq(source.includes("mapUrl.searchParams.set('trazado'"), true);
for (const file of ['dashboard-inventario-v3.js', 'fiber-inventory.js', 'odf-inventory.js', 'routes-inventory.js', 'mapa_inventario.js']) {
    new vm.Script(read(file)); checks++;
}
eq(read('routes-inventory.js').includes("rankingContext = { kind: 'troncales', id: incoming.get('ranking_id')"), true);
eq(read('odf-inventory.js').includes("rankingOdfId = incoming.get('odf_id')"), true);
eq(read('fiber-inventory.js').includes("root.querySelectorAll('[data-filter=\"trazado\"]')"), true);
eq(read('fiber-inventory.js').includes("form.querySelector('[data-clear]').addEventListener('click', syncTraceUrl)"), true);
console.log(`Dashboard: ${checks} comprobaciones JavaScript correctas.`);
