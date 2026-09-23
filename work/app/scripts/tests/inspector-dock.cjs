/* Layout-only docking: no API calls or changes to inventory state. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../mapas/static/js/responsive-inventory.js'), 'utf8');
const classes = new Set(), styles = {}, events = {}, frames = [];
let onMutation, onResize, track = 286, right = 1416;
const compact = {matches:false, addEventListener: (_name, fn) => compact.change = fn};
const main = {scrollTop:0, getBoundingClientRect: () => ({top:56})};
const anchor = {getClientRects: () => [{}], getBoundingClientRect: () => ({top:400-main.scrollTop})};
const workbench = {querySelectorAll: () => [anchor], getBoundingClientRect: () => ({right})};
const inspector = {parentElement:workbench, closest: () => main,
    style:{setProperty: (name, value) => {styles[name] = value;}},
    scrollIntoView() {},
    classList:{contains: name => classes.has(name), add: name => classes.add(name),
        remove: name => classes.delete(name), toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name)},
};
const window = {innerHeight:900, matchMedia: () => compact, addEventListener: (name, fn) => {events[name] = fn;}};
vm.runInNewContext(source, {window, getComputedStyle: () => ({gridTemplateColumns:`856px ${track}px`}),
    requestAnimationFrame: fn => {frames.push(fn); return frames.length;},
    ResizeObserver: class {constructor(fn) {onResize=fn;} observe() {}},
    MutationObserver: class {constructor(fn) {onMutation=fn;} observe() {}},
});
const flush = () => {const pending=frames.splice(0); pending.forEach(fn => fn());};
window.FGResponsive.bindInspector(inspector, value => inspector.classList.toggle('is-collapsed', value));
flush();
assert.equal(classes.has('is-docked'), true);
assert.equal(styles['--inspector-dock-top'], '400px');
assert.equal(styles['--inspector-dock-left'], '1130px');
main.scrollTop=600; onResize(); flush();
assert.equal(styles['--inspector-dock-top'], '400px');
track=46; classes.add('is-collapsed'); onMutation(); flush();
assert.equal(styles['--inspector-dock-width'], '46px');
assert.equal(styles['--inspector-dock-left'], '1370px');
window.innerHeight=400; events.resize(); flush();
assert.equal(styles['--inspector-dock-top'], '180px');
compact.matches=true; compact.change(); flush();
assert.equal(classes.has('is-docked'), false);
compact.matches=false; compact.change(); right=1200; onResize(); flush();
assert.equal(classes.has('is-docked'), true);
assert.equal(styles['--inspector-dock-left'], '1154px');
onResize(); onResize(); onMutation();
assert.equal(frames.length, 1, 'batch layout reads into one frame');
console.log('PASS: fixed anchor, page scroll, collapse, resize, mobile and batched updates');
