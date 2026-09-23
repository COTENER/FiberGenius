const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../mapas/static/js/map-basemap.js'), 'utf8');
let checks = 0;
function eq(actual, expected) { assert.deepEqual(actual, expected); checks++; }
function setup({ missingKey = false, fallbackEnabled = true, theme = 'light', darkUrl = 'carto/dark', streetsUrl = 'esri/streets' } = {}) {
    const elements = [];
    function element(tag, className = '') {
        const node = { tag, className, dataset: {}, children: [], handlers: {}, hidden: false,
            setAttribute(k, v) { this[k] = v; }, appendChild(n) { this.children.push(n); },
            addEventListener(k, fn) { this.handlers[k] = fn; },
            classList: { add() {}, remove() {} },
        };
        elements.push(node); return node;
    }
    const target = element('div');
    const timers = new Map(); let nextTimer = 0;
    const tileLayers = [];
    const inventoryLayer = {};
    const layers = new Set([inventoryLayer]);
    const map = { on(k, fn) { this[k] = fn; }, removeLayer(l) { layers.delete(l); } };
    const config = {
        light: { url: 'carto/light', missingKey, attribution: 'CARTO' },
        dark: { url: darkUrl, missingKey, attribution: 'CARTO' },
        streets: { url: streetsUrl, attribution: 'Esri streets' },
        satellite: { url: 'esri/satellite', attribution: 'Esri' }, fallbackEnabled,
    };
    const doc = { getElementById: () => ({ textContent: JSON.stringify(config) }),
        documentElement: { getAttribute: () => theme }, createElement: element };
    const context = { document: doc, window: {
        setTimeout(fn) { timers.set(++nextTimer, fn); return nextTimer; },
        clearTimeout(id) { timers.delete(id); },
    }, L: {
        control: () => ({ addTo() { this.onAdd(); } }),
        DomUtil: { create(tag, cls, parent) { const n = element(tag, cls); if (parent) parent.appendChild(n); return n; } },
        DomEvent: { disableClickPropagation() {}, disableScrollPropagation() {} },
        tileLayer(url, options) {
            const l = { url, options, handlers: {},
                on(k, fn) { this.handlers[k] = fn; }, off() { this.handlers = {}; },
                addTo() { layers.add(this); }, bringToBack() {},
                emit(k) { if (this.handlers[k]) this.handlers[k](); },
            };
            tileLayers.push(l); return l;
        },
    } };
    vm.createContext(context); vm.runInContext(source, context);
    const controller = context.window.FGBaseMap(map, target);
    const select = elements.find(e => e.tag === 'select');
    return { controller, target, tileLayers, map, layers, inventoryLayer, timers, context,
        status: elements.find(e => e.role === 'status'),
        retry: elements.find(e => e.tag === 'button'),
        choose(mode) { select.value = mode; select.handlers.change(); },
        tick() { const work = [...timers.values()]; timers.clear(); work.forEach(fn => fn()); },
    };
}
function succeed(layer) { layer.emit('tileload'); layer.emit('load'); }
function fail(layer) { layer.emit('tileerror'); layer.emit('load'); }

let s = setup();
eq(s.tileLayers[0].url, 'carto/light');
eq(s.tileLayers[0].options.referrerPolicy, 'strict-origin-when-cross-origin');
succeed(s.tileLayers[0]); eq(s.timers.size, 0); eq(s.status.textContent, '');
s.choose('satellite'); eq(s.tileLayers.at(-1).url, 'esri/satellite');
eq(s.layers.has(s.inventoryLayer), true); eq(s.layers.size, 2);
s.controller.followTheme(); eq(s.tileLayers.length, 2); // Keep manual backup on theme changes.

s = setup(); const oldEvent = s.tileLayers[0].handlers.load;
fail(s.tileLayers[0]);
eq(s.tileLayers.length, 1); // Never remove a layer inside Leaflet's load callback.
s.tick(); eq(s.target.dataset.basemapTheme, 'streets');
eq(s.tileLayers.at(-1).url, 'esri/streets');
oldEvent(); eq(s.tileLayers.length, 2); // Ignore stale primary events.
fail(s.tileLayers[1]); eq(s.tileLayers.length, 2); eq(s.retry.hidden, false);
eq(s.layers.has(s.inventoryLayer), true); // Both providers unavailable, inventory stays.
s.retry.handlers.click(); eq(s.tileLayers.length, 3);
succeed(s.tileLayers[2]); eq(s.retry.hidden, true);
s.choose('light'); eq(s.tileLayers.at(-1).url, 'carto/light');

s = setup({ missingKey: true });
eq(s.tileLayers.length, 1); eq(s.tileLayers[0].url, 'esri/streets');
s.controller.followTheme(); eq(s.tileLayers.length, 1);

s = setup({ missingKey: true, fallbackEnabled: false });
eq(s.tileLayers.length, 0); eq(s.retry.hidden, false);
s = setup({ fallbackEnabled: false }); fail(s.tileLayers[0]);
eq(s.tileLayers.length, 1); eq(s.retry.hidden, false);

s = setup(); s.tick(); s.tick(); eq(s.tileLayers.at(-1).url, 'esri/streets');
s.tick(); eq(s.tileLayers.length, 2); eq(s.timers.size, 0);
s = setup(); s.map.unload(); eq(s.timers.size, 0); s.tick(); eq(s.tileLayers.length, 1);
s = setup(); fail(s.tileLayers[0]); s.choose('dark'); s.tick();
eq(s.tileLayers.at(-1).url, 'carto/dark'); // A manual selection cancels pending failover.

s = setup(); // A HTTP-200 watermark is a successful tile: manual backup remains available.
succeed(s.tileLayers[0]); eq(s.tileLayers.length, 1);
s.choose('satellite'); eq(s.tileLayers.length, 2);

s = setup(); // One failed tile out of four warns, does not force a provider switch.
s.tileLayers[0].emit('tileerror');
for (let i = 0; i < 3; i++) s.tileLayers[0].emit('tileload');
s.tileLayers[0].emit('load'); eq(s.tileLayers.length, 1); eq(s.retry.hidden, false);
s.tileLayers[0].emit('loading'); succeed(s.tileLayers[0]); eq(s.retry.hidden, true);
s = setup({ theme: 'dark', darkUrl: '' });
eq(s.tileLayers[0].url, 'carto/light'); // An internal installation may offer only one style.

s = setup(); s.choose('streets'); s.controller.followTheme();
eq(s.tileLayers.length, 2); eq(s.target.dataset.basemapTheme, 'streets');
fail(s.tileLayers.at(-1)); s.tick();
eq(s.tileLayers.length, 2); // Street outage must not switch to satellite without asking.
s.choose('satellite'); fail(s.tileLayers.at(-1)); s.tick();
eq(s.tileLayers.length, 3); eq(s.retry.hidden, false); // Manual satellite does not loop back.
s = setup({ missingKey: true, streetsUrl: '' });
eq(s.tileLayers.length, 0); eq(s.retry.hidden, false);
s = setup({ streetsUrl: 'carto/light' }); fail(s.tileLayers[0]); s.tick();
eq(s.tileLayers.length, 1); // No retry loop when both URLs are identical.

console.log(`Mapas: ${checks} comprobaciones JavaScript correctas.`);
