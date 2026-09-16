(function () {
    'use strict';

    if (document.body.dataset.authenticated !== 'true') return;

    const storageKey = 'fg-site-context';
    const currentUrl = new URL(window.location.href);
    const urlSite = currentUrl.searchParams.get('site')?.trim() || '';
    const dashboardSite = document.querySelector('select[name="site"]')?.value.trim() || '';
    let storedSite = '';
    try { storedSite = sessionStorage.getItem(storageKey) || ''; } catch (_) { /* Storage opcional. */ }
    let activeSite = currentUrl.searchParams.has('site') ? urlSite : (dashboardSite || storedSite);

    const banner = document.getElementById('global-site-context');
    const name = document.getElementById('global-site-context-name');
    const clear = document.getElementById('global-site-context-clear');

    function render() {
        if (!banner) return;
        banner.hidden = !activeSite;
        if (name) name.textContent = activeSite ? `Site: ${activeSite}` : '';
    }

    function setSite(value) {
        activeSite = String(value || '').trim();
        try {
            if (activeSite) sessionStorage.setItem(storageKey, activeSite);
            else sessionStorage.removeItem(storageKey);
        } catch (_) { /* La selección sigue funcionando sin almacenamiento. */ }
        const url = new URL(window.location.href);
        if (activeSite) url.searchParams.set('site', activeSite);
        else url.searchParams.delete('site');
        window.history.replaceState(window.history.state, '', url.toString());
        render();
    }

    window.FGSiteContext = { get: () => activeSite, set: setSite };
    // Antes de inicializar las pestañas, haga visible en su URL el contexto heredado.
    setSite(activeSite);
    if (activeSite && !currentUrl.searchParams.has('site')
        && (currentUrl.pathname.endsWith('/inventario/dashboard/')
            || currentUrl.pathname.endsWith('/inventario/calidad/'))) {
        // Los contadores del Dashboard se calculan en servidor.
        window.location.replace(window.location.href);
        return;
    }

    function urlWithSite(rawUrl) {
        if (!activeSite) return rawUrl;
        const url = new URL(rawUrl, window.location.href);
        if (url.origin !== window.location.origin) return rawUrl;
        if (!url.searchParams.has('site')) url.searchParams.set('site', activeSite);
        return url.toString();
    }

    const originalFetch = window.fetch.bind(window);
    window.fetch = function (resource, options) {
        if (!activeSite) return originalFetch(resource, options);
        const method = String(options?.method || resource?.method || 'GET').toUpperCase();
        const rawUrl = typeof resource === 'string' || resource instanceof URL
            ? resource.toString()
            : resource.url;
        if (method !== 'GET' || !rawUrl) return originalFetch(resource, options);
        const url = new URL(rawUrl, window.location.href);
        if (
            url.origin !== window.location.origin
            || !url.pathname.startsWith('/api/inventario/')
        ) {
            return originalFetch(resource, options);
        }
        if (!url.searchParams.has('site')) url.searchParams.set('site', activeSite);
        if (typeof resource === 'string' || resource instanceof URL) {
            return originalFetch(url.toString(), options);
        }
        return originalFetch(new Request(url.toString(), resource), options);
    };

    document.addEventListener('change', event => {
        if (event.target.matches('select[name="site"]')) setSite(event.target.value);
    }, true);

    document.addEventListener('reset', event => {
        if (event.target.querySelector('select[name="site"]')) setSite('');
    }, true);

    document.addEventListener('submit', (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        const select = form.querySelector('select[name="site"]');
        if (select) {
            setSite(select.value);
            return;
        }
        if (activeSite && form.method.toLowerCase() === 'get' && !form.elements.site) {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'site';
            input.value = activeSite;
            form.appendChild(input);
        }
    }, true);

    document.addEventListener('click', (event) => {
        const link = event.target.closest('a[href]');
        if (!link) return;
        if (link.dataset.clearSiteContext === 'true') {
            setSite('');
            return;
        }
        if (!activeSite) return;
        const url = new URL(link.href, window.location.href);
        if (url.origin !== window.location.origin) return;
        link.href = urlWithSite(url);
    }, true);

    clear?.addEventListener('click', () => {
        setSite('');
        const url = new URL(window.location.href);
        url.searchParams.delete('site');
        window.location.assign(url.toString());
    });

    render();
})();
