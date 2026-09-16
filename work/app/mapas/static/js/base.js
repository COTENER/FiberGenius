document.addEventListener("DOMContentLoaded", () => {
    // Definimos las constantes que vamos a usar
    const closeBtn = document.getElementById('close-btn');
    const openBtn = document.getElementById('open-btn');
    const toggleButton = document.getElementById("toggle-theme");
    const docElement = document.documentElement; // <-- ¡AÑADE ESTA LÍNEA!

    // El botón de abrir (hamburguesa) maneja dos lógicas distintas
    openBtn.addEventListener('click', () => {
        if (window.innerWidth <= 768) {
            // Lógica para el menú móvil
            document.getElementById('app-container').classList.toggle('mobile-menu-open');
        } else {
            // En escritorio, quitamos la clase del <html>
            docElement.classList.remove('sidebar-is-collapsed');
            localStorage.setItem('sidebarState', 'expanded');
        }
    });

    // El botón de cerrar solo funciona en la vista de escritorio
    closeBtn.addEventListener('click', () => {
        // En escritorio, añadimos la clase al <html>
        docElement.classList.add('sidebar-is-collapsed');
        localStorage.setItem('sidebarState', 'collapsed');
    });

    // Limpia las clases al cambiar el tamaño de la ventana para evitar estados conflictivos
    window.addEventListener('resize', () => {
        if (window.innerWidth > 768) {
            document.getElementById('app-container').classList.remove('mobile-menu-open');
        } else {
            // Si pasamos a móvil, quitamos la clase de colapso de escritorio para que no interfiera
            docElement.classList.remove('sidebar-is-collapsed');
        }
    });

    // --- Lógica del Tema ---
    toggleButton.addEventListener("click", () => {
        const isDark = docElement.classList.contains("theme-dark");

        // Usamos un if/else para que la lógica sea más clara
        if (isDark) {
            // Si ESTÁ en modo oscuro, cambiamos a CLARO
            docElement.classList.remove("theme-dark");
            docElement.classList.add("theme-light");
            localStorage.setItem("theme", "theme-light");
            // Y el botón ahora debe mostrar la LUNA para el próximo cambio
            toggleButton.textContent = "🌙";
        } else {
            // Si NO está en modo oscuro (está en claro), cambiamos a OSCURO
            docElement.classList.remove("theme-light");
            docElement.classList.add("theme-dark");
            localStorage.setItem("theme", "theme-dark");
            // Y el botón ahora debe mostrar el SOL para el próximo cambio
            toggleButton.textContent = "☀️";
        }
    });
        const userMenu = document.querySelector('.user-menu');
    if (userMenu) {
        const userMenuButton = userMenu.querySelector('.user-menu-button');
        userMenuButton.addEventListener('click', (event) => {
            event.stopPropagation(); // Evita que el clic se propague al window
            userMenu.classList.toggle('open');
        });
    }

    // Cierra el menú si se hace clic fuera de él
    window.addEventListener('click', () => {
        if (userMenu && userMenu.classList.contains('open')) {
            userMenu.classList.remove('open');
        }
    });
});