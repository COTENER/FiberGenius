// Init UI checks based on real inputs pre-selected
        function syncGroupPermissions() {
            document.querySelectorAll('.permiso-modern-card').forEach(card => {
                const hiddenCheckboxes = card.querySelectorAll("input.real-perm");
                const toggleInput = card.querySelector('.toggle-switch-sm input');

                if (toggleInput && hiddenCheckboxes.length > 0) {
                    // Turn ON if ANY hidden checkbox is ON
                    const isChecked = Array.from(hiddenCheckboxes).some(cb => cb.checked);
                    toggleInput.checked = isChecked;
                }
            });

            // Sincronizar el toggle maestro de Categoría
            document.querySelectorAll('.categoria-section').forEach(section => {
                const moduleToggles = section.querySelectorAll('.permiso-modern-card .toggle-switch-sm input[type="checkbox"]');
                const catToggle = section.querySelector('.cat-toggle-input');
                if (catToggle && moduleToggles.length > 0) {
                    // Turn ON si todos los módulos están ON, o al menos uno (depende de la lógica deseada, aquí hacemos si TODOS están ON o si ALGUNO está ON. Mejor si ALGUNO está ON)
                    const isChecked = Array.from(moduleToggles).some(toggle => toggle.checked);
                    catToggle.checked = isChecked;
                }
            });

        }

        function toggleModulePermissions(el) {
            const isChecked = el.checked;
            const card = el.closest('.permiso-modern-card');
            const hiddenCheckboxes = card.querySelectorAll("input.real-perm");
            hiddenCheckboxes.forEach(cb => {
                document.querySelectorAll('input.real-perm').forEach(other => {
                    if (other.value === cb.value) other.checked = isChecked;
                });
            });
            syncGroupPermissions();

            // Sincronizar hacia arriba a la categoría
            const section = el.closest('.categoria-section');
            if (section) {
                const moduleToggles = section.querySelectorAll('.permiso-modern-card .toggle-switch-sm input[type="checkbox"]');
                const catToggle = section.querySelector('.cat-toggle-input');
                if (catToggle) {
                    catToggle.checked = Array.from(moduleToggles).some(t => t.checked);
                }
            }
        }

        function toggleCategoryPermissions(el) {
            const isChecked = el.checked;
            const section = el.closest('.categoria-section');
            if (section) {
                const moduleToggles = section.querySelectorAll('.permiso-modern-card .toggle-switch-sm input[type="checkbox"]');
                moduleToggles.forEach(toggle => {
                    if (toggle.checked !== isChecked) {
                        toggle.checked = isChecked;
                        // Sincronizar hacia abajo a los hidden inputs
                        toggleModulePermissions(toggle);
                    }
                });
            }
        }

        syncGroupPermissions();
