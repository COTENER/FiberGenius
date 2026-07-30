document.addEventListener('DOMContentLoaded', () => {

    // =========================================================
    // 1. LÓGICA GENÉRICA PARA MENÚS DESPLEGABLES (.user-menu)
    // =========================================================
    // Seleccionamos TODOS los menús (Usuario, Reportes, etc.)
    const dropdowns = document.querySelectorAll('.user-menu');

    dropdowns.forEach(menu => {
        // Buscamos el botón que activa el menú dentro del contenedor
        const toggleButton = menu.querySelector('.action-button'); 
        // Buscamos la lista de opciones
        const optionsList = menu.querySelector('.user-menu__options');

        if (toggleButton && optionsList) {
            toggleButton.addEventListener('click', (e) => {
                e.stopPropagation(); // Evita que el clic cierre inmediatamente el menú
                
                // Primero cerramos CUALQUIER OTRO menú que esté abierto para que no se solapen
                dropdowns.forEach(otherMenu => {
                    if (otherMenu !== menu) {
                        otherMenu.classList.remove('is-active');
                        const otherOptions = otherMenu.querySelector('.user-menu__options');
                        if(otherOptions) otherOptions.style.display = 'none';
                    }
                });

                // Alternamos el estado del menú actual
                // Usamos tanto la clase 'is-active' como display inline para máxima compatibilidad
                if (optionsList.style.display === 'block') {
                    optionsList.style.display = 'none';
                    menu.classList.remove('is-active');
                } else {
                    optionsList.style.display = 'block';
                    menu.classList.add('is-active');
                }
            });
        }
    });

    // Cerrar todos los menús al hacer clic fuera
    document.addEventListener('click', () => {
        dropdowns.forEach(menu => {
            menu.classList.remove('is-active');
            const options = menu.querySelector('.user-menu__options');
            if (options) options.style.display = 'none';
        });
    });


    // =========================================================
    // 2. LÓGICA DE FILTROS MÓVIL (Mantenemos tu código)
    // =========================================================
    const toggleBtn = document.getElementById('toggle-filters-btn');
    const filtersPanel = document.getElementById('filtroEventos');
    if (toggleBtn && filtersPanel) {
        toggleBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            filtersPanel.classList.toggle('is-open');
        });
        
        // Cerrar filtro al hacer clic fuera (integrado aquí para orden)
        document.addEventListener('click', (e) => {
            if (filtersPanel.classList.contains('is-open') && !filtersPanel.contains(e.target) && !e.target.matches('#toggle-filters-btn')) {
                filtersPanel.classList.remove('is-open');
            }
        });
    }


    // =========================================================
    // 3. LÓGICA DE CAMBIO DE TEMA (Mantenemos tu código)
    // =========================================================
    const themeToggleBtn = document.getElementById('theme-toggle-btn');
    const body = document.body;

    if (themeToggleBtn) {
        const savedTheme = localStorage.getItem('theme');
        const icon = themeToggleBtn.querySelector('.icon-text');

        if (savedTheme === 'dark') {
            body.classList.add('dark-theme');
            icon.textContent = '☀️';
            themeToggleBtn.title = "Cambiar a Modo Claro";
        } else {
            body.classList.remove('dark-theme');
            icon.textContent = '🌙';
            themeToggleBtn.title = "Cambiar a Modo Oscuro";
        }

        themeToggleBtn.addEventListener('click', () => {
            body.classList.toggle('dark-theme');
            const isDarkMode = body.classList.contains('dark-theme');
            
            if (isDarkMode) {
                icon.textContent = '☀️';
                themeToggleBtn.title = "Cambiar a Modo Claro";
                localStorage.setItem('theme', 'dark');
            } else {
                icon.textContent = '🌙';
                themeToggleBtn.title = "Cambiar a Modo Oscuro";
                localStorage.setItem('theme', 'light');
            }
            
            window.dispatchEvent(new Event('themeChanged'));
        });
    }
});