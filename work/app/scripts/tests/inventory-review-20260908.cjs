const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = path.resolve(__dirname, '../..');
const read = name => fs.readFileSync(path.join(app, 'mapas/static/js', name), 'utf8');
let assertions = 0;
function eq(a, b) { assert.deepEqual(a, b); assertions++; }

function searchHarness() {
    const source = read('planta-interna-paginada.js');
    const functions = source.slice(source.indexOf('    function cancelFiberSearch()'), source.indexOf('    function openManagement(port)'));
    const pending = [], rendered = [], messages = [];
    const nodes = {
        'port-management-route': { value: 'RUTA' },
        'port-management-fiber-query': { value: 'F17' },
        'port-management-fiber': { replaceChildren() {}, appendChild() {} },
    };
    const context = {
        URL, AbortController,
        Option: function (text, value) { this.text = text; this.value = value; this.dataset = {}; },
        managementValue: k => nodes[k], updateProvisionalFiberField() {}, updateEndpointLabels() {},
        clearFiberResults() {}, emptyFiberPrompt() { return 'Buscar'; }, appendNewFiberOption() {},
        setManagementMessage: text => messages.push(text),
        root: { dataset: { fibersUrl: '/api/inventario/consulta/fibras/' } },
        window: { location: { origin: 'http://localhost' }, clearTimeout() {} },
        // El mock deja responder incluso peticiones abortadas, para comprobar
        // que el número de versión también protege durante response.json().
        fetch: (url, options) => new Promise((resolve, reject) => pending.push({ url, options, resolve, reject })),
        renderFiberResults: fibers => rendered.push(Array.from(fibers, f => f.numero)),
        numberFormat: new Intl.NumberFormat('es-PE'), fiberTerminationContext: () => '',
    };
    vm.createContext(context);
    vm.runInContext('let fiberSearchTimer=null, fiberSearchController=null, fiberSearchVersion=0;\n' + functions, context);
    const response = number => ({ ok: true, json: async () => ({ data: [{ id: number, numero: 'F' + number }] }) });
    return { context, nodes, pending, rendered, messages, response, source };
}

async function main() {
    const h = searchHarness();
    const first = h.context.loadFibers();
    eq(h.pending[0].url.searchParams.has('site'), true);
    eq(h.pending[0].url.searchParams.get('site'), '');
    eq(h.pending[0].url.searchParams.get('ruta'), 'RUTA');
    h.nodes['port-management-fiber-query'].value = 'F18';
    const second = h.context.loadFibers();
    eq(h.pending[0].options.signal.aborted, true);
    h.pending[1].resolve(h.response(18)); await second;
    h.pending[0].resolve(h.response(17)); await first;
    eq(h.rendered, [['F18']]);

    const stale = searchHarness();
    const request = stale.context.loadFibers();
    stale.context.cancelFiberSearch(); // Teclear/cerrar antes del siguiente debounce.
    stale.pending[0].resolve(stale.response(17)); await request;
    eq(stale.rendered, []);

    const failed = searchHarness();
    const oldRequest = failed.context.loadFibers();
    const latest = failed.context.loadFibers();
    failed.pending[1].resolve(failed.response(18)); await latest;
    const lastMessage = failed.messages.at(-1);
    failed.pending[0].reject(new Error('Error de la consulta antigua')); await oldRequest;
    eq(failed.messages.at(-1), lastMessage);
    eq(failed.rendered, [['F18']]);

    const cleared = searchHarness();
    const queued = cleared.context.loadFibers();
    cleared.nodes['port-management-route'].value = '';
    cleared.nodes['port-management-fiber-query'].value = '';
    await cleared.context.loadFibers();
    cleared.pending[0].resolve(cleared.response(17)); await queued;
    eq(cleared.rendered, []);
    eq(cleared.pending.length, 1);
    eq(h.source.includes('function closeManagement() {\n        cancelFiberSearch();'), true);
    eq(h.source.includes("addEventListener('input', () => {\n        cancelFiberSearch();"), true);
    eq(h.source.includes("elements.management?.addEventListener('close'"), true);

    const downloads = { window: {} };
    vm.runInNewContext(read('inventory-downloads.js'), downloads);
    const readBlob = downloads.window.FGInventoryDownloads.readBlob;
    const xlsx = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    function response({ status = 200, type = xlsx, disposition = 'attachment; filename="report.xlsx"', redirected = false } = {}) {
        let consumed = 0;
        return {
            status, ok: status >= 200 && status < 300, redirected,
            headers: new Headers({ 'Content-Type': type, 'Content-Disposition': disposition }),
            blob: async () => { consumed++; return 'FILE'; },
            consumed: () => consumed,
        };
    }
    for (const type of [xlsx, 'text/csv; charset=utf-8']) {
        const r = response({ type });
        eq(await readBlob(r), 'FILE');
        eq(r.consumed(), 1);
    }
    for (const options of [
        { redirected: true, type: 'text/html' }, { status: 401 },
        { status: 403 }, { status: 500 }, { type: 'text/html' },
        { type: 'application/json' }, { disposition: '' },
    ]) {
        const r = response(options);
        await assert.rejects(() => readBlob(r)); assertions++;
        eq(r.consumed(), 0);
    }
    await assert.rejects(() => readBlob(response({ redirected: true })), /sesión venció/); assertions++;
    for (const file of ['odf-inventory.js', 'planta-interna-paginada.js', 'fiber-inventory.js', 'routes-inventory.js']) {
        const source = read(file);
        eq(source.includes('FGInventoryDownloads.readBlob(response)'), true);
        eq(source.includes('response.blob()'), false);
        new vm.Script(source); assertions++;
    }
    const base = fs.readFileSync(path.join(app, 'mapas/templates/base.html'), 'utf8');
    eq(base.indexOf('inventory-downloads.js') < base.indexOf('{% block extra_js %}'), true);
    console.log(`${assertions} comprobaciones nuevas correctas.`);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
