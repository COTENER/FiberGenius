/* =============================================================================
   APP.JS — Core interactivity: sidebar, theme, user menu, responsive
   ============================================================================= */

(function () {
    'use strict';

    // ─── DOM References ───
    const sidebar       = document.getElementById('sidebar');
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebarOverlay= document.getElementById('sidebar-overlay');
    const mobileToggle  = document.getElementById('mobile-menu-toggle');
    const themeToggle   = document.getElementById('theme-toggle');
    const userDropdown  = document.getElementById('user-dropdown');
    const userTrigger   = document.getElementById('user-dropdown-trigger');

    // ─── Sidebar Toggle (Desktop) ───
    function applySidebarState(collapsed) {
        if (!sidebar) return;
        sidebar.classList.toggle('sidebar--collapsed', collapsed);
        localStorage.setItem('fg-sidebar', collapsed ? 'collapsed' : 'expanded');
        if (sidebarToggle) {
            sidebarToggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            sidebarToggle.setAttribute('aria-label', collapsed ? 'Expandir menú principal' : 'Colapsar menú principal');
        }
    }

    // Restore sidebar state
    if (window.innerWidth > 1024) {
        const saved = localStorage.getItem('fg-sidebar');
        if (saved === 'collapsed') applySidebarState(true);
    }

    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', function () {
            const isCollapsed = sidebar.classList.contains('sidebar--collapsed');
            applySidebarState(!isCollapsed);
        });
    }

    // ─── Mobile Sidebar ───
    if (mobileToggle) {
        mobileToggle.addEventListener('click', function () {
            sidebar.classList.toggle('mobile-open');
            const open = sidebar.classList.contains('mobile-open');
            mobileToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            mobileToggle.setAttribute('aria-label', open ? 'Cerrar menú principal' : 'Abrir menú principal');
        });
    }
    if (sidebarOverlay) {
        sidebarOverlay.addEventListener('click', function () {
            sidebar.classList.remove('mobile-open');
            if (mobileToggle) mobileToggle.setAttribute('aria-expanded', 'false');
        });
    }

    // ─── Theme Toggle ───
    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('fg-theme', theme);
        if (themeToggle) {
            themeToggle.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false');
            themeToggle.setAttribute('title', theme === 'dark' ? 'Cambiar a tema claro' : 'Cambiar a tema oscuro');
        }

        // Toggle icon visibility
        var sunIcon  = document.querySelector('.icon-sun');
        var moonIcon = document.querySelector('.icon-moon');
        if (sunIcon && moonIcon) {
            if (theme === 'dark') {
                sunIcon.style.display = 'none';
                moonIcon.style.display = 'block';
            } else {
                sunIcon.style.display = 'block';
                moonIcon.style.display = 'none';
            }
        }
    }

    // Initialize theme icons
    var currentTheme = localStorage.getItem('fg-theme') || 'light';
    applyTheme(currentTheme);

    if (themeToggle) {
        themeToggle.addEventListener('click', function () {
            var current = document.documentElement.getAttribute('data-theme');
            applyTheme(current === 'dark' ? 'light' : 'dark');
        });
    }

    // ─── User Dropdown ───
    if (userTrigger && userDropdown) {
        userTrigger.addEventListener('click', function (e) {
            e.stopPropagation();
            userDropdown.classList.toggle('open');
            userTrigger.setAttribute('aria-expanded', userDropdown.classList.contains('open') ? 'true' : 'false');
        });

        document.addEventListener('click', function (e) {
            if (!userDropdown.contains(e.target)) {
                userDropdown.classList.remove('open');
                userTrigger.setAttribute('aria-expanded', 'false');
            }
        });
    }

    // ─── Close mobile sidebar on window resize ───
    window.addEventListener('resize', function () {
        if (window.innerWidth > 1024 && sidebar) {
            sidebar.classList.remove('mobile-open');
            if (mobileToggle) mobileToggle.setAttribute('aria-expanded', 'false');
        }
    });
})();
