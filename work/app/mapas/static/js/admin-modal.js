/* Formularios administrativos: validación recuperable, sin reenvíos automáticos. */
(() => {
    'use strict';
    const modal = document.getElementById('modal-dinamico');
    if (!modal) return;
    const content = document.getElementById('modal-contenido');
    const closer = document.getElementById('cerrar-modal-dinamico');
    let generation = 0, controller, saving = false, opener;
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-labelledby', 'modal-titulo');
    closer.setAttribute('aria-label', 'Cerrar formulario');

    function showError(message) {
        let alert = content.querySelector('[data-admin-error]');
        if (!alert) {
            alert = document.createElement('p');
            alert.dataset.adminError = 'true';
            alert.setAttribute('role', 'alert');
            alert.style.color = 'var(--danger, #b91c1c)';
            content.prepend(alert);
        }
        alert.textContent = message;
    }
    function checkResponse(response) {
        if (response.redirected || response.status === 401) throw new Error('La sesión o el acceso cambió. Abre de nuevo la página e inicia sesión si es necesario.');
        if (response.status === 403) throw new Error('Acceso denegado o sesión vencida. Revisa tus permisos y vuelve a abrir la página.');
        if (!response.ok) throw new Error('No se pudo confirmar el guardado. Revisa el listado antes de volver a enviar.');
    }
    function renderForm(html, url) {
        const parsed = new DOMParser().parseFromString(html, 'text/html');
        const panel = parsed.querySelector('#modal-ajax-content');
        if (!panel) throw new Error('Respuesta inesperada. Revisa el listado antes de volver a enviar.');
        panel.querySelectorAll('script').forEach(script => script.remove());
        panel.querySelectorAll('form').forEach(form => form.action = url);
        panel.querySelectorAll('.page-header').forEach(header => header.hidden = true);
        content.replaceChildren(panel);
        if (window.syncGroupPermissions) window.syncGroupPermissions();
        content.querySelector('input:not([type="hidden"]), button')?.focus();
    }
    function close() {
        if (saving) return;
        generation += 1;
        controller?.abort();
        modal.style.display = 'none';
        content.replaceChildren();
        opener?.focus();
    }
    document.addEventListener('click', async event => {
        const link = event.target.closest('.btn-modal-form');
        if (!link) return;
        event.preventDefault();
        if (saving) return;
        opener = link;
        const current = ++generation;
        controller?.abort();
        controller = new AbortController();
        document.getElementById('modal-titulo').textContent = link.dataset.titulo || 'Formulario';
        content.textContent = 'Cargando formulario…';
        modal.style.display = 'flex';
        closer.focus();
        try {
            const response = await fetch(link.href, {signal: controller.signal, headers: {'X-Requested-With': 'XMLHttpRequest'}, cache: 'no-store'});
            checkResponse(response);
            const html = await response.text();
            if (current === generation) renderForm(html, link.href);
        } catch (error) {
            if (current === generation && error.name !== 'AbortError') showError(error.message || 'No se pudo cargar el formulario.');
        }
    });
    content.addEventListener('submit', async event => {
        const form = event.target;
        if (form.tagName !== 'FORM') return;
        event.preventDefault();
        if (saving) return;
        saving = true;
        const data = new FormData(form);
        if (event.submitter?.name) data.append(event.submitter.name, event.submitter.value || '1');
        const buttons = [...content.querySelectorAll('button[type="submit"], input[type="submit"]')];
        const states = buttons.map(button => ({button, disabled: button.disabled, html: button.innerHTML}));
        buttons.forEach(button => { button.disabled = true; });
        if (event.submitter?.tagName === 'BUTTON') event.submitter.textContent = 'Guardando…';
        closer.disabled = true;
        const saveController = new AbortController();
        const timeout = setTimeout(() => saveController.abort(), 30000);
        try {
            const response = await fetch(form.action, {method: 'POST', body: data, signal: saveController.signal, headers: {'X-Requested-With': 'XMLHttpRequest'}});
            checkResponse(response);
            if ((response.headers.get('Content-Type') || '').includes('application/json')) {
                const result = await response.json();
                if (!result.success) throw new Error(result.error || 'No se pudo confirmar el guardado.');
                window.location.reload();
            } else {
                renderForm(await response.text(), form.action);
            }
        } catch (error) {
            showError(error instanceof TypeError || error.name === 'AbortError'
                ? 'Se perdió la conexión. El cambio podría haberse guardado: revisa el listado antes de volver a enviar.'
                : error.message);
        } finally {
            clearTimeout(timeout);
            saving = false;
            closer.disabled = false;
            states.forEach(({button, disabled, html}) => { button.disabled = disabled; button.innerHTML = html; });
        }
    });
    closer.addEventListener('click', close);
    modal.addEventListener('click', event => { if (event.target === modal) close(); });
    modal.addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); close(); }
        if (event.key !== 'Tab') return;
        const focusable = [...modal.querySelectorAll('a[href], button, input, select, textarea, [tabindex="0"]')].filter(el => !el.disabled && el.getClientRects().length);
        const first = focusable[0], last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    });
})();
