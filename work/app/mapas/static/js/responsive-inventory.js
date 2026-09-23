/* Shared layout transitions only: inventory data and selection stay untouched. */
(function () {
    'use strict';
    function bindInspectorDock(inspector, compact) {
        const workbench = inspector.parentElement;
        const scrollport = inspector.closest('.main-content');
        if (!workbench || !scrollport) return () => {};
        let frame = 0;
        const update = () => {
            frame = 0;
            if (compact.matches) {
                if (inspector.classList.contains('is-docked')) inspector.classList.remove('is-docked');
                return;
            }
            const anchors = [...workbench.querySelectorAll('.odf-filter-card, .ports-filter-card')];
            const anchor = anchors.find(node => node.getClientRects().length);
            if (!anchor) return;
            // The grid still reserves the right column. Pin to its natural
            // filter position, not the scrolling rectangle of the inspector.
            const bounds = workbench.getBoundingClientRect();
            const tracks = getComputedStyle(workbench).gridTemplateColumns.split(' ');
            const width = parseFloat(tracks[tracks.length - 1]);
            if (!Number.isFinite(width) || width <= 0) return;
            const minTop = scrollport.getBoundingClientRect().top + 12;
            const naturalTop = anchor.getBoundingClientRect().top + scrollport.scrollTop;
            const top = Math.max(minTop, Math.min(naturalTop, window.innerHeight - 220));
            inspector.style.setProperty('--inspector-dock-top', `${top}px`);
            inspector.style.setProperty('--inspector-dock-left', `${bounds.right - width}px`);
            inspector.style.setProperty('--inspector-dock-width', `${width}px`);
            if (!inspector.classList.contains('is-docked')) inspector.classList.add('is-docked');
        };
        const schedule = () => {
            if (!frame) frame = requestAnimationFrame(update);
        };
        const observer = new ResizeObserver(schedule);
        observer.observe(workbench);
        observer.observe(scrollport);
        workbench.querySelectorAll('.odf-filter-card, .ports-filter-card').forEach(node => observer.observe(node));
        window.addEventListener('resize', schedule);
        schedule();
        return schedule;
    }
    window.FGResponsive = {
        bindInspector(inspector, setCollapsed) {
            if (!inspector) return;
            const compact = window.matchMedia('(max-width: 1180px)');
            let desktopCollapsed = false;
            setCollapsed(compact.matches);
            const updateDock = bindInspectorDock(inspector, compact);
            compact.addEventListener('change', () => {
                if (compact.matches) desktopCollapsed = inspector.classList.contains('is-collapsed');
                setCollapsed(compact.matches || desktopCollapsed);
                updateDock();
            });
            // Selection can expand the panel from a row far below the header.
            new MutationObserver(() => {
                updateDock();
                if (compact.matches && !inspector.classList.contains('is-collapsed')) {
                    inspector.scrollIntoView({block: 'nearest'});
                }
            }).observe(inspector, {attributes: true, attributeFilter: ['class']});
        },
    };
})();
