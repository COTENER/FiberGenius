const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
let assertions = 0;
for (const name of ['fiber-inventory.js', 'routes-inventory.js']) {
    const source = fs.readFileSync(path.join(__dirname, '../../mapas/static/js', name), 'utf8');
    const start = source.indexOf("        const dark = document.documentElement.dataset.theme");
    const end = source.indexOf(name === 'fiber-inventory.js' ? '        const bounds =' : '        const line =', start);
    assert.ok(start >= 0 && end > start); assertions++;
    for (const theme of ['light', 'dark']) {
        let selected;
        const routeMap = {};
        vm.runInNewContext(source.slice(start, end), {
            document: {documentElement: {dataset: {theme}}, body: {dataset: {
                mapTileLight: 'https://maps.example.test/light/{z}/{x}/{y}',
                mapTileDark: 'https://maps.example.test/dark/{z}/{x}/{y}',
                mapAttribution: 'Approved provider',
            }}}, routeMap,
            L: {tileLayer: (url, options) => {
                selected = {url, options};
                return {addTo: target => { assert.equal(target, routeMap); assertions++; }};
            }},
        });
        assert.equal(selected.url, `https://maps.example.test/${theme}/{z}/{x}/{y}`); assertions++;
        assert.equal(selected.options.attribution, 'Approved provider'); assertions++;
    }
    vm.runInNewContext(source.slice(start, end), {
        document: {documentElement: {dataset: {theme: 'light'}}, body: {dataset: {}}},
        routeMap: {}, L: {tileLayer: () => assert.fail('No external provider fallback allowed')},
    });
    assertions++;
}
console.log(`Map provider: ${assertions} assertions passed`);
