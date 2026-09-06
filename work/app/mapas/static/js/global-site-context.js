(function () {
    'use strict';

    if (document.body.dataset.authenticated !== 'true') return;

    const storageKey = 'fg-site-context';
    const currentUrl = new URL(window.location.href);
    const urlSite = currentUrl.searchParams.get('site')?.trim() || '';
    const dashboardSite = document.querySelector('select[name="site"]')?.value.trim() || '';
    let activeSite = urlSite || dashboardSite || sessionStorage.getItem(storageKey) || '';

    if (urlSite || dashboardSite) {
        activeSite = urlSite || dashboardSite;
        sessionStorage.setItem(storageKey, activeSite);
    }

    const banner = document.getElementById('global-site-context');
    const name = document.getElementById('global-site-context-name');
    const clear = document.getElementById('global-site-context-clear');

    function render() {
        if (!banner) return;
        banner.hidden = !activeSite;
        if (name) name.textContent = activeSite ? `Site: ${activeSite}` : '';
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
        url.searchParams.set('site', activeSite);
        if (typeof resource === 'string' || resource instanceof URL) {
            return originalFetch(url.toString(), options);
        }
        return originalFetch(new Request(url.toString(), resource), options);
    };

    document.addEventListener('submit', (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        const select = form.querySelector('select[name="site"]');
        if (select) {
            activeSite = select.value.trim();
            if (activeSite) sessionStorage.setItem(storageKey, activeSite);
            else sessionStorage.removeItem(storageKey);
            render();
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
            sessionStorage.removeItem(storageKey);
            activeSite = '';
            return;
        }
        if (!activeSite) return;
        const url = new URL(link.href, window.location.href);
        if (url.origin !== window.location.origin) return;
        link.href = urlWithSite(url);
    }, true);

    clear?.addEventListener('click', () => {
        sessionStorage.removeItem(storageKey);
        activeSite = '';
        const url = new URL(window.location.href);
        url.searchParams.delete('site');
        window.location.assign(url.toString());
    });

    render();
})();
