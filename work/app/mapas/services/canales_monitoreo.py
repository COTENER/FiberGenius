"""Base pasiva para un upgrade de monitoreo, sin red, tareas ni escrituras de inventario.

No usar nombres de troncal ni F1/F17 como identidad de una medición. El adaptador
futuro deberá resolver fuente + identificador externo del puerto de monitoreo.
Los nombres internos se conservan; estos puertos son del OTU/VeEX, no del ODF.
"""
from django.db import transaction
from django.utils import timezone

from ..models import CanalMonitoreo, InventarioFibra


@transaction.atomic
def registrar_canal(*, fuente, identificador_externo, fibra, extremo_origen=''):
    """Registra una correspondencia explícita, sin crear ni conectar fibras."""
    # Serializa esta relación con las operaciones oficiales sobre la fibra.
    fibra = InventarioFibra.objects.select_for_update().get(pk=fibra.pk)
    return CanalMonitoreo.objects.create(
        fuente=fuente,
        identificador_externo=identificador_externo,
        fibra=fibra,
        extremo_origen=extremo_origen,
    )


@transaction.atomic
def retirar_canal(*, canal):
    """Conserva la asociación original y libera el identificador para otra nueva."""
    bloqueado = CanalMonitoreo.objects.select_for_update().get(pk=canal.pk)
    if bloqueado.retirado_en is None:
        bloqueado.retirado_en = timezone.now()
        bloqueado.save(update_fields=['retirado_en'])
    return bloqueado


def canales_de_troncal(ruta):
    """Relaciones vigentes, no un diagnóstico de disponibilidad o salud óptica."""
    return (
        CanalMonitoreo.objects.filter(fibra__ruta_id=ruta.pk, retirado_en__isnull=True)
        .select_related('fuente', 'fibra__ruta')
        .order_by('fibra_id', 'pk')
    )
