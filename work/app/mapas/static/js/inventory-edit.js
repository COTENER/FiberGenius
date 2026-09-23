/* Envía intención de edición, no la copia completa de un formulario antiguo. */
window.InventoryEdit = {
    snapshot(form) {
        form.dataset.original = JSON.stringify(Object.fromEntries(new FormData(form).entries()));
    },
    patch(current, original, identity = []) {
        const result = { _original: original };
        for (const [key, value] of Object.entries(current)) {
            if (key === 'csrfmiddlewaretoken') continue;
            if (identity.includes(key) || String(value ?? '') !== String(original[key] ?? '')) result[key] = value;
        }
        return result;
    },
    formPatch(form, identity = []) {
        return this.patch(Object.fromEntries(new FormData(form).entries()), JSON.parse(form.dataset.original || '{}'), identity);
    }
};
