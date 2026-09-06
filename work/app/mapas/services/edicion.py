"""Control de cambios parciales; no mantiene bloqueos mientras el usuario edita."""
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError


def valores_edicion_troncal(ruta, tramos):
    """Valores editables exactos; no usar contadores o distancias de resumen."""
    valores = {'nombre': ruta.nombre, 'hub_origen': ruta.olt or '',
               'distancia_km': (ruta.distancia_m or 0) / 1000}
    if len(tramos) == 1:
        tramo = tramos[0]
        valores.update({
            'estado': tramo.estado or '', 'capacidad': tramo.capacidad or '',
            'hub_origen': tramo.origen or tramo.hub_site or ruta.olt or '',
            'destino': tramo.destino or '', 'tipo_trazado': tramo.tipo_trazado or '',
            'tipo_fibra': tramo.tipo_fibra or '', 'odf_nombre': tramo.odf_nombre or '',
            'reserva_km': (tramo.reservas_m or 0) / 1000,
        })
    return valores


def equivalente(a, b):
    a, b = str(a if a is not None else '').strip(), str(b if b is not None else '').strip()
    if a == b:
        return True
    try:
        return Decimal(a) == Decimal(b)
    except InvalidOperation:
        return False


def verificar_edicion(data, actuales):
    """Comparar bajo bloqueo original/actual solo para los campos solicitados.

Los clientes antiguos sin snapshot mantienen compatibilidad; los editores web
envían _original. No sustituye permisos ni validaciones de negocio.
"""
    original = data.get('_original')
    if original is None:
        return
    if not isinstance(original, dict):
        raise ValidationError('La referencia de edición no es válida; recargue el formulario.')
    for campo, actual in actuales.items():
        if campo not in data or equivalente(data[campo], actual):
            continue
        if campo not in original or not equivalente(original[campo], actual):
            raise ValidationError(
                f'El dato {campo} cambió desde que abrió el formulario. '
                'Recargue y revise el cambio; no se guardó la edición.'
            )
