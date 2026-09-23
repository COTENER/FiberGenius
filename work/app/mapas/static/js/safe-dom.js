(function () {
    'use strict';

    const ENTITY_MAP = Object.freeze({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    });

    function escapeHTML(value) {
        if (value === null || value === undefined) return '';
        return String(value).replace(/[&<>"']/g, character => ENTITY_MAP[character]);
    }

    window.FiberGeniusSafe = Object.freeze({escapeHTML});
}());
