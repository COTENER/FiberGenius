/* Ejecuta el código real de la GUI con respuestas controladas, sin tocar datos. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const read = name => fs.readFileSync(path.join(__dirname, '../../mapas', name), 'utf8');
let assertions = 0;
const eq = (actual, expected) => { assert.equal(actual, expected); assertions++; };
const ok = value => { assert.ok(value); assertions++; };
function section(source, start, end) {
    const from = source.indexOf(start), to = source.indexOf(end, from);
    assert.ok(from >= 0 && to > from, `Sección no encontrada: ${start}`);
    return source.slice(from, to);
}
function node() {
    return {children: [], dataset: {}, attrs: {}, textContent: '', disabled: false,
        classList: {add() {}, remove() {}, toggle() {}},
        setAttribute(key, value) { this.attrs[key] = value; },
        replaceChildren(...children) { this.children = children; },
        appendChild(child) { this.children.push(child); }, addEventListener() {}};
}
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return {promise, resolve, reject};
};
const response = payload => ({ok: true, status: 200, json: async () => payload});

async function checkNavigation(source) {
    const nodes = {}, pending = [], rendered = [], errors = [], ports = [];
    const element = id => nodes[id] ||= node();
    element('network-nav-panel').dataset = {siteUrlTemplate: '/sites/0/', portsUrl: '/ports/'};
    const context = vm.createContext({AbortController, URL, window: {location: {origin: 'http://localhost'}},
        state: {navigation: {}, siteMarkers: {}}, networkElement: element,
        showNetworkLoading() {}, showNetworkError: message => errors.push(message),
        renderSiteView: data => rendered.push(data.site.nombre), renderOdfPorts: data => ports.push(data),
        setPortGridMessage() {}, fetch: (url, options) => {
            const request = deferred(); pending.push({...request, signal: options.signal}); return request.promise;
        }});
    vm.runInContext(section(source, '    async function openSiteNavigation(', '    function renderSiteView(') +
        section(source, '    function closeNetworkNavigation(', '    function showNetworkLoading(') +
        section(source, '    async function loadOdfPorts(', '    function renderOdfPorts('), context);
    const first = context.openSiteNavigation(1, 'A');
    const second = context.openSiteNavigation(2, 'B');
    eq(pending[0].signal.aborted, true);
    pending[1].resolve(response({status: 'success', site: {nombre: 'B'}})); await second;
    pending[0].resolve(response({status: 'success', site: {nombre: 'A'}})); await first;
    eq(rendered.join(','), 'B'); eq(context.state.navigation.sitePayload.site.nombre, 'B');
    const closed = context.openSiteNavigation(3, 'C');
    context.closeNetworkNavigation();
    pending[2].resolve(response({status: 'success', site: {nombre: 'C'}})); await closed;
    eq(rendered.join(','), 'B'); eq(nodes['network-nav-panel'].attrs['aria-hidden'], 'true');
    const staleError = context.openSiteNavigation(4, 'D');
    const current = context.openSiteNavigation(5, 'E');
    pending[3].reject(Error('late failure')); await staleError;
    eq(errors.length, 0); ok(context.state.navigation.siteAbortController);
    pending[4].reject(Error('current failure')); await current;
    eq(errors.length, 1);
    const portLoad = context.loadOdfPorts({id: 1}, 1);
    const nextSite = context.openSiteNavigation(6, 'F');
    eq(pending[5].signal.aborted, true);
    pending[5].resolve(response({status: 'success', pagination: {page: 1}})); await portLoad;
    eq(ports.length, 0); eq(context.state.navigation.loadedOdfId, null);
    context.closeNetworkNavigation(); pending[6].reject(Error('closed')); await nextSite;
    eq(errors.length, 1);
}

function checkTiles(source) {
    const state = {map: {}}, mapElement = {};
    let refreshed = 0;
    const context = vm.createContext({state, mapElement,
        applyRouteFocusStyles() { refreshed++; },
        window: {FGBaseMap(map, target, options) {
            eq(map, state.map); eq(target, mapElement);
            options.onChange(); return {shared: true};
        }},
    });
    vm.runInContext(section(source, '        state.baseMap =', '        setupFullscreenControl();'), context);
    eq(refreshed, 1); eq(state.baseMap.shared, true);
    // Error, partial recovery, stale events and retries now live in the shared controller.
    require('./map-basemap.cjs');
}

async function checkTable(name) {
    const source = read(`static/js/${name}`);
    const end = name === 'fiber-inventory.js' ? '    function clearFiberDestinationContext(' : '    let routesTable;';
    const nodes = {}, panel = node(), pending = [];
    const element = key => nodes[key] ||= node();
    panel.querySelector = element;
    element('form').querySelector = element;
    const context = vm.createContext({AbortController, URL, URLSearchParams,
        root: {querySelector: () => panel}, location: {origin: 'http://localhost'},
        window: {location: {origin: 'http://localhost'}}, number: Intl.NumberFormat('es'), formatter: Intl.NumberFormat('es'),
        fetch: () => { const request = deferred(); pending.push(request); return request.promise; }});
    vm.runInContext(section(source, '    class InventoryTable {', end) + '\nthis.Table = InventoryTable;', context);
    context.Table.prototype.bind = () => {};
    const table = new context.Table('fibras', {api: '/api/', summary: value => { table.summary = value; }});
    table.params = () => new URLSearchParams();
    table.rows = table.renderRows = rows => { table.body.children = rows; };
    table.pages = table.renderPagination = () => {};
    const payload = label => ({data: [label], summary: {total: 1}, pagination: {
        page: 1, page_size: 25, total: 1, has_previous: false, has_next: false, total_pages: 1}});
    table.body.children = ['old']; table.summary = {total: 99}; table.loaded = true;
    const failed = table.load();
    eq(table.body.children.length, 0); eq(Object.keys(table.summary).length, 0);
    eq(element('[data-export]').disabled, true); eq(table.loaded, false);
    pending[0].reject(Error('offline')); await failed;
    eq(table.info.textContent, 'Consulta no disponible'); eq(table.body.children.length, 0);
    eq(table.previous.disabled, true); eq(table.next.disabled, true); eq(panel.attrs['aria-busy'], 'false');
    const old = table.load(), newest = table.load();
    pending[2].resolve(response(payload('new'))); await newest;
    pending[1].resolve(response(payload('old'))); await old;
    eq(table.body.children.join(','), 'new'); eq(table.loaded, true); eq(element('[data-export]').disabled, false);
    const oldError = table.load(), current = table.load();
    pending[3].reject(Error('old failure')); await oldError;
    eq(panel.attrs['aria-busy'], 'true');
    pending[4].resolve(response(payload('current'))); await current;
    eq(table.message.textContent, ''); eq(table.body.children.join(','), 'current');
    for (const result of [{ok: false, status: 403}, {ok: true, status: 200, redirected: true}]) {
        const task = table.load(); pending.at(-1).resolve(result); await task;
        eq(table.body.children.length, 0); eq(element('[data-export]').disabled, true);
        ok(table.message.textContent.includes(result.status === 403 ? 'permisos' : 'sesión'));
    }
    const malformed = table.load(); pending.at(-1).resolve(response({data: ['invalid']})); await malformed;
    eq(table.loaded, false); eq(table.body.children.length, 0);
    const recovered = table.load(); pending.at(-1).resolve(response(payload('recovered'))); await recovered;
    eq(table.body.children.join(','), 'recovered'); eq(element('[data-export]').disabled, false);
}

function checkUpload() {
    const source = read('templates/configuracion/configuracion_index.html');
    const snippet = section(source, 'function startImportUpload(', 'async function validateImportFile(');
    const cases = [
        [202, '{"lote":"abc","progreso_url":"/avance/"}', 'PENDIENTE'],
        [400, '{"error":"Archivo no válido"}', 'FALLIDO'],
        [403, '{"error":"Sin permiso"}', 'FALLIDO'],
        [502, '{"error":"Proxy no disponible"}', 'PROCESANDO'],
        [500, '<html>Error</html>', 'PROCESANDO'],
        [200, '<html>Login</html>', 'PROCESANDO'],
        [202, '{}', 'PROCESANDO'], [202, 'null', 'PROCESANDO'], [202, '{broken', 'PROCESANDO'],
        [403, '<html>Sesión caducada</html>', 'PROCESANDO'],
        [0, '', 'PROCESANDO', 'error'], [0, '', 'PROCESANDO', 'timeout'], [0, '', 'PROCESANDO', 'abort'],
    ];
    for (const [status, responseText, expected, event = 'load'] of cases) {
        const rendered = [], polls = [], remembered = [], nodes = {};
        let request, restored = 0, sends = 0;
        class Request {
            constructor() { request = this; this.events = {}; this.upload = {addEventListener() {}}; }
            open() {} setRequestHeader() {} addEventListener(key, handler) { this.events[key] = handler; }
            send() { sends++; }
        }
        const context = vm.createContext({FormData: class {set() {}}, XMLHttpRequest: Request,
            document: {getElementById: id => nodes[id] ||= node()}, currentImportType: 'fibras', importValidationToken: 'token',
            renderImportExecution: data => rendered.push(data), restoreImportControls: () => { restored++; },
            rememberImport: data => remembered.push(data), pollImportProgress: url => polls.push(url)});
        vm.runInContext(snippet, context); context.startImportUpload({action: '/import/'});
        Object.assign(request, {status, responseText}); request.events[event]();
        eq(rendered.at(-1).estado, expected); eq(sends, 1);
        if (expected === 'PENDIENTE') {
            eq(polls.join(','), '/avance/'); eq(remembered[0].codigo, 'abc'); eq(restored, 0);
        } else {
            eq(polls.length, 0); eq(remembered.length, 0); eq(restored, 1); eq(rendered.at(-1).porcentaje, 0);
            if (expected === 'PROCESANDO') ok(rendered.at(-1).mensaje.includes('historial'));
        }
    }
}

(async () => {
    const source = read('static/js/mapa_inventario.js');
    await checkNavigation(source); checkTiles(source);
    await checkTable('fiber-inventory.js'); await checkTable('routes-inventory.js'); checkUpload();
    console.log(`Recuperación funcional: ${assertions} aserciones correctas.`);
})().catch(error => { console.error(error); process.exitCode = 1; });
