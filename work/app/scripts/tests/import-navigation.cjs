/* Pruebas de comportamiento sin navegador ni acceso a la base de datos. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const app = path.resolve(__dirname, '../..');
const navigation = fs.readFileSync(path.join(app, 'mapas/static/js/import-navigation.js'), 'utf8');
const html = fs.readFileSync(path.join(app, 'mapas/templates/configuracion/configuracion_index.html'), 'utf8');
const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]);
inline.forEach(source => new vm.Script(source));
new vm.Script(navigation);

function element() {
    return {
        handlers: {}, attrs: {}, hidden: false, open: false, style: {},
        addEventListener(type, action) { this.handlers[type] = action; },
        setAttribute(name, value) { this.attrs[name] = value; },
        focus() { this.focused = true; },
    };
}
function setup(url) {
    const ids = Object.fromEntries(['importNavigation', 'odfConnectionGuide', 'startOdfGuide', 'closeOdfGuide', 'odf-guide-title'].map(id => [id, element()]));
    ids.odfConnectionGuide.hidden = true;
    const areas = [element(), element(), element(), element()];
    ids.importNavigation.querySelectorAll = () => areas;
    ids.importNavigation.contains = candidate => !candidate.outside;
    const opened = [];
    const win = {location: {href: url}, history: {replaceState(_a, _b, next) { win.location.href = String(next); }}};
    const context = vm.createContext({window: win, URL, document: {getElementById: id => ids[id]}, openImportModal: (...args) => opened.push(args)});
    vm.runInContext(navigation, context);
    return {ids, areas, opened, win};
}
const state = setup('http://localhost/configuracion/?site=SITE-A');
state.ids.startOdfGuide.handlers.click();
assert.equal(state.ids.odfConnectionGuide.hidden, false);
assert.equal(state.ids.startOdfGuide.attrs['aria-expanded'], 'true');
assert.equal(state.ids['odf-guide-title'].focused, true);
assert.equal(new URL(state.win.location.href).searchParams.get('site'), 'SITE-A');
assert.equal(new URL(state.win.location.href).searchParams.get('tarea'), 'odf-conexiones');
state.ids.closeOdfGuide.handlers.click();
assert.equal(state.ids.odfConnectionGuide.hidden, true);
assert.equal(state.ids.startOdfGuide.attrs['aria-expanded'], 'false');
assert.equal(state.ids.startOdfGuide.focused, true);
assert.equal(new URL(state.win.location.href).searchParams.has('tarea'), false);
const restored = setup('http://localhost/configuracion/?tarea=odf-conexiones');
assert.equal(restored.ids.odfConnectionGuide.hidden, false);
assert.equal(restored.ids['odf-guide-title'].focused, undefined);
state.areas[0].open = state.areas[1].open = true;
state.areas[1].handlers.toggle();
assert.equal(state.areas[0].open, false);
assert.equal(state.areas[1].open, true);
const button = {dataset: {openImport: 'terminaciones_fibra', importTitle: 'Conexiones A/B'}};
state.ids.importNavigation.handlers.click({target: {closest: () => button}});
assert.deepEqual(state.opened, [['terminaciones_fibra', 'Conexiones A/B']]);
button.outside = true;
state.ids.importNavigation.handlers.click({target: {closest: () => button}});
assert.equal(state.opened.length, 1);
state.ids.importNavigation.handlers.click({target: {closest: () => null}});

// El modal en proceso conserva su tipo aunque se seleccione otra tarjeta.
const nodes = {};
const origin = {...element(), isConnected: true};
const close = element();
const document = {
    activeElement: origin,
    addEventListener() {},
    getElementById(id) { return nodes[id] ||= element(); },
};
nodes.importModal = {...element(), querySelector: () => close};
const context = vm.createContext({document, window: {}, localStorage: {getItem: () => null}, console});
inline.forEach(source => vm.runInContext(source, context));
vm.runInContext("importBusy = true; currentImportType = 'odf_inventario'; openImportModal('fibras_globales', 'Otra carga');", context);
assert.equal(vm.runInContext('currentImportType', context), 'odf_inventario');
assert.equal(nodes.importModal.style.display, 'flex');
assert.equal(close.focused, true);
vm.runInContext('closeImportModal()', context);
assert.equal(nodes.importModal.style.display, 'none');
assert.equal(origin.focused, true);
// Una referencia antigua o de otra sesión no bloquea todas las tarjetas.
const removed = [];
context.fetch = async () => ({status: 404});
context.localStorage.removeItem = key => removed.push(key);
context.window.clearTimeout = () => {};
context.window.setTimeout = () => 1;
context.AbortController = AbortController;
vm.runInContext('renderImportExecution = data => { globalThis.lastProgress = data; };', context);
vm.runInContext("importBusy = true; pollImportProgress('/api/importaciones/progreso/no-disponible/');", context).then(() => {
    assert.equal(vm.runInContext('importBusy', context), false);
    assert.deepEqual(removed, ['fiberGeniusActiveImport']);
    assert.equal(context.lastProgress.etapa, 'Avance no disponible');
    console.log('Navegación de importaciones: sintaxis y 23 aserciones correctas.');
}).catch(error => { console.error(error); process.exitCode = 1; });
