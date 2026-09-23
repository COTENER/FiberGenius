"""Persistencia masiva del inventario global, sin materializar recorridos.

Comparte los normalizadores y las reglas del modelo con los servicios unitarios.
Las referencias y unicidades se verifican en conjunto bajo bloqueo; los campos
y clean() se validan por objeto. Las restricciones SQL siguen siendo definitivas.
Ningún lote de escritura realiza un commit independiente.
"""
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from ..models import AuditoriaFibra, InventarioFibra, Ruta
from .fibras import (
    _origen_auditoria, _referencia_fibra, normalizar_condicion_fisica,
    normalizar_estado_fibra, normalizar_numero_hilo,
)


@transaction.atomic
def guardar_fibras_globales_masivo(filas, *, usuario=None, lote=None, progreso=None):
    """Aplica parches explícitos; las celdas None conservan el valor existente."""
    filas = list(filas)
    existentes = list(
        InventarioFibra.objects.select_for_update(of=('self',))
        .select_related('ruta').order_by('pk')
    )
    por_codigo = {f.codigo_fibra.casefold(): f for f in existentes}
    por_posicion = defaultdict(list)
    for fibra in existentes:
        if fibra.ruta_id:
            por_posicion[(fibra.ruta_id, fibra.fibra_numero.casefold())].append(fibra)
    # Mismo orden de bloqueo que los servicios unitarios: fibra, después ruta.
    ids_ruta = sorted({item['ruta'].pk for item in filas if item.get('ruta')})
    rutas = {}
    for inicio in range(0, len(ids_ruta), 500):
        rutas.update({r.pk: r for r in Ruta.objects.select_for_update().filter(
            pk__in=ids_ruta[inicio:inicio + 500]).order_by('pk')})
    if usuario is not None and not getattr(usuario, 'is_authenticated', False):
        usuario = None
    preparadas = []
    identidades = set()
    posiciones = set()
    creadas = actualizadas = 0
    campos_parche = {
        'fibra_numero': 'fibra_numero', 'condicion_fisica': 'condicion_fisica',
        'servicio': 'nombre_fibra', 'conector': 'tipo_conector',
        'observaciones': 'observaciones',
    }

    for indice, item in enumerate(filas, 1):
        try:
            numero, _ = normalizar_numero_hilo(item['fibra_numero'])
            codigo = str(item.get('codigo') or '').strip().upper()
            ruta_solicitada = item.get('ruta')
            ruta = rutas.get(ruta_solicitada.pk) if ruta_solicitada else None
            if ruta_solicitada and ruta is None:
                raise ValidationError('La troncal seleccionada ya no existe.')
            if codigo:
                fibra = por_codigo.get(codigo.casefold())
            else:
                if ruta is None:
                    raise ValidationError('Sin Código Fibra debe informar una Ruta.')
                candidatas = por_posicion.get((ruta.pk, numero.casefold()), [])
                if len(candidatas) > 1:
                    raise ValidationError('Ruta + Fibra es ambiguo; informe Código Fibra.')
                fibra = candidatas[0] if candidatas else None
            nueva = fibra is None
            if nueva:
                fibra = InventarioFibra(
                    **({'codigo_fibra': codigo} if codigo else {}),
                    fibra_numero=numero, nombre_fibra='', tipo_conector='',
                )
            identidad = ('pk', fibra.pk) if not nueva else ('codigo', fibra.codigo_fibra.casefold())
            if identidad in identidades:
                raise ValidationError('La misma fibra está repetida en el archivo.')
            identidades.add(identidad)
            if fibra.ruta_id and ruta and fibra.ruta_id != ruta.pk:
                raise ValidationError('La fibra ya pertenece a otra troncal.')
            ruta_final = ruta or fibra.ruta
            if ruta_final:
                posicion = (ruta_final.pk, numero.casefold())
                if posicion in posiciones:
                    raise ValidationError(f'{numero} está repetida en la troncal {ruta_final.nombre}.')
                if any(f.pk != fibra.pk or nueva for f in por_posicion.get(posicion, [])):
                    raise ValidationError(f'{numero} ya pertenece a otra fibra de la troncal {ruta_final.nombre}.')
                posiciones.add(posicion)
            auditorias = []

            def auditar(accion, anterior, nuevo, metadatos=None):
                auditorias.append(AuditoriaFibra(
                    fibra=fibra, accion=accion, origen=_origen_auditoria('EXCEL'),
                    usuario=usuario, lote_importacion=lote,
                    referencia_fibra=_referencia_fibra(fibra)[:300],
                    valor_anterior=str(anterior)[:300], valor_nuevo=str(nuevo)[:300],
                    metadatos=metadatos or {},
                ))

            anteriores = {}
            cambios = set()
            for columna, campo in campos_parche.items():
                valor = numero if campo == 'fibra_numero' else item.get(columna)
                if valor is None:
                    continue
                valor = normalizar_condicion_fisica(valor) if campo == 'condicion_fisica' else str(valor).strip()
                if getattr(fibra, campo) != valor:
                    anteriores[campo] = getattr(fibra, campo)
                    setattr(fibra, campo, valor)
                    cambios.add(campo)
            cambio_negocio = bool(cambios)
            if lote is not None and fibra.lote_importacion_id != lote.pk:
                anteriores['lote_importacion_id'] = fibra.lote_importacion_id
                fibra.lote_importacion = lote
                cambios.add('lote_importacion')
            if nueva:
                auditar('CREAR_FIBRA', '', fibra.codigo_fibra)
            elif cambios:
                auditar('ACTUALIZAR_METADATOS', anteriores,
                        {campo: getattr(fibra, campo) for campo in sorted(cambios)},
                        {'campos': sorted(cambios)})

            estado = item.get('estado')
            if estado is not None:
                estado = normalizar_estado_fibra(estado)
                origen = 'NO_INFORMADO' if estado == 'SIN_INFORMACION' else 'INFORMADO'
                anterior = f'{fibra.estado}/{fibra.origen_estado}'
                cambia_estado = (fibra.estado, fibra.origen_estado) != (estado, origen)
                fibra.estado, fibra.origen_estado = estado, origen
                if cambia_estado:
                    cambios.update(('estado', 'origen_estado'))
                    cambio_negocio = True
                # Un restablecimiento explícito se audita incluso si ya estaba sin información.
                if cambia_estado or (not nueva and estado == 'SIN_INFORMACION'):
                    auditar('RESTABLECER_ESTADO' if estado == 'SIN_INFORMACION' else 'CAMBIAR_ESTADO',
                            anterior, f'{estado}/{origen}')
            if ruta and not fibra.ruta_id:
                fibra.ruta = ruta
                cambios.add('ruta')
                cambio_negocio = True
                auditar('ASIGNAR_RUTA', 'Troncal pendiente', ruta.nombre)
            # Evitar N consultas por FK/unique: ya comprobadas arriba. No omitir
            # longitudes, choices ni clean(). La BD mantiene todos sus constraints.
            fibra.full_clean(exclude=['ruta', 'lote_importacion'],
                             validate_unique=False, validate_constraints=False)
            preparadas.append((item['fila'], fibra, nueva, cambios, auditorias))
            creadas += int(nueva)
            actualizadas += int(not nueva and cambio_negocio)
        except (ValueError, ValidationError) as exc:
            raise ValidationError(f"fila {item['fila']}: {exc}") from exc
        if progreso and (indice % 250 == 0 or indice == len(filas)):
            progreso('validar', indice, len(filas))

    for inicio in range(0, len(preparadas), 250):
        grupo = preparadas[inicio:inicio + 250]
        try:
            # Savepoint permite añadir contexto sin continuar con una transacción rota.
            with transaction.atomic():
                nuevas = [f for _, f, nueva, _, _ in grupo if nueva]
                InventarioFibra.objects.bulk_create(nuevas, batch_size=250)
                por_campos = defaultdict(list)
                for _, fibra, nueva, cambios, _ in grupo:
                    if not nueva and cambios:
                        por_campos[tuple(sorted(cambios))].append(fibra)
                for campos, fibras in por_campos.items():
                    if campos == ('lote_importacion',):
                        InventarioFibra.objects.filter(pk__in=[f.pk for f in fibras]).update(lote_importacion=lote)
                    else:
                        InventarioFibra.objects.bulk_update(fibras, campos, batch_size=250)
                # bulk_create completó los PK, también para los eventos de fibras nuevas.
                auditorias = []
                for _, fibra, _, _, eventos in grupo:
                    for evento in eventos:
                        evento.fibra_id = fibra.pk
                    auditorias.extend(eventos)
                AuditoriaFibra.objects.bulk_create(auditorias, batch_size=250)
        except IntegrityError as exc:
            raise ValidationError(
                f"filas {grupo[0][0]}–{grupo[-1][0]}: conflicto de integridad al guardar. "
                'Revise códigos y posiciones o vuelva a validar si hubo cambios simultáneos. '
                'No se guardó ninguna fila del archivo.'
            ) from exc
        if progreso:
            progreso('guardar', min(inicio + len(grupo), len(filas)), len(filas))
    return creadas, actualizadas
