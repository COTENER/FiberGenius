"""Operaciones transaccionales para la ocupación de puertos ODF."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import (
    AuditoriaPuertoODF,
    DetallePuertoODF,
    InventarioFibra,
    TerminacionFibra,
)
from .fibras import establecer_estado_fibra_informado


class MovimientoRequiereConfirmacion(ValidationError):
    def __init__(self, terminacion):
        puerto = terminacion.puerto_odf
        self.terminacion = terminacion
        super().__init__(
            f"El extremo {terminacion.extremo} de "
            f"{terminacion.fibra.nombre_troncal} / "
            f"{terminacion.fibra.fibra_numero} "
            f"ya está conectado en {puerto.odf_obj.odf} / Puerto {puerto.puerto_odf}."
        )


def _bloquear_fibra(fibra_id):
    try:
        return (
            InventarioFibra.objects.select_for_update()
            .select_related("ruta")
            .get(pk=fibra_id)
        )
    except InventarioFibra.DoesNotExist as exc:
        raise ValidationError("La fibra seleccionada no existe.") from exc


def _bloquear_terminacion(fibra, extremo):
    return (
        TerminacionFibra.objects.select_for_update()
        .select_related("fibra__ruta", "puerto_odf__odf_obj")
        .filter(fibra=fibra, extremo=extremo)
        .order_by("pk")
        .first()
    )


def _bloquear_puertos(puerto_ids):
    ids = sorted({int(puerto_id) for puerto_id in puerto_ids})
    puertos = {
        puerto.pk: puerto
        for puerto in (
            DetallePuertoODF.objects.select_for_update()
            .select_related("odf_obj")
            .filter(pk__in=ids)
            .order_by("pk")
        )
    }
    faltantes = [puerto_id for puerto_id in ids if puerto_id not in puertos]
    if faltantes:
        raise ValidationError("El puerto ODF no existe.")
    return puertos


def _bloquear_puerto(puerto_id):
    return _bloquear_puertos((puerto_id,))[int(puerto_id)]


def _actualizar_contadores(*odfs):
    procesados = set()
    for odf in odfs:
        if odf and odf.pk not in procesados:
            odf.actualizar_contadores()
            procesados.add(odf.pk)


def _establecer_estado(puerto, estado):
    DetallePuertoODF.objects.filter(pk=puerto.pk).update(estado_puerto=estado)
    puerto.estado_puerto = estado


def _referencia_puerto(puerto):
    if not puerto:
        return ""
    odf = puerto.odf_obj
    return f"{odf.hub_site} / {odf.odf} / Puerto {puerto.puerto_odf}"


def _registrar_auditoria(
    *, accion, usuario=None, origen="SISTEMA", lote_importacion=None,
    fibra=None, extremo="", puerto_anterior=None, puerto_nuevo=None,
    estado_anterior="", estado_nuevo="", sincronizo_fibra=False,
    metadatos=None,
):
    origen = str(origen or "SISTEMA").strip().upper()
    if origen not in {clave for clave, _ in AuditoriaPuertoODF.ORIGENES}:
        origen = "SISTEMA"
    if usuario is not None and not getattr(usuario, "is_authenticated", False):
        usuario = None
    AuditoriaPuertoODF.objects.create(
        accion=accion,
        origen=origen,
        usuario=usuario,
        fibra=fibra,
        extremo=extremo,
        puerto_anterior=puerto_anterior,
        puerto_nuevo=puerto_nuevo,
        estado_anterior=estado_anterior or "",
        estado_nuevo=estado_nuevo or "",
        referencia_puerto_anterior=_referencia_puerto(puerto_anterior),
        referencia_puerto_nuevo=_referencia_puerto(puerto_nuevo),
        referencia_fibra=(
            f"{fibra.nombre_troncal} / {fibra.fibra_numero}" if fibra else ""
        ),
        sincronizo_fibra=bool(sincronizo_fibra),
        lote_importacion=lote_importacion,
        metadatos=metadatos or {},
    )


@transaction.atomic
def conectar_puerto(
    *,
    puerto_id,
    fibra_id=None,
    fibra_numero="",
    extremo,
    permitir_mover=False,
    ocupar_fibra=False,
    usuario=None,
    origen="SISTEMA",
    lote_importacion=None,
):
    extremo = str(extremo or "").strip().upper()
    if extremo not in {"A", "B"}:
        raise ValidationError("Seleccione el extremo A o B.")

    if not fibra_id:
        numero_provisional = str(fibra_numero or "").strip().upper()
        if not numero_provisional:
            raise ValidationError(
                "Indique el número del hilo que quedará con troncal pendiente."
            )
        if len(numero_provisional) > 50:
            raise ValidationError("El número del hilo no puede superar 50 caracteres.")
        fibra = InventarioFibra.objects.create(
            ruta=None,
            fibra_numero=numero_provisional,
            estado="Desconocido",
            origen_estado="NO_INFORMADO",
        )
        fibra_id = fibra.pk

    # Orden oficial de toda operación de conectividad:
    # InventarioFibra -> TerminacionFibra -> puertos por PK ascendente.
    fibra = _bloquear_fibra(fibra_id)
    existente = _bloquear_terminacion(fibra, extremo)
    ids_puerto = {int(puerto_id)}
    if existente:
        ids_puerto.add(existente.puerto_odf_id)
    puertos_bloqueados = _bloquear_puertos(ids_puerto)
    puerto = puertos_bloqueados[int(puerto_id)]

    # El puerto ya está bloqueado y funciona como mutex físico. No se toma
    # aquí otra terminación fuera de orden; la unicidad de puerto protege la
    # escritura y la lectura confirma el ocupante comprometido más reciente.
    ocupante = (
        TerminacionFibra.objects
        .select_related("fibra__ruta", "puerto_odf__odf_obj")
        .filter(puerto_odf=puerto)
        .first()
    )
    if ocupante and (not existente or ocupante.pk != existente.pk):
        raise ValidationError(
            "El puerto ya está conectado a otra fibra. Desconéctelo antes de reutilizarlo."
        )
    estado_destino_anterior = puerto.estado_puerto
    if existente and existente.puerto_odf_id == puerto.pk:
        cambio_puerto = puerto.estado_puerto != "Ocupado"
        if puerto.estado_puerto != "Ocupado":
            _establecer_estado(puerto, "Ocupado")
            _actualizar_contadores(puerto.odf_obj)
        if ocupar_fibra:
            establecer_estado_fibra_informado(
                fibra=fibra,
                estado="Ocupado",
                usuario=usuario,
                origen=origen,
                lote_importacion=lote_importacion,
            )
        if cambio_puerto or ocupar_fibra:
            _registrar_auditoria(
                accion="CONECTAR",
                usuario=usuario,
                origen=origen,
                lote_importacion=lote_importacion,
                fibra=fibra,
                extremo=extremo,
                puerto_anterior=puerto,
                puerto_nuevo=puerto,
                estado_anterior=estado_destino_anterior,
                estado_nuevo="Ocupado",
                sincronizo_fibra=ocupar_fibra,
                metadatos={"conexion_existente": True},
            )
        return existente, False
    if existente and not permitir_mover:
        raise MovimientoRequiereConfirmacion(existente)
    if puerto.estado_puerto not in {"Libre", "Reservado", "Ocupado"}:
        raise ValidationError("El puerto no tiene un estado válido para conectarlo.")

    odf_anterior = None
    puerto_anterior = None
    estado_puerto_origen = ""
    if existente:
        puerto_anterior = puertos_bloqueados[existente.puerto_odf_id]
        estado_puerto_origen = puerto_anterior.estado_puerto
        odf_anterior = puerto_anterior.odf_obj
        existente.puerto_odf = puerto
        existente.save(update_fields=["puerto_odf"])
        if puerto_anterior.pk != puerto.pk:
            _establecer_estado(puerto_anterior, "Libre")
        terminacion = existente
        creada = False
    else:
        terminacion = TerminacionFibra.objects.create(
            fibra=fibra,
            extremo=extremo,
            puerto_odf=puerto,
            tipo_conector=puerto.tipo_conector or fibra.tipo_conector or "",
        )
        creada = True

    _establecer_estado(puerto, "Ocupado")
    if ocupar_fibra:
        establecer_estado_fibra_informado(
            fibra=fibra,
            estado="Ocupado",
            usuario=usuario,
            origen=origen,
            lote_importacion=lote_importacion,
        )
    _actualizar_contadores(odf_anterior, puerto.odf_obj)
    _registrar_auditoria(
        accion="MOVER" if existente else "CONECTAR",
        usuario=usuario,
        origen=origen,
        lote_importacion=lote_importacion,
        fibra=fibra,
        extremo=extremo,
        puerto_anterior=puerto_anterior,
        puerto_nuevo=puerto,
        estado_anterior=estado_destino_anterior,
        estado_nuevo="Ocupado",
        sincronizo_fibra=ocupar_fibra,
        metadatos=(
            {
                "estado_puerto_origen_anterior": estado_puerto_origen,
                "estado_puerto_origen_nuevo": "Libre",
            }
            if existente else {}
        ),
    )
    return terminacion, creada


@transaction.atomic
def desconectar_puerto(
    *, puerto_id, liberar_fibra=False, usuario=None, origen="SISTEMA",
    lote_importacion=None,
):
    referencia = (
        TerminacionFibra.objects.filter(puerto_odf_id=puerto_id)
        .values("fibra_id", "extremo")
        .first()
    )
    if referencia is None:
        raise ValidationError(
            "El puerto no tiene una terminación oficial. Debe regularizarse antes de desconectarlo."
        )
    fibra = _bloquear_fibra(referencia["fibra_id"])
    terminacion = _bloquear_terminacion(fibra, referencia["extremo"])
    if terminacion is None or terminacion.puerto_odf_id != int(puerto_id):
        raise ValidationError(
            "La terminación cambió durante la operación. Actualice la vista e intente nuevamente."
        )
    puerto = _bloquear_puertos((puerto_id,))[int(puerto_id)]
    fibra = terminacion.fibra
    extremo = terminacion.extremo
    estado_anterior = puerto.estado_puerto
    terminacion.delete()
    _establecer_estado(puerto, "Libre")
    if liberar_fibra:
        establecer_estado_fibra_informado(
            fibra=fibra,
            estado="Libre",
            usuario=usuario,
            origen=origen,
            lote_importacion=lote_importacion,
        )
    _actualizar_contadores(puerto.odf_obj)
    _registrar_auditoria(
        accion="DESCONECTAR",
        usuario=usuario,
        origen=origen,
        lote_importacion=lote_importacion,
        fibra=fibra,
        extremo=extremo,
        puerto_anterior=puerto,
        estado_anterior=estado_anterior,
        estado_nuevo="Libre",
        sincronizo_fibra=liberar_fibra,
    )
    return fibra


@transaction.atomic
def reservar_puerto(
    *, puerto_id, usuario=None, origen="SISTEMA", lote_importacion=None
):
    puerto = _bloquear_puerto(puerto_id)
    if TerminacionFibra.objects.filter(puerto_odf=puerto).exists():
        raise ValidationError("No se puede reservar un puerto que tiene una fibra conectada.")
    if puerto.estado_puerto == "Ocupado":
        raise ValidationError("Un puerto ocupado debe regularizarse antes de reservarlo.")
    estado_anterior = puerto.estado_puerto
    _establecer_estado(puerto, "Reservado")
    _actualizar_contadores(puerto.odf_obj)
    _registrar_auditoria(
        accion="RESERVAR",
        usuario=usuario,
        origen=origen,
        lote_importacion=lote_importacion,
        puerto_anterior=puerto,
        puerto_nuevo=puerto,
        estado_anterior=estado_anterior,
        estado_nuevo="Reservado",
    )
    return puerto


@transaction.atomic
def cancelar_reserva_puerto(
    *, puerto_id, usuario=None, origen="SISTEMA", lote_importacion=None
):
    puerto = _bloquear_puerto(puerto_id)
    if TerminacionFibra.objects.filter(puerto_odf=puerto).exists():
        raise ValidationError("El puerto tiene una fibra conectada y no puede liberarse como reserva.")
    if puerto.estado_puerto != "Reservado":
        raise ValidationError("El puerto no está reservado.")
    estado_anterior = puerto.estado_puerto
    _establecer_estado(puerto, "Libre")
    _actualizar_contadores(puerto.odf_obj)
    _registrar_auditoria(
        accion="CANCELAR_RESERVA",
        usuario=usuario,
        origen=origen,
        lote_importacion=lote_importacion,
        puerto_anterior=puerto,
        puerto_nuevo=puerto,
        estado_anterior=estado_anterior,
        estado_nuevo="Libre",
    )
    return puerto
