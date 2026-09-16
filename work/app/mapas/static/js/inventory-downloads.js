(function () {
    'use strict';

    // Validar antes de consumir/guardar el cuerpo: un login redirigido también
    // devuelve HTTP 200, pero nunca constituye un reporte Excel/CSV.
    window.FGInventoryDownloads = {
        async readBlob(response) {
            if (response.redirected || response.status === 401) {
                throw new Error('La sesión venció. Inicia sesión nuevamente antes de descargar.');
            }
            if (response.status === 403) {
                throw new Error('No tienes permiso para descargar este reporte.');
            }
            if (!response.ok) throw new Error('No se pudo generar el reporte. Intenta nuevamente.');
            const type = (response.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
            const disposition = response.headers.get('Content-Disposition') || '';
            if (!['text/csv', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'].includes(type)
                || !/^attachment\b/i.test(disposition)) {
                throw new Error('El servidor no devolvió un reporte válido. Comprueba tu sesión e intenta nuevamente.');
            }
            return response.blob();
        },
    };
})();
