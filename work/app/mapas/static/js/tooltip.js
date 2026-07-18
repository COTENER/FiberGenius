document.addEventListener('DOMContentLoaded', function () {
    const tooltips = document.querySelectorAll('.tooltip a');

    tooltips.forEach(function (anchor) {
        anchor.addEventListener('click', function (e) {
            const tooltipText = anchor.querySelector('.tooltip-text');

            if ('ontouchstart' in window || navigator.maxTouchPoints) {
                e.preventDefault(); // evita que se siga el enlace

                // Oculta otros tooltips activos
                document.querySelectorAll('.tooltip-text.active').forEach(t => {
                    if (t !== tooltipText) t.classList.remove('active');
                });

                // Muestra este
                tooltipText.classList.toggle('active');
            }
        });
    });

    // Cierra tooltip si se toca fuera
    document.addEventListener('click', function (e) {
        if (!e.target.closest('.tooltip')) {
            document.querySelectorAll('.tooltip-text.active').forEach(t => t.classList.remove('active'));
        }
    });
});
