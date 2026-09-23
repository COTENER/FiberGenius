"""Persistencia masiva de posiciones físicas, sin alterar identidad ni extremos."""
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count

from ..models import AuditoriaFibra, FibraTramo, InventarioFibra, InventarioTramo
from .fibras import _referencia_fibra, sincronizar_estado_fibra, snapshot_asignacion


def _lotes(items, size=250):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


@transaction.atomic
def guardar_asignaciones_tramo_masivo(filas, *, lote=None, usuario=None, progreso=None):
    """Valida el estado final bajo bloqueo y conserva el rollback del archivo.

    FK y unicidad se verifican en conjunto, no con consultas por cada fila.
    clean_fields/clean mantienen longitudes, choices, capacidad y correlación.
    Las restricciones SQL siguen activas. Los efectos de post_save se ejecutan
    explícitamente después del guardado: auditoría, estado global y contadores.
    No se asignan rutas, crean fibras ni cambian terminaciones en este servicio.
    """
    filas = list(filas)
    if not filas:
        return 0, 0
    ids_fibra = sorted({item['fibra_consultada'].pk for item in filas})
    ids_tramo = sorted({item['tramo'].pk for item in filas})
    fibras = {}
    for ids in _lotes(ids_fibra):
        fibras.update({f.pk: f for f in InventarioFibra.objects.select_for_update(of=('self',))
                       .select_related('ruta').filter(pk__in=ids).order_by('pk')})
    tramos = {}
    for ids in _lotes(ids_tramo):
        tramos.update({t.pk: t for t in InventarioTramo.objects.select_for_update()
                      .filter(pk__in=ids).order_by('pk')})
    existentes = []
    for ids in _lotes(ids_tramo):
        existentes.extend(FibraTramo.objects.select_for_update().filter(tramo_id__in=ids).order_by('pk'))
    por_posicion = {(a.tramo_id, a.numero_hilo.casefold()): a for a in existentes}
    por_fibra = {(a.tramo_id, a.fibra_id): a for a in existentes if a.fibra_id}
    anteriores = {a.pk: snapshot_asignacion(a) for a in existentes}
    posiciones_archivo = set()
    fibras_archivo = set()
    nuevas, modificadas, desvinculadas, tocadas = [], [], [], []
    creadas = actualizadas = 0

    def avance(porcentaje, etapa, procesadas=0, total=None):
        if progreso:
            progreso(porcentaje, etapa, procesadas=procesadas,
                     total=len(filas) if total is None else total)

    # Conservar el rechazo conservador de posiciones ocupadas: este importador
    # no permite desplazar otra fibra, aunque aparezca más adelante en el CSV.
    for item in filas:
        try:
            fibra = fibras.get(item['fibra_consultada'].pk)
            tramo = tramos.get(item['tramo'].pk)
            if fibra is None or tramo is None:
                raise ValidationError('La fibra o el tramo seleccionado ya no existe.')
            numero = str(item['numero_hilo'] or '').strip().upper()
            clave = (tramo.pk, numero.casefold())
            logica = (tramo.pk, fibra.pk)
            if clave in posiciones_archivo or logica in fibras_archivo:
                raise ValidationError('Posición o fibra repetida en el mismo tramo.')
            posiciones_archivo.add(clave)
            fibras_archivo.add(logica)
            posicion = por_posicion.get(clave)
            if posicion and posicion.fibra_id not in (None, fibra.pk):
                raise ValidationError(f'{numero} ya pertenece a otra fibra lógica.')
            candidata = FibraTramo(tramo=tramo, fibra=fibra, numero_hilo=numero,
                                   estado=item.get('estado') or 'SIN_INFORMACION',
                                   observaciones=item.get('observaciones') or '')
            candidata.full_clean(exclude=['tramo', 'fibra', 'lote_importacion'],
                                 validate_unique=False, validate_constraints=False)
        except (ValueError, ValidationError) as exc:
            raise ValidationError(f"fila {item['fila']}: {exc}") from exc

    for index, item in enumerate(filas, 1):
        tramo = tramos[item['tramo'].pk]
        fibra = fibras[item['fibra_consultada'].pk]
        numero = str(item['numero_hilo']).strip().upper()
        clave = (tramo.pk, numero.casefold())
        logica = (tramo.pk, fibra.pk)
        asignacion = por_posicion.get(clave)
        asignacion_logica = por_fibra.get(logica)
        if asignacion is None:
            if asignacion_logica is not None:
                por_posicion.pop((tramo.pk, asignacion_logica.numero_hilo.casefold()), None)
                asignacion = asignacion_logica
                asignacion.numero_hilo = numero
            else:
                asignacion = FibraTramo(tramo=tramo, numero_hilo=numero)
        elif asignacion_logica is not None and asignacion_logica.pk != asignacion.pk:
            asignacion_logica.fibra = None
            asignacion_logica.estado = 'SIN_INFORMACION'
            desvinculadas.append(asignacion_logica)
            tocadas.append(asignacion_logica)
        nueva = asignacion.pk is None
        asignacion.tramo = tramo
        asignacion.fibra = fibra
        if item.get('estado') is not None:
            asignacion.estado = item['estado']
        if item.get('observaciones') is not None:
            asignacion.observaciones = item['observaciones']
        asignacion.observaciones = (asignacion.observaciones or '').strip()
        asignacion.lote_importacion = lote
        try:
            # Validar también valores conservados por el parche, no solo el CSV.
            asignacion.full_clean(exclude=['tramo', 'fibra', 'lote_importacion'],
                                  validate_unique=False, validate_constraints=False)
        except ValidationError as exc:
            raise ValidationError(f"fila {item['fila']}: {exc}") from exc
        (nuevas if nueva else modificadas).append(asignacion)
        tocadas.append(asignacion)
        creadas += int(nueva)
        actualizadas += int(not nueva and anteriores[asignacion.pk] != snapshot_asignacion(asignacion))
        por_posicion[clave] = asignacion
        por_fibra[logica] = asignacion
        if index % 250 == 0 or index == len(filas):
            avance(50 + int(15 * index / len(filas)), 'Validando posiciones físicas', index)

    # Se libera primero la asociación antigua cuando el destino es un registro
    # físico sin fibra. No se elimina esa posición ni se inventa disponibilidad.
    escritos = 0
    total_escrituras = len(desvinculadas) + len(modificadas) + len(nuevas)
    try:
        with transaction.atomic():
            for objetos, campos in (
                (desvinculadas, ['fibra', 'estado']),
                (modificadas, ['numero_hilo', 'fibra', 'estado', 'observaciones', 'lote_importacion']),
                (nuevas, None),
            ):
                for grupo in _lotes(objetos):
                    if campos:
                        # CASE usa dos parámetros por campo/fila y otro para
                        # el WHERE: 6 campos × 2 × 50 + 50 = 650 (< 999).
                        FibraTramo.objects.bulk_update(grupo, campos, batch_size=50)
                    else:
                        FibraTramo.objects.bulk_create(grupo, batch_size=100)
                    escritos += len(grupo)
                    avance(65 + int(15 * escritos / max(total_escrituras, 1)),
                           'Guardando posiciones; pendiente de confirmación', escritos, total_escrituras)
    except IntegrityError as exc:
        raise ValidationError(
            f"filas {min(f['fila'] for f in filas)}–{max(f['fila'] for f in filas)}: "
            'conflicto de integridad al guardar posiciones. No se aplicó el archivo; '
            'revise las posiciones y vuelva a validar si hubo cambios simultáneos.'
        ) from exc

    if usuario is not None and not getattr(usuario, 'is_authenticated', False):
        usuario = None
    auditorias = []
    for asignacion in tocadas:
        anterior = anteriores.get(asignacion.pk)
        nuevo = snapshot_asignacion(asignacion)
        if anterior == nuevo:
            continue
        fibra_id = asignacion.fibra_id or (anterior or {}).get('fibra_id')
        if fibra_id:
            auditorias.append(AuditoriaFibra(
                fibra=fibras[fibra_id], accion='ASIGNAR_TRAMO', origen='EXCEL',
                usuario=usuario, lote_importacion=lote,
                referencia_fibra=_referencia_fibra(fibras[fibra_id])[:300],
                valor_anterior=str(anterior or '')[:300], valor_nuevo=str(nuevo or '')[:300],
                metadatos={'asignacion_id': asignacion.pk, 'tramo_id': asignacion.tramo_id,
                           'anterior': anterior, 'nuevo': nuevo},
            ))
    for grupo in _lotes(auditorias):
        AuditoriaFibra.objects.bulk_create(grupo, batch_size=50)
    avance(85, 'Auditoría de recorrido guardada', len(filas))

    # Mantener el servicio canónico: nunca reemplazar un estado INFORMADO.
    for index, fibra in enumerate(fibras.values(), 1):
        if fibra.origen_estado != 'INFORMADO':
            sincronizar_estado_fibra(fibra, origen='EXCEL', lote_importacion=lote,
                                     causa='IMPORTACION_FIBRA_TRAMO')
        if index % 100 == 0 or index == len(fibras):
            avance(85 + int(10 * index / len(fibras)), 'Verificando estados globales', index, len(fibras))
    for ids in _lotes(ids_tramo):
        conteos = defaultdict(dict)
        for row in FibraTramo.objects.filter(tramo_id__in=ids).order_by().values('tramo_id', 'estado').annotate(total=Count('pk')):
            conteos[row['tramo_id']][row['estado']] = row['total']
        for pk in ids:
            tramos[pk].hilos_ocupados = conteos[pk].get('OCUPADO', 0)
            tramos[pk].hilos_reservados = conteos[pk].get('RESERVADO', 0)
            tramos[pk].hilos_libres = conteos[pk].get('DISPONIBLE', 0)
        InventarioTramo.objects.bulk_update([tramos[pk] for pk in ids],
            ['hilos_ocupados', 'hilos_reservados', 'hilos_libres'], batch_size=100)
    avance(97, 'Contadores de tramos verificados', len(filas))
    return creadas, actualizadas
