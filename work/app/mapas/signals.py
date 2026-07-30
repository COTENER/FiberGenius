"""Mantiene sincronizados los estados derivados de relaciones físicas."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import DetallePuertoODF, TerminacionFibra


def _actualizar_contadores_puerto(puerto_id):
    puerto = DetallePuertoODF.objects.filter(pk=puerto_id).select_related('odf_obj').first()
    if puerto:
        puerto.odf_obj.actualizar_contadores()


@receiver(post_save, sender=TerminacionFibra)
def ocupar_puerto_terminado(sender, instance, **kwargs):
    DetallePuertoODF.objects.filter(pk=instance.puerto_odf_id).update(
        estado_puerto='Ocupado',
        fibra=instance.fibra.fibra_numero,
    )
    _actualizar_contadores_puerto(instance.puerto_odf_id)


@receiver(post_delete, sender=TerminacionFibra)
def liberar_puerto_sin_terminacion(sender, instance, **kwargs):
    if not TerminacionFibra.objects.filter(puerto_odf_id=instance.puerto_odf_id).exists():
        DetallePuertoODF.objects.filter(pk=instance.puerto_odf_id).update(
            estado_puerto='Libre',
            fibra='',
        )
    _actualizar_contadores_puerto(instance.puerto_odf_id)
