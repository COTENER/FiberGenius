const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = path.resolve(__dirname, '../..');
const read = name => fs.readFileSync(path.join(app, 'mapas/static/js', name), 'utf8');
let assertions = 0;
function eq(actual, expected) { assert.deepEqual(actual, expected); assertions++; }

function boot({ url = 'http://localhost/inventario/interno/?site=A', stored = '', denied = false } = {}) {
    const handlers = {};
    const calls = [];
    const storage = new Map([['fg-site-context', stored]]);
    const nodes = {};
    const select = { value: '', matches: selector => selector === 'select[name="site"]' };
    const location = {
        href: url, origin: 'http://localhost',
        assign(value) { this.href = value; },
        replace(value) { this.replaced = value; },
    };
    const context = {
        URL, Request,
        sessionStorage: {
            getItem(k) { if (denied) throw Error('disabled'); return storage.get(k); },
            setItem(k, v) { if (denied) throw Error('disabled'); storage.set(k, v); },
            removeItem(k) { if (denied) throw Error('disabled'); storage.delete(k); },
        },
        document: {
            body: { dataset: { authenticated: 'true' } },
            querySelector: () => select,
            getElementById: id => nodes[id] ||= {
                addEventListener(type, handler) { this[type] = handler; },
            },
            addEventListener(type, handler) { handlers[type] = handler; },
        },
        window: {
            location,
            history: { state: null, replaceState(_s, _t, value) { location.href = value; } },
            fetch(resource, options) { calls.push({ resource, options }); return Promise.resolve({ ok: true }); },
        },
        HTMLFormElement: class {},
    };
    vm.runInNewContext(read('global-site-context.js'), context);
    return { ...context, handlers, calls, select, nodes, storage };
}

const c = boot();
eq(c.window.FGSiteContext.get(), 'A');
c.select.value = 'B';
c.handlers.change({ target: c.select });
c.window.fetch('/api/inventario/consulta/puertos/?site=B');
eq(new URL(c.calls.at(-1).resource).searchParams.get('site'), 'B');
eq(c.storage.get('fg-site-context'), 'B');
eq(c.nodes['global-site-context-name'].textContent, 'Site: B');
c.window.fetch('/api/inventario/consulta/fibras/');
eq(new URL(c.calls.at(-1).resource).searchParams.get('site'), 'B');
c.window.fetch('/api/inventario/consulta/puertos/?site=C');
eq(new URL(c.calls.at(-1).resource).searchParams.get('site'), 'C');
c.window.fetch('/api/inventario/consulta/puertos/?site=');
eq(new URL(c.calls.at(-1).resource).searchParams.get('site'), '');
c.handlers.reset({ target: { querySelector: () => c.select } });
c.window.fetch('/api/inventario/consulta/puertos/');
eq(c.calls.at(-1).resource, '/api/inventario/consulta/puertos/');
eq(c.window.FGSiteContext.get(), '');
eq(c.nodes['global-site-context'].hidden, true);
eq(new URL(c.window.location.href).searchParams.has('site'), false);
c.window.FGSiteContext.set('A');
c.window.fetch('/api/inventario/test/', { method: 'POST' });
eq(c.calls.at(-1).resource, '/api/inventario/test/');
c.window.fetch('https://external.example/api/inventario/test/');
eq(c.calls.at(-1).resource, 'https://external.example/api/inventario/test/');
c.window.fetch(new Request('http://localhost/api/inventario/test/', { headers: { 'X-Test': 'yes' } }));
eq(new URL(c.calls.at(-1).resource.url).searchParams.get('site'), 'A');
eq(c.calls.at(-1).resource.headers.get('X-Test'), 'yes');
c.nodes['global-site-context-clear'].click();
eq(c.window.FGSiteContext.get(), '');
eq(c.storage.has('fg-site-context'), false);
eq(boot({ url: 'http://localhost/inventario/interno/?site=', stored: 'OLD' }).window.FGSiteContext.get(), '');
const inherited = boot({ url: 'http://localhost/inventario/interno/', stored: 'SITE-A' });
eq(new URL(inherited.window.location.href).searchParams.get('site'), 'SITE-A');
const dashboard = boot({ url: 'http://localhost/inventario/dashboard/', stored: 'SITE-A' });
eq(new URL(dashboard.window.location.replaced).searchParams.get('site'), 'SITE-A');
const quality = boot({ url: 'http://localhost/inventario/calidad/', stored: 'SITE-A' });
eq(new URL(quality.window.location.replaced).searchParams.get('site'), 'SITE-A');
eq(boot({ url: 'http://localhost/inventario/calidad/?site=', stored: 'OLD' }).window.FGSiteContext.get(), '');
const denied = boot({ denied: true });
denied.select.value = 'B';
denied.handlers.change({ target: denied.select });
eq(denied.window.FGSiteContext.get(), 'B');

// Ejecute el predicado real del minimapa sobre rutas compuestas.
const dashboardSource = read('dashboard-inventario-v3.js');
const normalize = vm.runInNewContext('(' + dashboardSource.match(/function normalize\(value\)\s*\{[\s\S]*?\n    \}/)[0] + ')');
const condition = dashboardSource.match(/if \(selectedTrace && selectedTrace !== 'HIBRIDO'[\s\S]*?return;/)[0];
const render = new Function('selectedTrace', 'tramo', 'normalize', condition + ' return true;');
eq(['AEREO', 'SOTERRADO'].filter(tipo => render('HIBRIDO', { tipo_trazado: tipo }, normalize)), ['AEREO', 'SOTERRADO']);
eq(['AEREO', 'SOTERRADO'].filter(tipo => render('AEREO', { tipo_trazado: tipo }, normalize)), ['AEREO']);
eq(Boolean(render('', { tipo_trazado: 'SOTERRADO' }, normalize)), true);
eq(read('planta-interna-paginada.js').includes('terminacion_esperada: managedPort?.conexion?.terminacion_id'), true);
eq(read('mapa_inventario.js').includes('data: { site: window.FGSiteContext?.get()'), true);
console.log(`${assertions} comprobaciones JavaScript correctas.`);
