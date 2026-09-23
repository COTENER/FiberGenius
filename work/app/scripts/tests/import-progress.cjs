/* Código real de la barra/sondeo, con red controlada y sin escrituras. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../mapas/templates/configuracion/configuracion_index.html'), 'utf8');
const nodes = {};
function node(id) {
    return nodes[id] ||= {hidden: true, textContent: '', style: {}, attrs: {},
        classList: {toggle() {}}, querySelector: key => node(`${id}/${key}`),
        setAttribute(key, value) { this.attrs[key] = value; }, appendChild() {}};
}
let result;
const scheduled = [];
const context = vm.createContext({AbortController, Date, Math, Number,
    document: {getElementById: node, createElement: node},
    window: {clearTimeout() {}, setTimeout(fn, delay) { scheduled.push({fn, delay}); return scheduled.length; }},
    fetch: async () => { if (result instanceof Error) throw result; return result; },
    lastImportProgress: {}, importPollTimer: null, importPollFailures: 0,
    rememberImport() {}, restoreImportControls() {},
});
function extract(from, to) { return source.slice(source.indexOf(from), source.indexOf(to, source.indexOf(from))); }
vm.runInContext(extract('function renderImportExecution(', 'function restoreImportControls(') +
    extract('async function pollImportProgress(', 'function startImportUpload('), context);

(async () => {
    context.renderImportExecution({estado: 'PROCESANDO', porcentaje: 65, total: 20000, procesadas: 5000});
    assert.equal(node('importExecutionPercent').textContent, '65 %');
    context.renderImportExecution({estado: 'PROCESANDO', porcentaje: 45, etapa: 'Respuesta atrasada'});
    assert.equal(node('importExecutionPercent').textContent, '65 %');
    assert.equal(context.lastImportProgress.procesadas, 5000);
    result = new Error('Network timeout');
    await context.pollImportProgress('/progress');
    assert.equal(node('importExecutionPercent').textContent, '65 %');
    assert.match(node('importExecutionMessage').textContent, /No vuelva/);
    assert.equal(context.importPollFailures, 1);
    result = {ok: false, status: 503};
    await context.pollImportProgress('/progress');
    assert.equal(node('importExecutionPercent').textContent, '65 %');
    assert.ok(scheduled.some(item => item.delay < 15000));
    result = {ok: true, status: 200, json: async () => ({estado: 'PROCESANDO', porcentaje: 93, etapa: 'Guardando'})};
    await context.pollImportProgress('/progress');
    assert.equal(node('importExecutionPercent').textContent, '93 %');
    assert.equal(context.importPollFailures, 0);
    result = {ok: false, status: 404};
    await context.pollImportProgress('/progress');
    assert.equal(node('importExecutionPercent').textContent, '93 %');
    context.renderImportExecution({estado: 'COMPLETADO', porcentaje: 100});
    assert.equal(node('importExecutionPercent').textContent, '100 %');
    // Una carga nueva reinicia su propio progreso, no hereda la anterior.
    assert.match(source, /function startImportUpload[^]*?lastImportProgress = \{\};/);
    console.log('Import progress: timeout, 503, recovery, stale response, counts and final state OK.');
})().catch(error => { console.error(error); process.exitCode = 1; });
