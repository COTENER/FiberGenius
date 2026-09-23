/* Scoped route summary: independent totals, no invented distribution. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../../mapas');
const source = fs.readFileSync(path.join(root, 'static/js/routes-inventory.js'), 'utf8');
const start = source.indexOf('    function renderContextSummary(');
const end = source.indexOf('    function renderRouteRanking(', start);
assert.ok(start > 0 && end > start);
class Node {
    constructor(tag) {
        this.tag = tag; this.children = []; this.style = {}; this.textContent = '';
        this.classes = new Set();
        this.classList = {toggle: (name, active) => active ? this.classes.add(name) : this.classes.delete(name)};
    }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.append(child); }
    replaceChildren(...children) { this.children = children; }
    closest() { return card; }
}
const card = new Node('section'), target = new Node('div'), footer = new Node('p');
let hidden = false, ranking;
const context = vm.createContext({
    number: new Intl.NumberFormat('es-PE', {maximumFractionDigits: 2}),
    root: {querySelector: () => ({hidden})},
    document: {createElement: tag => new Node(tag), getElementById: id => id === 'routes-context-summary' ? target : footer},
    renderRouteRanking: (items, kind) => { ranking = {items, kind}; },
});
vm.runInContext(source.slice(start, end), context);
function render(kind, summary) { context.renderContextSummary(kind, summary); }
function texts(node) { return typeof node === 'string' ? node : [node.textContent, ...node.children.map(texts)].join(' '); }
render('troncales', {total: 359, fibras: 2126, tramos: 359, reservas: 0, distancia_km: 1737.19});
assert.equal(target.children[0].className, 'network-route-totals__grid');
assert.equal(target.children[0].style.background, undefined);
assert.ok(target.classes.has('network-route-totals'));
assert.ok(card.classes.has('network-inspector-card--route-totals'));
assert.match(texts(target), /2,126/);
assert.match(texts(footer), /1,737.19.*km documentados/);
assert.doesNotMatch(texts(target), /por ruta|NaN|undefined/);
assert.equal(target.children[0].children.length, 4);
assert.deepEqual(target.children[0].children.map(tile => tile.children[0].textContent), ['Troncales', 'Fibras inventariadas', 'Tramos', 'Reservas físicas']);
assert.deepEqual(target.children[0].children.map(tile => tile.children[1].textContent), ['359', '2,126', '359', '0']);
render('tramos', {total: 3, aereos: 1, soterrados: 1});
assert.equal(target.children[0].className, 'network-donut');
assert.match(target.children[0].style.background, /conic-gradient/);
assert.equal(card.classes.has('network-inspector-card--route-totals'), false);
assert.equal(target.classes.has('network-route-totals'), false);
assert.match(texts(target), /33.33%/);
render('troncales', {});
assert.match(texts(target), /Troncales.*0/);
assert.doesNotMatch(texts(target), /NaN|undefined|%/);
const saved = texts(target);
hidden = true;
render('tramos', {total: 999});
assert.equal(texts(target), saved);
assert.equal(ranking.kind, 'troncales');
console.log('PASS: route totals, segment distribution, empty summary, tab isolation');
