/* Regresiones del modal: prevalidación, recuperación y comunicación incierta. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../../mapas/templates/configuracion/configuracion_index.html'), 'utf8');
const source = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');

function setup() {
    const nodes = {};
    const make = () => ({style: {}, files: [], hidden: false, disabled: false,
        textContent: '', classList: {add() {}, remove() {}, toggle() {}},
        addEventListener() {}, replaceChildren() {}, appendChild() {},
        querySelector: () => ({setAttribute() {}}), setAttribute() {}});
    const document = {getElementById: id => nodes[id] ||= make(),
        addEventListener() {}, querySelectorAll: () => [], createElement: make};
    const timers = [], removed = [], rendered = [];
    const context = vm.createContext({document, AbortController,
        window: {setTimeout: (fn, ms) => { timers.push(ms); return timers.length; }, clearTimeout() {}},
        localStorage: {getItem: () => null, removeItem: key => removed.push(key)},
        fetch: async () => ({ok: true, status: 200, json: async () => ({})})});
    vm.runInContext(source, context);
    context.rendered = rendered;
    vm.runInContext('renderImportExecution = data => rendered.push(data);', context);
    return {context, nodes, timers, removed, rendered};
}

(async () => {
    let app = setup();
    app.nodes.importModalFile.files = [{}];
    vm.runInContext("currentImportType = 'sites_inventario';", app.context);
    app.context.fetch = async () => ({ok: true, status: 200, json: async () => ({
        estado: 'COMPLETADO', modo: 'validar', tipo: 'sites_inventario', validacion_token: 'token-del-servidor',
    })});
    await vm.runInContext("pollImportProgress('/avance');", app.context);
    assert.equal(app.nodes.submitImportFile.disabled, false);
    assert.equal(app.nodes.submitImportFile.textContent, 'Confirmar carga');
    assert.equal(vm.runInContext('importBusy', app.context), false);
    assert.equal(app.timers.includes(2200), false); // no recargar antes de confirmar

    app = setup();
    app.context.fetch = async () => ({ok: true, status: 200, json: async () => ({
        estado: 'COMPLETADO', modo: 'validar', validacion_token: 'token',
    })});
    await vm.runInContext("pollImportProgress('/avance');", app.context);
    assert.equal(app.nodes.submitImportFile.disabled, true); // archivo ya no disponible

    app = setup();
    app.context.fetch = async () => { throw Error('offline'); };
    vm.runInContext("lastImportProgress = {porcentaje: 45, estado: 'PROCESANDO'};", app.context);
    await vm.runInContext("pollImportProgress('/avance');", app.context);
    assert.equal(app.rendered.at(-1).porcentaje, 45);
    assert.equal(app.rendered.at(-1).estado, 'PROCESANDO');
    assert.equal(app.removed.length, 0);
    assert.equal(app.timers.at(-1), 3000);

    app = setup();
    app.context.fetch = async () => ({status: 200, redirected: true});
    await vm.runInContext("pollImportProgress('/avance');", app.context);
    assert.equal(app.rendered.at(-1).etapa, 'Sesión finalizada');
    assert.equal(app.removed.length, 0);
    assert.equal(app.timers.length, 1); // solo timeout de fetch, no bucle sobre login

    app = setup();
    app.context.fetch = async () => ({ok: true, status: 200, json: async () => ({estado: 'PROCESANDO'})});
    vm.runInContext("recoverImport('/avance');", app.context);
    assert.equal(app.nodes.importModalForm.hidden, false);
    assert.equal(app.nodes.importModalFile.disabled, true);
    assert.equal(app.nodes.submitImportFile.disabled, true);
    assert.equal(vm.runInContext('importBusy', app.context), true);
    console.log('Modal de importación: 16 aserciones de revisión y recuperación correctas.');
})().catch(error => { console.error(error); process.exitCode = 1; });
