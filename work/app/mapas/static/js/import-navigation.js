/* Navegación por tareas: no ejecuta importaciones ni cambia datos de inventario. */
(() => {
    'use strict';
    const root = document.getElementById('importNavigation');
    if (!root) return;
    const guide = document.getElementById('odfConnectionGuide');
    const trigger = document.getElementById('startOdfGuide');
    const areas = [...root.querySelectorAll('[data-import-area]')];
    function setGuide(open, focus = true) {
        guide.hidden = !open;
        trigger.setAttribute('aria-expanded', String(open));
        const url = new URL(window.location.href);
        if (open) url.searchParams.set('tarea', 'odf-conexiones');
        else url.searchParams.delete('tarea');
        window.history.replaceState({}, '', url);
        if (focus) (open ? document.getElementById('odf-guide-title') : trigger).focus();
    }
    trigger.addEventListener('click', () => setGuide(guide.hidden));
    document.getElementById('closeOdfGuide').addEventListener('click', () => setGuide(false));
    if (new URL(window.location.href).searchParams.get('tarea') === 'odf-conexiones') setGuide(true, false);
    areas.forEach(area => area.addEventListener('toggle', () => {
        if (area.open) areas.forEach(other => { if (other !== area) other.open = false; });
    }));
    root.addEventListener('click', event => {
        const button = event.target.closest('[data-open-import]');
        if (!button || !root.contains(button)) return;
        openImportModal(button.dataset.openImport, button.dataset.importTitle);
    });
    // Entrada desde Sites: abre la misma carga, sin enviar ningún archivo.
    if (new URL(window.location.href).searchParams.get('importar') === 'sites_inventario') {
        window.addEventListener('load', () => {
            const button = root.querySelector('[data-open-import="sites_inventario"]');
            if (button && !button.disabled) {
                const area = button.closest('details');
                if (area) area.open = true;
                button.click();
            }
        }, {once: true});
    }
})();
