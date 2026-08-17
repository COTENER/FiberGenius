"""Señales de auditoría y seguridad de Fiber Genius."""

import logging

from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver
from django.db.models.signals import post_delete, post_save, pre_save

from .models import (
    DetallePuertoODF,
    FibraTramo,
    InventarioFibra,
    InventarioODF,
    InventarioTramo,
    TerminacionFibra,
)


security_logger = logging.getLogger('fibergenius.security')


def _ip(request):
    return request.META.get('REMOTE_ADDR', 'unknown') if request else 'unknown'


@receiver(user_logged_in)
def registrar_inicio_sesion(sender, request, user, **kwargs):
    security_logger.info(
        'login_success user=%s ip=%s',
        user.get_username(),
        _ip(request),
    )


@receiver(user_logged_out)
def registrar_cierre_sesion(sender, request, user, **kwargs):
    security_logger.info(
        'logout user=%s ip=%s',
        user.get_username() if user else 'anonymous',
        _ip(request),
    )


@receiver(user_login_failed)
def registrar_inicio_fallido(sender, credentials, request, **kwargs):
    # La señal limpia campos sensibles. No se registra la contraseña.
    security_logger.warning(
        'login_failed user=%s ip=%s',
        credentials.get('username', 'unknown'),
        _ip(request),
    )


def _recontar_odfs(ids):
    for odf in InventarioODF.objects.filter(pk__in={item for item in ids if item}):
        odf.actualizar_contadores()


@receiver(post_save, sender=DetallePuertoODF)
def recontar_odf_al_guardar_puerto(sender, instance, raw=False, **kwargs):
    if not raw:
        _recontar_odfs((instance.odf_obj_id,))


@receiver(post_delete, sender=DetallePuertoODF)
def recontar_odf_al_eliminar_puerto(sender, instance, **kwargs):
    _recontar_odfs((instance.odf_obj_id,))


def _sincronizar_fibras(ids, causa, lote_importacion=None):
    from .services.fibras import sincronizar_estado_fibra

    ids = {item for item in ids if item}
    for fibra in InventarioFibra.objects.filter(pk__in=ids).exclude(
        origen_estado='INFORMADO'
    ):
        sincronizar_estado_fibra(
            fibra,
            causa=causa,
            origen='EXCEL' if lote_importacion else 'SISTEMA',
            lote_importacion=lote_importacion,
        )


def _recalcular_tramos(ids):
    from .services.fibras import recalcular_cache_tramo

    for tramo in InventarioTramo.objects.filter(
        pk__in={item for item in ids if item}
    ):
        recalcular_cache_tramo(tramo)


@receiver(pre_save, sender=FibraTramo)
def recordar_asignacion_fibra_anterior(sender, instance, **kwargs):
    instance._fibra_anterior_id = None
    instance._tramo_anterior_id = None
    if instance.pk:
        anterior = FibraTramo.objects.filter(pk=instance.pk).values(
            'fibra_id', 'tramo_id'
        ).first()
        if anterior:
            instance._fibra_anterior_id = anterior['fibra_id']
            instance._tramo_anterior_id = anterior['tramo_id']


@receiver(post_save, sender=FibraTramo)
def sincronizar_asignacion_fibra(sender, instance, raw=False, **kwargs):
    if raw:
        return
    _recalcular_tramos((
        getattr(instance, '_tramo_anterior_id', None),
        instance.tramo_id,
    ))
    if getattr(instance, '_omitir_sincronizacion_estado', False):
        return
    _sincronizar_fibras(
        (getattr(instance, '_fibra_anterior_id', None), instance.fibra_id),
        'CAMBIO_FIBRA_TRAMO',
        instance.lote_importacion,
    )


@receiver(post_delete, sender=FibraTramo)
def sincronizar_eliminacion_fibra_tramo(sender, instance, **kwargs):
    _recalcular_tramos((instance.tramo_id,))
    _sincronizar_fibras(
        (instance.fibra_id,),
        'ELIMINAR_FIBRA_TRAMO',
        instance.lote_importacion,
    )


@receiver(post_save, sender=InventarioTramo)
def sincronizar_cambio_topologia(sender, instance, raw=False, **kwargs):
    if raw or getattr(instance, '_omitir_sincronizacion_estado', False):
        return
    ids = InventarioFibra.objects.filter(ruta_id=instance.ruta_id).values_list(
        'pk', flat=True
    )
    _sincronizar_fibras(
        ids,
        'CAMBIO_TOPOLOGIA_RUTA',
        instance.lote_importacion,
    )


@receiver(post_delete, sender=InventarioTramo)
def sincronizar_eliminacion_tramo(sender, instance, **kwargs):
    ids = InventarioFibra.objects.filter(ruta_id=instance.ruta_id).values_list(
        'pk', flat=True
    )
    _sincronizar_fibras(
        ids,
        'ELIMINAR_TRAMO',
        instance.lote_importacion,
    )


@receiver(pre_save, sender=TerminacionFibra)
def recordar_puerto_anterior(sender, instance, **kwargs):
    instance._puerto_anterior_id = None
    instance._odf_anterior_id = None
    if instance.pk:
        anterior = (
            TerminacionFibra.objects.filter(pk=instance.pk)
            .values('puerto_odf_id', 'puerto_odf__odf_obj_id')
            .first()
        )
        if anterior:
            instance._puerto_anterior_id = anterior['puerto_odf_id']
            instance._odf_anterior_id = anterior['puerto_odf__odf_obj_id']


@receiver(post_save, sender=TerminacionFibra)
def ocupar_puerto_terminado(sender, instance, raw=False, **kwargs):
    if raw:
        return
    puerto = DetallePuertoODF.objects.select_related('odf_obj').get(pk=instance.puerto_odf_id)
    anterior_id = getattr(instance, '_puerto_anterior_id', None)
    if anterior_id and anterior_id != puerto.pk:
        DetallePuertoODF.objects.filter(pk=anterior_id).update(estado_puerto='LIBRE')
    DetallePuertoODF.objects.filter(pk=puerto.pk).update(estado_puerto='OCUPADO')
    _recontar_odfs((getattr(instance, '_odf_anterior_id', None), puerto.odf_obj_id))


@receiver(post_delete, sender=TerminacionFibra)
def liberar_puerto_desconectado(sender, instance, **kwargs):
    puerto = DetallePuertoODF.objects.select_related('odf_obj').filter(
        pk=instance.puerto_odf_id
    ).first()
    if not puerto:
        return
    DetallePuertoODF.objects.filter(pk=puerto.pk).update(estado_puerto='LIBRE')
    _recontar_odfs((puerto.odf_obj_id,))
