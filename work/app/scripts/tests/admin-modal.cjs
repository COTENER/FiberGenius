/* Pruebas aisladas de la lógica del modal; no automatiza ningún navegador. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../mapas/static/js/admin-modal.js'), 'utf8');
let assertions = 0;
function eq(actual, expected) { assert.equal(actual, expected); assertions++; }
function setup(fetchImpl) {
    const listeners = {}, dom = {};
    let reloads = 0, parsed = 0, lastData;
    const button = {tagName: 'BUTTON', name: 'update_profile', value: '1', disabled: false, innerHTML: 'Guardar'};
    const form = {tagName: 'FORM', action: '/usuarios/7/editar/', value: 'Nombre escrito'};
    for (const id of ['modal-dinamico', 'modal-contenido', 'cerrar-modal-dinamico', 'modal-titulo']) {
        dom[id] = {style: {}, disabled: false, setAttribute() {}, focus() {}, addEventListener(type, fn) { listeners[id + ':' + type] = fn; }};
    }
    const content = dom['modal-contenido'];
    content.querySelectorAll = () => [button];
    content.querySelector = selector => selector === '[data-admin-error]' ? content.alert : null;
    content.prepend = element => content.alert = element;
    content.replaceChildren = panel => {content.panel = panel; content.alert = null;};
    const document = {getElementById: id => dom[id], addEventListener(type, fn) { listeners['document:' + type] = fn; }, createElement() {return {dataset: {}, style: {}, setAttribute() {}};}};
    const ctx = {
        document, AbortController, TypeError, console, setTimeout, clearTimeout,
        FormData: class {constructor(form) {this.form = form; this.values = []; lastData = this;} append(...pair) {this.values.push(pair);}},
        DOMParser: class {parseFromString(html) {
            parsed++;
            const panel = {html, querySelectorAll(selector) {return selector === 'form' ? [form] : [];}};
            return {querySelector() {return html === 'unexpected' ? null : panel;}};
        }},
        window: {location: {reload() {reloads++;}}},
        fetch: fetchImpl,
    };
    vm.runInNewContext(source, ctx);
    return {dom, form, button, content, ctx, listeners, submit: () => listeners['modal-contenido:submit']({target: form, preventDefault(){}, submitter: button}), reloads: () => reloads, data: () => lastData, parsed: () => parsed};
}
const response = (status=200, body={success: true}, type='application/json', redirected=false) => ({status, ok:status < 400, redirected, headers: {get:()=>type}, json:async()=>body, text:async()=>body});
(async () => {
    for (const status of [403, 401, 500]) {
        const env = setup(async()=>response(status));
        await env.submit();
        eq(env.button.disabled, false);
        eq(env.button.innerHTML, 'Guardar');
        eq(env.form.value, 'Nombre escrito');
        eq(env.reloads(), 0);
        eq(!!env.content.alert.textContent, true);
    }
    {
        const env = setup(async()=>{throw new TypeError('network');});
        await env.submit();
        eq(env.button.disabled, false);
        eq(env.content.alert.textContent.includes('podría haberse guardado'), true);
    }
    {
        const env = setup(async()=>response(200, 'login', 'text/html', true));
        await env.submit();
        eq(env.reloads(), 0);
        eq(env.button.disabled, false);
    }
    {
        let calls=0;
        const env = setup(async()=>++calls === 1 ? response(200, 'invalid form', 'text/html') : response());
        await env.submit();
        eq(env.content.panel.html, 'invalid form');
        eq(env.reloads(), 0);
        await env.submit();
        eq(env.reloads(), 1);
        eq(env.data().values[0][0], 'update_profile');
    }
    {
        let resolve, calls=0;
        const env = setup(()=>{calls++;return new Promise(r=>resolve=r);});
        const pending = env.submit();
        eq(env.button.disabled, true);
        await env.submit();
        eq(calls, 1);
        env.listeners['cerrar-modal-dinamico:click']();
        eq(env.dom['modal-dinamico'].style.display, undefined);
        resolve(response());
        await pending;
        eq(env.reloads(), 1);
    }
    {
        const env = setup(async()=>response(200, 'unexpected', 'text/html'));
        await env.submit();
        eq(env.button.disabled, false);
        eq(env.content.alert.textContent.includes('Respuesta inesperada'), true);
    }
    {
        const pending=[];
        const env=setup(()=>new Promise(resolve=>pending.push(resolve)));
        const click=url=>env.listeners['document:click']({preventDefault(){},target:{closest(){return {href:url,dataset:{titulo:url},focus(){}};}}});
        const first=click('/usuarios/1/editar/');
        const second=click('/usuarios/2/editar/');
        pending[1](response(200,'usuario 2','text/html')); await second;
        pending[0](response(200,'usuario 1','text/html')); await first;
        eq(env.content.panel.html,'usuario 2');
    }
    {
        const inputs = [{value:'7',checked:true},{value:'7',checked:true}];
        const toggles = [{checked:false},{checked:false}];
        const cards = inputs.map((input,i)=>({querySelectorAll(){return [input];},querySelector(){return toggles[i];}}));
        toggles.forEach((toggle,i)=>toggle.closest = selector=>selector==='.permiso-modern-card'?cards[i]:null);
        const context = {document:{querySelectorAll(selector){
            if(selector==='.permiso-modern-card') return cards;
            if(selector==='input.real-perm') return inputs;
            return [];
        }}};
        vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../mapas/static/js/group-permissions.js'),'utf8'), context);
        eq(toggles[0].checked,true); eq(toggles[1].checked,true);
        toggles[0].checked=false;
        context.toggleModulePermissions(toggles[0]);
        eq(inputs[0].checked,false); eq(inputs[1].checked,false);
        eq(toggles[0].checked,false); eq(toggles[1].checked,false);
    }
    console.log(`Administración: ${assertions} comprobaciones JavaScript correctas.`);
})().catch(error=>{console.error(error);process.exitCode=1;});
