const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
let assertions = 0;
for (const [name, template] of [['fiber-inventory.js', 'planta_externa.html'], ['routes-inventory.js', 'inventario_externo.html']]) {
    const source = fs.readFileSync(path.join(__dirname, '../../mapas/static/js', name), 'utf8');
    const hook = 'window.FGBaseMap(routeMap, target);';
    assert.equal(source.split(hook).length, 2); assertions++;
    assert.equal(source.includes('L.tileLayer('), false); assertions++;
    const routeMap = {}, target = {};
    vm.runInNewContext(hook, { routeMap, target, window: { FGBaseMap(map, element) {
        assert.equal(map, routeMap); assertions++;
        assert.equal(element, target); assertions++;
    } } });
    const html = fs.readFileSync(path.join(__dirname, '../../mapas/templates/mapa_inventario', template), 'utf8');
    assert.ok(html.includes('fg_map_config|json_script:"fg-map-config"')); assertions++;
    assert.ok(html.indexOf('js/map-basemap.js') < html.indexOf('js/' + name)); assertions++;
}
console.log(`Map provider: ${assertions} assertions passed`);
