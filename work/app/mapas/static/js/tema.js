document.addEventListener('DOMContentLoaded', () => {
    const themeToggleBtn = document.getElementById('theme-toggle-btn');
    const body = document.body;

    if (themeToggleBtn) {
        // Función para actualizar el estado del botón
        const updateButton = () => {
            const isDarkMode = body.classList.contains('dark-theme');
            const icon = themeToggleBtn.querySelector('.icon-text');
            icon.textContent = isDarkMode ? '☀️' : '🌙';
            themeToggleBtn.title = isDarkMode ? 'Cambiar a Modo Claro' : 'Cambiar a Modo Oscuro';
        };

        // Aplica el tema guardado al cargar
        const savedTheme = localStorage.getItem('theme');
        if (savedTheme === 'dark') {
            body.classList.add('dark-theme');
        }
        updateButton();

        // Maneja el clic
        themeToggleBtn.addEventListener('click', () => {
            body.classList.toggle('dark-theme');
            localStorage.setItem('theme', body.classList.contains('dark-theme') ? 'dark' : 'light');
            updateButton();
        });
    }
});