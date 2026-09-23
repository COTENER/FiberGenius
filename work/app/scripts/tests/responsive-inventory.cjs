/* Breakpoint transitions: no browser or database required. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = path.resolve(__dirname, '../..');
const source = name => fs.readFileSync(path.join(app, 'mapas/static/js', name), 'utf8');
function element() {
    const classes = new Set();
    return {
        attrs: {}, handlers: {},
        classList: {contains: x => classes.has(x), toggle(x, enabled = !classes.has(x)) {
            if (enabled) classes.add(x); else classes.delete(x);
        }},
        setAttribute(k, v) { this.attrs[k] = v; },
        closest() { return null; },
        addEventListener(k, fn) { this.handlers[k] = fn; },
        scrollIntoView() { this.scrolled = true; }, focus() { this.focused = true; },
    };
}
function media(matches) {
    return {matches, addEventListener(_type, fn) { this.change = fn; },
        resize(value) { this.matches = value; this.change(); }};
}
for (const mobile of [false, true]) {
    const compact = media(mobile), inspector = element();
    let observer;
    const window = {matchMedia: () => compact};
    vm.runInNewContext(source('responsive-inventory.js'), {window, MutationObserver: class {
        constructor(fn) { observer = fn; } observe() {}
    }});
    window.FGResponsive.bindInspector(inspector, collapsed => inspector.classList.toggle('is-collapsed', collapsed));
    assert.equal(inspector.classList.contains('is-collapsed'), mobile);
    compact.resize(false);
    assert.equal(inspector.classList.contains('is-collapsed'), false);
    inspector.classList.toggle('is-collapsed', true); // User collapses on desktop.
    compact.resize(true);
    inspector.classList.toggle('is-collapsed', false); // Opens on mobile.
    observer(); assert.equal(inspector.scrolled, true);
    compact.resize(false);
    assert.equal(inspector.classList.contains('is-collapsed'), true); // Restores desktop choice.
}
for (const mobile of [false, true]) {
    const compact = media(mobile), values = {'fg-sidebar': 'collapsed'};
    const ids = Object.fromEntries(['sidebar','sidebar-toggle','sidebar-overlay','mobile-menu-toggle'].map(id => [id, element()]));
    const handlers = {};
    const document = {getElementById: id => ids[id], querySelector: () => null,
        documentElement: element(), addEventListener: (type, fn) => handlers[type] = fn};
    vm.runInNewContext(source('app.js'), {document, window: {matchMedia: () => compact},
        localStorage: {getItem: k => values[k], setItem: (k,v) => values[k] = v}});
    assert.equal(ids.sidebar.classList.contains('sidebar--collapsed'), !mobile);
    compact.resize(true);
    assert.equal(ids.sidebar.inert, true);
    assert.equal(ids.sidebar.classList.contains('sidebar--collapsed'), false);
    ids['mobile-menu-toggle'].handlers.click();
    assert.equal(ids.sidebar.inert, false);
    assert.equal(ids['mobile-menu-toggle'].attrs['aria-expanded'], 'true');
    handlers.keydown({key: 'Escape'});
    assert.equal(ids.sidebar.inert, true);
    assert.equal(ids['mobile-menu-toggle'].focused, true);
    ids['mobile-menu-toggle'].handlers.click();
    ids['sidebar-overlay'].handlers.click();
    assert.equal(ids['mobile-menu-toggle'].attrs['aria-expanded'], 'false');
    compact.resize(false);
    assert.equal(ids.sidebar.classList.contains('sidebar--collapsed'), true);
    assert.equal(ids.sidebar.inert, false);
    assert.equal(values['fg-sidebar'], 'collapsed');
}
const base = fs.readFileSync(path.join(app, 'mapas/templates/base.html'), 'utf8');
assert.ok(base.indexOf('responsive-inventory.js') < base.indexOf('{% block extra_js %}'));
console.log('PASS: responsive inspectors, desktop preference, mobile menu and keyboard');
