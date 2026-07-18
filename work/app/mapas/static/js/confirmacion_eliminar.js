document.addEventListener("DOMContentLoaded", function () {
    const modal = document.getElementById("modal-confirmacion");
    const btnCancelar = document.getElementById("cancelar-eliminar");
    const btnConfirmar = document.getElementById("confirmar-eliminar");

    let formularioEliminar = null;

    document.querySelectorAll(".btn-eliminar").forEach(boton => {
        boton.addEventListener("click", function (e) {
            e.preventDefault();
            formularioEliminar = this.closest("form");
            modal.style.display = "flex";  // Muestra el modal
        });
    });

    btnCancelar.addEventListener("click", () => {
        modal.style.display = "none";
        formularioEliminar = null;
    });

    btnConfirmar.addEventListener("click", () => {
        if (formularioEliminar) {
            formularioEliminar.submit();
        }
    });
});
