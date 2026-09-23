/* Small UI contracts; real-browser geometry checks remain in the stage 5 evidence. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const app = path.resolve(__dirname, '../..');
const read = relative => fs.readFileSync(path.join(app, 'mapas', relative), 'utf8');
let checks = 0;
function verify(value, message) { assert.ok(value, message); checks++; }
// Typography reverted at the user's request; do not enforce the removed 12px floor.
for (const [name, selector, size] of [
    ['odf-inventory', '.odf-table td', '10px'],
    ['odf-inventory', '.odf-table th', '9px'],
    ['puertos-odf', '.ports-table td', '10px'],
    ['network-inventory', '.network-donut small', '7px'],
]) {
    const css = read(`static/css/${name}.css`);
    const start = css.lastIndexOf(selector + ' {');
    verify(start >= 0 && css.slice(start, css.indexOf('}', start)).includes(`font-size: ${size}`), `${name}: original typography`);
}

for (const name of ['routes-inventory', 'fiber-inventory']) {
    const js = read(`static/js/${name}.js`);
    verify(js.includes("setAttribute('aria-label', `Ir a la página"), `${name}: page names`);
    verify(js.includes("setAttribute('aria-current', 'page')"), `${name}: active page`);
}
const filters = read('static/css/inventory-filters.css');
verify(filters.includes('repeat(auto-fit, minmax(min(100%, 180px), 1fr))'), 'filters fit content column');
const network = read('static/css/network-inventory.css');
verify(/\.network-state-summary\s*\{[^}]*grid-template-columns: 80px minmax\(0, 1fr\)/.test(network), 'original side legend');
verify(/\.network-inspector-card\s*\{[^}]*border: 1px solid/.test(network), 'original framed cards');
verify(!read('static/js/routes-inventory.js').includes('routeTraceChart'), 'later trace chart removed');
verify(read('static/js/routes-inventory.js').includes('else table.renderSummary(table.lastSummary)'), 'cached tab restores its own summary');
verify(read('static/js/routes-inventory.js').includes('`[data-resource="${kind}"]`)?.hidden'), 'inactive responses cannot replace visible summary');
for (const name of ['inventario_externo', 'planta_externa']) {
    const html = read(`templates/mapa_inventario/${name}.html`);
    for (const [direction, label] of [['previous', 'Página anterior'], ['next', 'Página siguiente']]) {
        const buttons = html.match(new RegExp(`<button[^>]+data-${direction}[^>]*>`, 'g'));
        verify(buttons?.length === 2 && buttons.every(b => b.includes(`aria-label="${label}"`)), `${name}: both tab pagers labeled`);
    }
}
verify(network.includes('.odf-analysis-main { grid-template-columns: minmax(0, 1fr); }'), 'ODF inner grid can shrink');
verify(/\.network-inspector__body\s*\{[^}]*grid-template-columns: minmax\(0, 1fr\)/.test(network), 'inspector cards can shrink');
verify(/\.network-ranking,\s*\.network-type-list\s*\{[^}]*grid-template-columns: minmax\(0, 1fr\)/.test(network), 'ranking tracks shrink inside cards');
verify(/\.network-ranking__item,\s*\.network-type-list__item\s*\{[^}]*min-width: 0/.test(network), 'ranking items do not impose intrinsic widths');

verify(/\.odf-table td, \.ports-table td\s*\{[^}]*overflow-wrap: anywhere/.test(network), 'identifiers wrap without dropping data');
const mapCss = read('static/css/mapa-red-v2.css');
verify(mapCss.includes('bottom: calc(var(--map-attribution-height, 20px) + 12px)'), 'map reserves attribution space');
const mapJs = read('static/js/mapa_inventario.js');
verify(mapJs.includes('attributionObserver.observe(attribution)'), 'tracks real attribution height');
verify(mapJs.includes("state.map.on('unload', () => attributionObserver.disconnect())"), 'observer is cleaned up');
console.log(`GUI stage 5: ${checks} UI contracts passed`);
