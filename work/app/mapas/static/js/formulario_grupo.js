    // 1. "Todo" dentro de la tarjeta
    function toggleAllCard(source) {
        const card = source.closest('.permiso-card');
        
        // Si es la tarjeta de Mapas, marcamos los maestros Y los ocultos
        if (card.querySelector('.hidden-real-inputs')) {
            const allInputs = card.querySelectorAll('input[type="checkbox"]'); // Incluye maestros y ocultos
            allInputs.forEach(cb => {
                if (cb !== source) cb.checked = source.checked;
            });
        } else {
            // Tarjetas normales
            const checkboxes = card.querySelectorAll('input[name="permissions"]');
            checkboxes.forEach(cb => cb.checked = source.checked);
        }
    }

    // 2. Lógica para la tarjeta unificada (Mapas)
    function toggleUnified(actionType, source) {
        // Marca todos los ocultos correspondientes
        console.log("Toggle Unified llamado para:", actionType);
        const targetClass = '.type-' + actionType;
        const realCheckboxes = document.querySelectorAll(targetClass);

        console.log("Checkboxes encontrados:", realCheckboxes.length);
        realCheckboxes.forEach(cb => cb.checked = source.checked);

        
        
        // Verifica el checkbox "Todo" de la tarjeta por si acaso
        checkMasterAllStatus(source);
    }

    // Función auxiliar para actualizar el "Todo" global si se marca todo manual
    function checkMasterAllStatus(source) {
        const card = source.closest('.permiso-card');
        const masterAll = card.querySelector('.select-all-wrapper input');
        // Simple UX: si desmarcas uno, desmarcas "Todo".
        if (!source.checked && masterAll) masterAll.checked = false;
    }

    // 3. Script de Inicialización (Al Cargar/Editar)
    document.addEventListener("DOMContentLoaded", function() {
        const actions = ['add', 'change', 'delete', 'view','reports'];
        
        actions.forEach(action => {
            const realCheckboxes = document.querySelectorAll('.type-' + action);
            if (realCheckboxes.length > 0) {
                // Si TODOS los permisos ocultos de ese tipo están marcados, marcamos el visual
                const allChecked = Array.from(realCheckboxes).every(cb => cb.checked);
                if (allChecked) {
                    const masterCheckbox = document.getElementById('master_' + action);
                    if (masterCheckbox) masterCheckbox.checked = true;
                }
            }
        });
    });