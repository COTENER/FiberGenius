(function () {
    'use strict';

    const FG = window.FiberGenius = window.FiberGenius || {};

    FG.notify = function (message, type = 'info') {
        const region = document.getElementById('fg-toast-region');
        if (!region) return;
        const toast = document.createElement('div');
        toast.className = `fg-toast fg-toast--${type}`;
        toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
        toast.textContent = message;
        region.appendChild(toast);
        window.setTimeout(() => toast.remove(), 5000);
    };

    function focusables(container) {
        return Array.from(container.querySelectorAll(
            'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
            'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )).filter((element) => !element.hidden && element.offsetParent !== null);
    }

    function enhanceLegacyDialogs() {
        const candidates = document.querySelectorAll('.modal-overlay, [id$="Modal"]');
        candidates.forEach((overlay, index) => {
            if (overlay.dataset.fgDialogEnhanced === 'true') return;
            const dialog = overlay.matches('.modal-content')
                ? overlay
                : overlay.querySelector('.modal-content') || overlay.firstElementChild;
            if (!dialog || dialog === overlay && !overlay.matches('.modal-content')) return;

            overlay.dataset.fgDialogEnhanced = 'true';
            overlay.setAttribute('aria-hidden', 'true');
            dialog.setAttribute('role', 'dialog');
            dialog.setAttribute('aria-modal', 'true');
            dialog.setAttribute('tabindex', '-1');

            const heading = dialog.querySelector('h1, h2, h3, [class*="modal-title"]');
            if (heading) {
                if (!heading.id) heading.id = `fg-dialog-title-${index}`;
                dialog.setAttribute('aria-labelledby', heading.id);
            } else {
                dialog.setAttribute('aria-label', 'Ventana de Fiber Genius');
            }

            const syncState = () => {
                const visible = !overlay.hidden && window.getComputedStyle(overlay).display !== 'none';
                overlay.setAttribute('aria-hidden', visible ? 'false' : 'true');
                if (visible && overlay.dataset.fgWasOpen !== 'true') {
                    overlay.dataset.fgWasOpen = 'true';
                    overlay._fgPreviousFocus = document.activeElement;
                    window.setTimeout(() => {
                        const items = focusables(dialog);
                        (items[0] || dialog).focus();
                    }, 0);
                } else if (!visible && overlay.dataset.fgWasOpen === 'true') {
                    overlay.dataset.fgWasOpen = 'false';
                    if (overlay._fgPreviousFocus && document.contains(overlay._fgPreviousFocus)) {
                        overlay._fgPreviousFocus.focus();
                    }
                }
            };

            new MutationObserver(syncState).observe(overlay, {
                attributes: true,
                attributeFilter: ['style', 'class', 'hidden'],
            });
            syncState();
        });
    }

    function visibleLegacyDialog() {
        return Array.from(document.querySelectorAll('.modal-overlay, [id$="Modal"]'))
            .filter((overlay) => overlay.dataset.fgDialogEnhanced === 'true')
            .filter((overlay) => overlay.getAttribute('aria-hidden') === 'false')
            .pop();
    }

    document.addEventListener('keydown', (event) => {
        const overlay = visibleLegacyDialog();
        if (!overlay) return;
        const dialog = overlay.querySelector('[role="dialog"]');

        if (event.key === 'Escape') {
            const close = overlay.querySelector(
                '[data-dialog-close], .btn-close, [onclick*="cerrar"], [onclick*="closeModal"]'
            );
            if (close) close.click();
            else overlay.style.display = 'none';
            event.preventDefault();
            return;
        }
        if (event.key !== 'Tab' || !dialog) return;
        const items = focusables(dialog);
        if (!items.length) {
            event.preventDefault();
            dialog.focus();
            return;
        }
        const first = items[0];
        const last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });

    document.addEventListener('DOMContentLoaded', () => {
        enhanceLegacyDialogs();

        const mobileToggle = document.getElementById('mobile-menu-toggle');
        const sidebar = document.getElementById('sidebar');
        if (mobileToggle && sidebar) {
            mobileToggle.addEventListener('click', () => {
                window.setTimeout(() => {
                    mobileToggle.setAttribute('aria-expanded', sidebar.classList.contains('mobile-open') ? 'true' : 'false');
                }, 0);
            });
        }

        document.querySelectorAll('.card[onclick]').forEach((card) => {
            if (!card.hasAttribute('role')) card.setAttribute('role', 'button');
            if (!card.hasAttribute('tabindex')) card.setAttribute('tabindex', '0');
            card.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    card.click();
                }
            });
        });

        document.querySelectorAll('input[placeholder], textarea[placeholder]').forEach((field) => {
            if (!field.labels?.length && !field.hasAttribute('aria-label') && !field.hasAttribute('aria-labelledby')) {
                field.setAttribute('aria-label', field.getAttribute('placeholder'));
            }
        });
        document.querySelectorAll('button[title]').forEach((button) => {
            if (!button.hasAttribute('aria-label') && !button.textContent.trim()) {
                button.setAttribute('aria-label', button.getAttribute('title'));
            }
        });

        const deepLinkQuery = new URLSearchParams(window.location.search).get('q');
        if (deepLinkQuery) {
            ['searchRutasMain', 'searchODFMain'].forEach((id) => {
                const field = document.getElementById(id);
                if (field) {
                    field.value = deepLinkQuery;
                    field.dispatchEvent(new Event('input', { bubbles: true }));
                }
            });
        }

        const userTrigger = document.getElementById('user-dropdown-trigger');
        const userDropdown = document.getElementById('user-dropdown');
        if (userTrigger && userDropdown) {
            const observer = new MutationObserver(() => {
                userTrigger.setAttribute('aria-expanded', userDropdown.classList.contains('open') ? 'true' : 'false');
            });
            observer.observe(userDropdown, { attributes: true, attributeFilter: ['class'] });
        }
    });
})();
