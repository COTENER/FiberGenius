"""
Vistas de importación de datos CSV y configuración del sistema.
"""
import os
import io
import hashlib
import logging
import re
import zipfile
from decimal import Decimal, InvalidOperation

import pandas as pd
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import Count, F
from django.utils.timezone import now
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
import math
from ..models import (
    OTU, Ruta, PuertoOTU, CoordenadaRuta, Reserva, IDRuta,
    InventarioTramo, InventarioFibra, LoteImportacion,
)

logger = logging.getLogger('mapas')

VALORES_VACIOS = {'', '-', 'nan', 'none', 'null', 'sin_dato', 'no_aplica', 'n/a'}

TIPOS_TRAZADO = {
    'AEREO': 'AEREO',
    'AÉREO': 'AEREO',
    'SOTERRADO': 'SOTERRADO',
    'SUBTERRANEO': 'SOTERRADO',
    'SUBTERRÁNEO': 'SOTERRADO',
    'HIBRIDO': 'HIBRIDO',
    'HÍBRIDO': 'HIBRIDO',
}


def _validar_archivo_subido(archivo, *, es_zip=False):
    limite = int(getattr(settings, 'FIBERGENIUS_MAX_UPLOAD_BYTES', 25 * 1024 * 1024))
    tamano = getattr(archivo, 'size', None)
    if tamano is not None and tamano > limite:
        raise ValueError(f'El archivo supera el limite permitido de {limite // (1024 * 1024)} MB.')

    if not es_zip:
        return

    posicion = archivo.tell()
    try:
        archivo.seek(0)
        with zipfile.ZipFile(archivo, 'r') as paquete:
            entradas = [info for info in paquete.infolist() if not info.is_dir()]
            max_entradas = int(getattr(settings, 'FIBERGENIUS_MAX_ZIP_ENTRIES', 500))
            if len(entradas) > max_entradas:
                raise ValueError(f'El ZIP contiene mas de {max_entradas} archivos.')
            total = sum(info.file_size for info in entradas)
            max_total = int(
                getattr(
                    settings,
                    'FIBERGENIUS_MAX_ZIP_UNCOMPRESSED_BYTES',
                    100 * 1024 * 1024,
                )
            )
            if total > max_total:
                raise ValueError(
                    f'El contenido descomprimido supera {max_total // (1024 * 1024)} MB.'
                )
    except zipfile.BadZipFile as exc:
        raise ValueError('El archivo ZIP no es valido.') from exc
    finally:
        archivo.seek(posicion)


def _texto(valor, default=''):
    if valor is None or pd.isna(valor):
        return default
    texto = str(valor).strip()
    return default if texto.casefold() in VALORES_VACIOS else texto


def _decimal(valor, default=None):
    texto = _texto(valor)
    if not texto:
        return default
    try:
        return Decimal(texto.replace(',', '.'))
    except InvalidOperation as exc:
        raise ValueError(f'Número decimal inválido: {valor}') from exc


def _float(valor, default=None):
    numero = _decimal(valor, default=None)
    return default if numero is None else float(numero)


def _entero(valor, default=None):
    numero = _decimal(valor, default=None)
    if numero is None:
        return default
    if numero != numero.to_integral_value():
        raise ValueError(f'Se esperaba un entero y se recibió: {valor}')
    return int(numero)


def _normalizar_tipo_trazado(valor):
    texto = _texto(valor).upper()
    return TIPOS_TRAZADO.get(texto, texto or 'SIN_CLASIFICAR')


def _es_verdadero(valor):
    return _texto(valor).casefold() in {'1', 'true', 'si', 'sí', 'yes', 'x'}


def _codigo_tramo(valor, secuencia):
    codigo = _texto(valor).upper() or f'T{secuencia:03d}'
    if len(codigo) > 50 or not re.fullmatch(r'[A-Z0-9._-]+', codigo):
        raise ValueError(
            f'Código de tramo inválido: {codigo}. Usa letras, números, punto, guion o guion bajo.'
        )
    return codigo


def _leer_csv_normalizado(file):
    decoded_file = file.read().decode('utf-8-sig')
    dataframe = pd.read_csv(io.StringIO(decoded_file))
    dataframe.columns = [
        str(columna).strip().lower()
        for columna in dataframe.columns
    ]
    return dataframe


def _valor_fila(row, *columnas, default=''):
    for columna in columnas:
        if columna in row:
            valor = row.get(columna)
            if _texto(valor):
                return valor
    return default


def _entero_no_negativo(valor, nombre, default=None):
    numero = _entero(valor, default=default)
    if numero is not None and numero < 0:
        raise ValueError(f'{nombre} no puede ser negativo.')
    return numero


def _float_no_negativo(valor, nombre, default=None):
    numero = _float(valor, default=default)
    if numero is not None and numero < 0:
        raise ValueError(f'{nombre} no puede ser negativo.')
    return numero


def _url_o_none(valor):
    texto = _texto(valor)
    if not texto:
        return None
    URLValidator()(texto)
    return texto


def _resultado(total=0, creadas=0, actualizadas=0, rechazadas=0):
    return {
        'total': int(total),
        'creadas': int(creadas),
        'actualizadas': int(actualizadas),
        'rechazadas': int(rechazadas),
    }

def calcular_distancia_haversine(lat1, lon1, lat2, lon2):
    """Calcula la distancia en metros entre dos puntos geográficos (Haversine)."""
    R = 6371000  # radio terrestre en metros
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * \
        math.sin(delta_lambda / 2.0) ** 2
        
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

logger = logging.getLogger('mapas')


def save_uploaded_file(uploaded_file, destination_path):
    """Guarda un archivo subido en el sistema de archivos."""
    try:
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        with open(destination_path, 'wb+') as destination:
            for chunk in uploaded_file.chunks():
                destination.write(chunk)
        return True
    except OSError:
        logger.exception("Error al guardar el archivo en %s", destination_path)
        return False


@login_required
@never_cache
def configuracion(request):
    rutas = Ruta.objects.all().order_by('nombre')
    lotes = LoteImportacion.objects.select_related('usuario').order_by('-creado_en')[:20]
    return render(
        request,
        'configuracion/configuracion_index.html',
        {'rutas': rutas, 'lotes_importacion': lotes},
    )


@login_required
@permission_required('mapas.delete_ruta', raise_exception=True)
@require_POST
def eliminar_ruta_config(request):
    """Permite a los superusuarios eliminar una ruta específica y toda su información relacionada."""
    if request.user.is_superuser:
        ruta_id = request.POST.get('ruta_id')
        if ruta_id:
            ruta_obj = Ruta.objects.filter(id=ruta_id).first()
            if ruta_obj:
                nombre_ruta = ruta_obj.nombre
                
                # Transacción para garantizar consistencia al borrar
                with transaction.atomic():
                    # Eliminar coordenadas e inventario técnico de tramos asociados
                    CoordenadaRuta.objects.filter(ruta=ruta_obj).delete()
                    InventarioTramo.objects.filter(ruta=ruta_obj).delete()
                    # Desvincular puertos asociados
                    PuertoOTU.objects.filter(ruta_asociada=ruta_obj).update(ruta_asociada=None)
                    
                    # Eliminar la ruta en sí
                    ruta_obj.delete()
                    
                messages.success(request, f"La ruta '{nombre_ruta}' y todos sus datos geográficos/técnicos asociados fueron eliminados permanentemente del sistema.")
            else:
                messages.error(request, "La ruta seleccionada no existe.")
        else:
            messages.error(request, "No se seleccionó ninguna ruta para eliminar.")
    else:
        messages.error(request, "Acción no permitida o permisos insuficientes.")
        
    return redirect('configuracion')


@transaction.atomic
def _procesar_equipos_otu(file, lote=None):
    """Actualiza o crea OTUs y Puertos desde un CSV."""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))

    creadas = actualizadas = rechazadas = 0
    for row in df.to_dict('records'):
        nombre_otu = _texto(row.get('Descripcion'))
        if not nombre_otu:
            rechazadas += 1
            continue
        otu_identificador = {'nombre': nombre_otu}
        otu_datos = {
            'modelo': _texto(row.get('Modelo')) or None,
            'latitud': _decimal(row.get('Latitude')),
            'longitud': _decimal(row.get('Longitude')),
            'lote_importacion': lote,
        }
        otu_obj, otu_creada = OTU.objects.update_or_create(
            defaults=otu_datos,
            **otu_identificador
        )

        puerto_identificadores = {
            'otu': otu_obj,
            'numero': _entero(row.get('Puerto')),
        }
        puerto_datos = {
            'estado': _texto(row.get('Estado'), 'Desconocido'),
            'lote_importacion': lote,
        }
        _, puerto_creado = PuertoOTU.objects.update_or_create(
            defaults=puerto_datos,
            **puerto_identificadores
        )
        if otu_creada or puerto_creado:
            creadas += 1
        else:
            actualizadas += 1

    return _resultado(len(df), creadas, actualizadas, rechazadas)


@transaction.atomic
def _asociar_puertos_a_rutas(file, lote=None):
    """Lee el CSV de equipos y actualiza los puertos existentes para asociarlos a las rutas."""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))

    PuertoOTU.objects.all().update(ruta_asociada=None)

    puertos_actualizados = rechazados = 0
    for _, row in df.iterrows():
        if pd.notna(row['Enlaces']):
            try:
                puerto = PuertoOTU.objects.get(
                    otu__nombre=row['Descripcion'].strip(),
                    numero=row['Puerto']
                )
                ruta_obj = Ruta.objects.filter(nombre=row['Enlaces'].strip()).first()
                if puerto and ruta_obj:
                    puerto.ruta_asociada = ruta_obj
                    puerto.lote_importacion = lote
                    puerto.save()
                    puertos_actualizados += 1
                else:
                    rechazados += 1
            except PuertoOTU.DoesNotExist:
                logger.warning(f"Puerto {row['Puerto']} del OTU {row['Descripcion']} no encontrado en BD.")
                rechazados += 1

    return _resultado(len(df), actualizadas=puertos_actualizados, rechazadas=rechazados)


@transaction.atomic
def _procesar_ruta_otu(file, lote=None):
    """Actualiza o crea Rutas desde un CSV."""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))

    creadas = actualizadas = rechazadas = 0
    for row in df.to_dict('records'):
        ruta_nombre = _texto(row.get('Ruta'))
        if not ruta_nombre:
            rechazadas += 1
            continue
        otu_nombre = _texto(row.get('OTU'))
        otu_obj = OTU.objects.filter(nombre__iexact=otu_nombre).first() if otu_nombre else None
        ruta_identificador = {'nombre': ruta_nombre}
        ruta_datos = {
            'otu': otu_obj,
            'distancia_m': _float(row.get('Distancia (m)')),
            'olt': _texto(row.get('OLT')) or None,
            'slot_olt': _entero(row.get('SLOT OLT')),
            'puerto_olt': _entero(row.get('PUERTO OLT')),
            'pon': _texto(row.get('PON')) or None,
            'enlace': _url_o_none(row.get('Enlace')),
            'lote_importacion': lote,
        }
        _, creada = Ruta.objects.update_or_create(
            defaults=ruta_datos,
            **ruta_identificador
        )
        creadas += int(creada)
        actualizadas += int(not creada)

    return _resultado(len(df), creadas, actualizadas, rechazadas)


@transaction.atomic
def _procesar_coordenadas_zip(file, lote=None):
    """Vincula geometrías ZIP con rutas y tramos ya inventariados."""
    total = actualizadas = rechazadas = 0
    with zipfile.ZipFile(file, 'r') as zip_ref:
        for filename in zip_ref.namelist():
            if not filename.lower().endswith('.csv'):
                continue
            ruta_nombre = os.path.splitext(os.path.basename(filename))[0]
            ruta_obj = Ruta.objects.filter(nombre__iexact=ruta_nombre).first()
            if ruta_obj is None:
                raise ValueError(
                    f'La ruta {ruta_nombre} no existe en el inventario. '
                    'Carga primero las troncales y sus tramos.'
                )
            with zip_ref.open(filename) as csv_file:
                contenido = csv_file.read().decode('utf-8-sig')
                df_coords = pd.read_csv(io.StringIO(contenido))
            cantidad, _ = _vincular_geometria_existente(
                ruta_obj,
                df_coords,
                lote=lote,
            )
            total += cantidad
            actualizadas += cantidad

    return _resultado(
        total + rechazadas,
        actualizadas=actualizadas,
        rechazadas=rechazadas,
    )


@transaction.atomic
def _procesar_reservas(file, lote=None):
    """Actualiza o crea Reservas desde un CSV."""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))

    creadas = actualizadas = rechazadas = 0
    for numero_fila, row in enumerate(df.to_dict('records'), start=2):
        ruta_nombre = _texto(row.get('Enlace'))
        ruta_obj = Ruta.objects.filter(nombre__iexact=ruta_nombre).first()
        if ruta_obj:
            nombre = _texto(row.get('Landmark name', row.get('Nombre')))
            tipo = _texto(row.get('Connection type', row.get('Tipo')))
            lat = _decimal(row.get('Latitud'))
            lon = _decimal(row.get('Longitud'))
            if not nombre or lat is None or lon is None:
                raise ValueError(f'Fila {numero_fila}: reserva sin nombre o coordenadas válidas.')

            reserva_identificadores = {
                'ruta': ruta_obj,
                'nombre': nombre,
                'latitud': lat,
                'longitud': lon,
            }
            reserva_datos = {
                'tipo': tipo,
                'tipo_original_cliente': tipo,
                'reserva_m': _float(row.get('Reserva (m)', row.get('Reserva_m')), 0.0),
                'codigo': _texto(row.get('Codigo', row.get('Código'))) or None,
                'orden_en_ruta': _entero(row.get('Orden')),
                'progresiva_m': _float(row.get('Progresiva (m)', row.get('Progresiva_m'))),
                'lote_importacion': lote,
            }
            _, creada = Reserva.objects.update_or_create(
                defaults=reserva_datos,
                **reserva_identificadores
            )
            creadas += int(creada)
            actualizadas += int(not creada)
        else:
            rechazadas += 1

    return _resultado(len(df), creadas, actualizadas, rechazadas)


@transaction.atomic
def _procesar_id_rutas(file, lote=None):
    """Carga el archivo CSV de IDs de Rutas."""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))

    creadas = actualizadas = 0
    for _, row in df.iterrows():
        ruta_nombre = row['Ruta'].strip()
        ruta_obj = Ruta.objects.filter(nombre__iexact=ruta_nombre).first()
        if not ruta_obj:
            raise ValueError(f"La ruta '{ruta_nombre}' no existe.")
        identificador = (
            IDRuta.objects.filter(ruta_obj=ruta_obj).first()
            or IDRuta.objects.filter(ruta__iexact=ruta_nombre, ruta_obj__isnull=True).first()
            or IDRuta(ruta_obj=ruta_obj)
        )
        identificador.ruta_obj = ruta_obj
        identificador.ruta = ruta_obj.nombre
        identificador.id_onmsi = int(row['ID'])
        identificador.lote_importacion = lote
        creada = identificador.pk is None
        identificador.full_clean()
        identificador.save()
        creadas += int(creada)
        actualizadas += int(not creada)

    return _resultado(len(df), creadas, actualizadas)



def _actualizar_geometria_segmentada(ruta_obj, df_coords, lote=None):
    """Valida y actualiza una geometría sin destruir la identidad de sus tramos."""
    df_coords = df_coords.copy()
    df_coords.columns = [str(columna).strip().lower() for columna in df_coords.columns]
    faltantes = {'latitude', 'longitude'} - set(df_coords.columns)
    if faltantes:
        raise ValueError(
            f"Faltan columnas obligatorias: {', '.join(sorted(faltantes))}"
        )
    if len(df_coords) < 2:
        raise ValueError(f'La ruta {ruta_obj.nombre} debe contener al menos dos coordenadas.')

    segmentacion_explicita = (
        'tramo_secuencia' in df_coords.columns
        and any(_texto(valor) for valor in df_coords['tramo_secuencia'])
    )
    if segmentacion_explicita and any(
        not _texto(valor) for valor in df_coords['tramo_secuencia']
    ):
        raise ValueError(
            f'La ruta {ruta_obj.nombre} mezcla filas con y sin tramo_secuencia.'
        )

    segmentos = {}
    coordenadas = []
    secuencia_actual = 1
    secuencia_anterior = None
    tipo_anterior = None
    lat_anterior = lon_anterior = None
    distancia_total = 0.0

    for indice, row in df_coords.reset_index(drop=True).iterrows():
        latitud = _float(row.get('latitude'))
        longitud = _float(row.get('longitude'))
        if latitud is None or longitud is None:
            raise ValueError(
                f'Ruta {ruta_obj.nombre}, fila {indice + 2}: coordenada vacía.'
            )
        if not -90 <= latitud <= 90 or not -180 <= longitud <= 180:
            raise ValueError(
                f'Ruta {ruta_obj.nombre}, fila {indice + 2}: coordenada fuera de rango.'
            )

        tipo = _normalizar_tipo_trazado(row.get('tipo_trazado'))
        if segmentacion_explicita:
            secuencia = _entero(row.get('tramo_secuencia'))
            if not secuencia or secuencia < 1:
                raise ValueError(
                    f'Ruta {ruta_obj.nombre}, fila {indice + 2}: secuencia inválida.'
                )
            if secuencia_anterior is None and secuencia != 1:
                raise ValueError(
                    f'Ruta {ruta_obj.nombre}: la segmentación debe comenzar en 1.'
                )
            if (
                secuencia_anterior is not None
                and secuencia not in (secuencia_anterior, secuencia_anterior + 1)
            ):
                raise ValueError(
                    f'Ruta {ruta_obj.nombre}, fila {indice + 2}: los tramos deben '
                    'aparecer en bloques continuos y ordenados.'
                )
        else:
            if indice and (
                _es_verdadero(row.get('new_seg'))
                or (tipo_anterior is not None and tipo != tipo_anterior)
            ):
                secuencia_actual += 1
            secuencia = secuencia_actual

        codigo = _codigo_tramo(row.get('codigo_tramo'), secuencia)
        meta = segmentos.setdefault(
            secuencia,
            {
                'codigo': codigo,
                'tipo': tipo,
                'distancia_m': 0.0,
                'puntos': 0,
            },
        )
        if meta['codigo'] != codigo:
            raise ValueError(
                f'Ruta {ruta_obj.nombre}: la secuencia {secuencia} tiene códigos distintos.'
            )
        if meta['tipo'] != tipo:
            raise ValueError(
                f'Ruta {ruta_obj.nombre}: el tramo {codigo} mezcla tipos de trazado.'
            )
        meta['puntos'] += 1

        if lat_anterior is not None:
            distancia = calcular_distancia_haversine(
                lat_anterior, lon_anterior, latitud, longitud
            )
            distancia_total += distancia
            meta['distancia_m'] += distancia
        lat_anterior, lon_anterior = latitud, longitud
        tipo_anterior = tipo
        secuencia_anterior = secuencia
        coordenadas.append({
            'orden': indice + 1,
            'latitud': Decimal(str(latitud)),
            'longitud': Decimal(str(longitud)),
            'secuencia': secuencia,
        })

    secuencias = sorted(segmentos)
    if secuencias != list(range(1, secuencias[-1] + 1)):
        raise ValueError(
            f'Ruta {ruta_obj.nombre}: las secuencias deben ser continuas desde 1.'
        )
    codigos = [segmentos[secuencia]['codigo'] for secuencia in secuencias]
    if len(codigos) != len(set(codigos)):
        raise ValueError(
            f'Ruta {ruta_obj.nombre}: cada tramo debe tener un código único.'
        )

    existentes = list(
        InventarioTramo.objects.filter(ruta=ruta_obj).order_by(
            'tramo_secuencia', 'id'
        )
    )
    por_secuencia = {tramo.tramo_secuencia: tramo for tramo in existentes}
    primer_tramo = existentes[0] if existentes else None
    datos_base = {}
    if primer_tramo:
        for campo in (
            'estado', 'capacidad', 'hub_site', 'destino', 'marca_modelo',
            'tipo_fibra', 'serial', 'odf_nombre',
        ):
            datos_base[campo] = getattr(primer_tramo, campo)
    if 'hub_site' in df_coords.columns:
        hub = next(
            (_texto(valor) for valor in df_coords['hub_site'] if _texto(valor)),
            '',
        )
        if hub:
            datos_base['hub_site'] = hub

    tramos_usados = {}
    for secuencia, meta in segmentos.items():
        tramo = por_secuencia.get(secuencia)
        calidad = (
            InventarioTramo.CALIDAD_PENDIENTE
            if segmentacion_explicita
            else InventarioTramo.CALIDAD_LEGACY
        )
        if meta['puntos'] < 2:
            calidad = InventarioTramo.CALIDAD_REVISION
        if tramo:
            if (
                tramo.codigo_tramo != meta['codigo']
                and tramo.estado_calidad != InventarioTramo.CALIDAD_LEGACY
            ):
                raise ValueError(
                    f'La secuencia {secuencia} ya corresponde al código '
                    f'{tramo.codigo_tramo}; no se cambió su identidad.'
                )
            tramo.codigo_tramo = meta['codigo']
            tramo.tipo_trazado = meta['tipo']
            tramo.distancia_m = meta['distancia_m']
            tramo.estado_calidad = calidad
            tramo.vigente = True
            tramo.lote_importacion = lote
            tramo.save(update_fields=[
                'codigo_tramo', 'tipo_trazado', 'distancia_m',
                'estado_calidad', 'vigente', 'lote_importacion',
                'actualizado_en',
            ])
        else:
            valores = dict(datos_base)
            if secuencia != 1:
                valores.update({
                    'mufas': 0,
                    'splitters': 0,
                    'reservas_m': 0.0,
                    'hilos_ocupados': 0,
                    'hilos_libres': 0,
                })
            tramo = InventarioTramo.objects.create(
                ruta=ruta_obj,
                tramo_secuencia=secuencia,
                codigo_tramo=meta['codigo'],
                estado_calidad=calidad,
                vigente=True,
                tipo_trazado=meta['tipo'],
                distancia_m=meta['distancia_m'],
                lote_importacion=lote,
                **valores,
            )
        tramos_usados[secuencia] = tramo

    ids_usados = [tramo.pk for tramo in tramos_usados.values()]
    InventarioTramo.objects.filter(ruta=ruta_obj).exclude(
        pk__in=ids_usados
    ).update(
        vigente=False,
        estado_calidad=InventarioTramo.CALIDAD_REVISION,
        actualizado_en=now(),
    )

    existentes_coord = {
        coordenada.orden: coordenada
        for coordenada in CoordenadaRuta.objects.filter(ruta=ruta_obj)
    }
    crear = []
    actualizar = []
    ordenes = []
    for dato in coordenadas:
        ordenes.append(dato['orden'])
        tramo = tramos_usados[dato['secuencia']]
        coordenada = existentes_coord.get(dato['orden'])
        if coordenada:
            coordenada.latitud = dato['latitud']
            coordenada.longitud = dato['longitud']
            coordenada.tramo_secuencia = dato['secuencia']
            coordenada.tramo = tramo
            coordenada.lote_importacion = lote
            actualizar.append(coordenada)
        else:
            crear.append(CoordenadaRuta(
                ruta=ruta_obj,
                latitud=dato['latitud'],
                longitud=dato['longitud'],
                orden=dato['orden'],
                tramo_secuencia=dato['secuencia'],
                tramo=tramo,
                lote_importacion=lote,
            ))
    if crear:
        CoordenadaRuta.objects.bulk_create(crear, batch_size=1000)
    if actualizar:
        CoordenadaRuta.objects.bulk_update(
            actualizar,
            [
                'latitud', 'longitud', 'tramo_secuencia',
                'tramo', 'lote_importacion',
            ],
            batch_size=500,
        )
    CoordenadaRuta.objects.filter(ruta=ruta_obj).exclude(
        orden__in=ordenes
    ).delete()

    ruta_obj.distancia_m = distancia_total
    if lote is not None:
        ruta_obj.lote_importacion = lote
        ruta_obj.save(update_fields=['distancia_m', 'lote_importacion'])
    else:
        ruta_obj.save(update_fields=['distancia_m'])
    return len(coordenadas), len(segmentos)


def _vincular_geometria_existente(ruta_obj, df_coords, lote=None):
    """Reemplaza solo la geometría y exige inventario de tramos existente."""
    df_coords = df_coords.copy()
    df_coords.columns = [
        str(columna).strip().lower()
        for columna in df_coords.columns
    ]
    faltantes = {'latitude', 'longitude'} - set(df_coords.columns)
    if faltantes:
        raise ValueError(
            f"Faltan columnas obligatorias: {', '.join(sorted(faltantes))}"
        )
    if len(df_coords) < 2:
        raise ValueError(
            f'La ruta {ruta_obj.nombre} debe contener al menos dos coordenadas.'
        )

    if (
        'orden_punto' in df_coords.columns
        and any(_texto(valor) for valor in df_coords['orden_punto'])
    ):
        if any(not _texto(valor) for valor in df_coords['orden_punto']):
            raise ValueError(
                f'La ruta {ruta_obj.nombre} mezcla puntos con y sin orden_punto.'
            )
        ordenes = [
            _entero(valor)
            for valor in df_coords['orden_punto']
        ]
        if any(orden is None or orden < 1 for orden in ordenes):
            raise ValueError(
                f'La ruta {ruta_obj.nombre} contiene un orden_punto inválido.'
            )
        if len(ordenes) != len(set(ordenes)):
            raise ValueError(
                f'La ruta {ruta_obj.nombre} contiene órdenes de punto duplicados.'
            )
        df_coords = (
            df_coords.assign(_orden_importado=ordenes)
            .sort_values('_orden_importado')
            .reset_index(drop=True)
        )
    else:
        df_coords = df_coords.reset_index(drop=True)

    tramos = list(
        InventarioTramo.objects.filter(
            ruta=ruta_obj,
            vigente=True,
        ).order_by('tramo_secuencia', 'id')
    )
    if not tramos:
        raise ValueError(
            f'La ruta {ruta_obj.nombre} no tiene tramos de inventario. '
            'Carga primero el inventario de tramos.'
        )
    por_codigo = {
        (tramo.codigo_tramo or '').strip().upper(): tramo
        for tramo in tramos
    }
    por_secuencia = {
        tramo.tramo_secuencia: tramo
        for tramo in tramos
    }

    segmentacion_explicita = (
        (
            'codigo_tramo' in df_coords.columns
            and any(_texto(valor) for valor in df_coords['codigo_tramo'])
        )
        or (
            'tramo_secuencia' in df_coords.columns
            and any(_texto(valor) for valor in df_coords['tramo_secuencia'])
        )
    )
    if segmentacion_explicita:
        for columna in ('codigo_tramo', 'tramo_secuencia'):
            if (
                columna in df_coords.columns
                and any(_texto(valor) for valor in df_coords[columna])
                and any(not _texto(valor) for valor in df_coords[columna])
            ):
                raise ValueError(
                    f'La ruta {ruta_obj.nombre} mezcla filas con y sin {columna}.'
                )

    coordenadas = []
    distancias = {tramo.pk: 0.0 for tramo in tramos}
    puntos_por_tramo = {tramo.pk: 0 for tramo in tramos}
    anterior_por_tramo = {}
    secuencia_legacy = 1
    tipo_anterior = None
    secuencia_anterior = None
    secuencias_cerradas = set()

    for indice, row in df_coords.iterrows():
        latitud = _float(row.get('latitude'))
        longitud = _float(row.get('longitude'))
        if latitud is None or longitud is None:
            raise ValueError(
                f'Ruta {ruta_obj.nombre}, fila {indice + 2}: coordenada vacía.'
            )
        if not -90 <= latitud <= 90 or not -180 <= longitud <= 180:
            raise ValueError(
                f'Ruta {ruta_obj.nombre}, fila {indice + 2}: '
                'coordenada fuera de rango.'
            )

        tramo = None
        codigo_entrada = _texto(row.get('codigo_tramo')).upper()
        secuencia_entrada = _entero(row.get('tramo_secuencia'))
        if codigo_entrada:
            tramo = por_codigo.get(codigo_entrada)
            if not tramo:
                raise ValueError(
                    f'Ruta {ruta_obj.nombre}, fila {indice + 2}: el tramo '
                    f'{codigo_entrada} no existe en el inventario.'
                )
            if (
                secuencia_entrada is not None
                and tramo.tramo_secuencia != secuencia_entrada
            ):
                raise ValueError(
                    f'Ruta {ruta_obj.nombre}: {codigo_entrada} pertenece a la '
                    f'secuencia {tramo.tramo_secuencia}, no a '
                    f'{secuencia_entrada}.'
                )
        elif secuencia_entrada is not None:
            tramo = por_secuencia.get(secuencia_entrada)
            if not tramo:
                raise ValueError(
                    f'Ruta {ruta_obj.nombre}, fila {indice + 2}: la secuencia '
                    f'{secuencia_entrada} no existe en el inventario.'
                )
        elif len(tramos) == 1:
            tramo = tramos[0]
        else:
            tipo_actual = _normalizar_tipo_trazado(row.get('tipo_trazado'))
            if indice and (
                _es_verdadero(row.get('new_seg'))
                or (
                    tipo_anterior is not None
                    and tipo_actual != tipo_anterior
                )
            ):
                secuencia_legacy += 1
            tramo = por_secuencia.get(secuencia_legacy)
            if not tramo:
                raise ValueError(
                    f'Ruta {ruta_obj.nombre}, fila {indice + 2}: la geometría '
                    f'infiere T{secuencia_legacy:03d}, pero ese tramo no existe.'
                )
            tipo_anterior = tipo_actual

        tipo_entrada = _texto(row.get('tipo_trazado'))
        if tipo_entrada:
            tipo_entrada = _normalizar_tipo_trazado(tipo_entrada)
            tipo_inventario = _normalizar_tipo_trazado(
                tramo.tipo_trazado
            )
            if (
                tipo_inventario != 'SIN_CLASIFICAR'
                and tipo_entrada != tipo_inventario
            ):
                raise ValueError(
                    f'Ruta {ruta_obj.nombre}, tramo {tramo.codigo_tramo}: '
                    f'la geometría declara {tipo_entrada}, pero el inventario '
                    f'declara {tipo_inventario}.'
                )

        if secuencia_anterior is not None and tramo.tramo_secuencia != secuencia_anterior:
            secuencias_cerradas.add(secuencia_anterior)
        if tramo.tramo_secuencia in secuencias_cerradas:
            raise ValueError(
                f'Ruta {ruta_obj.nombre}: las coordenadas del tramo '
                f'{tramo.codigo_tramo} no forman un bloque continuo.'
            )
        secuencia_anterior = tramo.tramo_secuencia

        anterior = anterior_por_tramo.get(tramo.pk)
        if anterior:
            distancias[tramo.pk] += calcular_distancia_haversine(
                anterior[0],
                anterior[1],
                latitud,
                longitud,
            )
        anterior_por_tramo[tramo.pk] = (latitud, longitud)
        puntos_por_tramo[tramo.pk] += 1
        coordenadas.append({
            'orden': indice + 1,
            'latitud': Decimal(str(latitud)),
            'longitud': Decimal(str(longitud)),
            'tramo': tramo,
        })

    insuficientes = [
        tramo.codigo_tramo
        for tramo in tramos
        if 0 < puntos_por_tramo[tramo.pk] < 2
    ]
    if insuficientes:
        raise ValueError(
            f'La geometría de {ruta_obj.nombre} necesita al menos dos puntos '
            f'por tramo: {", ".join(insuficientes)}.'
        )

    tramos_incluidos = {
        dato['tramo'].pk
        for dato in coordenadas
    }
    existentes = list(
        CoordenadaRuta.objects.filter(ruta=ruta_obj)
        .select_related('tramo')
        .order_by('orden', 'id')
    )
    existentes_por_tramo = {
        tramo.pk: []
        for tramo in tramos
    }
    otras_coordenadas = []
    for coordenada in existentes:
        if coordenada.tramo_id in existentes_por_tramo:
            existentes_por_tramo[coordenada.tramo_id].append(coordenada)
        else:
            otras_coordenadas.append(coordenada)

    nuevas_por_tramo = {
        tramo.pk: []
        for tramo in tramos
    }
    for dato in coordenadas:
        nuevas_por_tramo[dato['tramo'].pk].append(dato)

    crear = []
    finales_por_tramo = {}
    ids_eliminar = []
    for tramo in tramos:
        previas = existentes_por_tramo[tramo.pk]
        if tramo.pk not in tramos_incluidos:
            finales_por_tramo[tramo.pk] = previas
            continue

        datos_nuevos = nuevas_por_tramo[tramo.pk]
        finales_tramo = []
        for indice, dato in enumerate(datos_nuevos):
            if indice < len(previas):
                coordenada = previas[indice]
                coordenada.latitud = dato['latitud']
                coordenada.longitud = dato['longitud']
                coordenada.tramo_secuencia = tramo.tramo_secuencia
                coordenada.tramo = tramo
                coordenada.lote_importacion = lote
            else:
                coordenada = CoordenadaRuta(
                    ruta=ruta_obj,
                    latitud=dato['latitud'],
                    longitud=dato['longitud'],
                    tramo_secuencia=tramo.tramo_secuencia,
                    tramo=tramo,
                    lote_importacion=lote,
                )
                crear.append(coordenada)
            finales_tramo.append(coordenada)
        ids_eliminar.extend(
            coordenada.pk
            for coordenada in previas[len(datos_nuevos):]
        )
        finales_por_tramo[tramo.pk] = finales_tramo

    if ids_eliminar:
        CoordenadaRuta.objects.filter(pk__in=ids_eliminar).delete()

    # Solo se reconstruye el orden global. Las coordenadas de los tramos
    # omitidos conservan sus valores e identificadores.
    finales = []
    for tramo in tramos:
        finales.extend(finales_por_tramo[tramo.pk])
    finales.extend(otras_coordenadas)

    existentes_finales = [
        coordenada
        for coordenada in finales
        if coordenada.pk is not None
    ]
    if existentes_finales:
        max_orden = max(
            (coordenada.orden or 0 for coordenada in existentes),
            default=0,
        )
        desplazamiento = max_orden + len(finales) + 1000
        CoordenadaRuta.objects.filter(
            pk__in=[coordenada.pk for coordenada in existentes_finales]
        ).update(orden=F('orden') + desplazamiento)

    for orden, coordenada in enumerate(finales, start=1):
        coordenada.orden = orden
    if existentes_finales:
        CoordenadaRuta.objects.bulk_update(
            existentes_finales,
            [
                'latitud',
                'longitud',
                'orden',
                'tramo_secuencia',
                'tramo',
                'lote_importacion',
            ],
            batch_size=500,
        )
    if crear:
        CoordenadaRuta.objects.bulk_create(crear, batch_size=1000)

    tramos_actualizar = []
    for tramo in tramos:
        if puntos_por_tramo[tramo.pk]:
            tramo.distancia_m = distancias[tramo.pk]
            tramos_actualizar.append(tramo)
    if tramos_actualizar:
        InventarioTramo.objects.bulk_update(
            tramos_actualizar,
            ['distancia_m'],
            batch_size=200,
        )
    puntos_actuales = {
        fila['tramo_id']: fila['total']
        for fila in CoordenadaRuta.objects.filter(
            ruta=ruta_obj,
            tramo_id__in=[tramo.pk for tramo in tramos],
        )
        .values('tramo_id')
        .annotate(total=Count('id'))
    }
    geometria_completa = all(
        puntos_actuales.get(tramo.pk, 0) >= 2
        for tramo in tramos
    )
    if geometria_completa:
        ids_tramos_activos = [tramo.pk for tramo in tramos]
        puntos_ruta = list(
            CoordenadaRuta.objects.filter(
                ruta=ruta_obj,
                tramo_id__in=ids_tramos_activos,
            )
            .order_by('orden')
            .values_list('latitud', 'longitud')
        )
        ruta_obj.distancia_m = sum(
            calcular_distancia_haversine(
                float(puntos_ruta[indice][0]),
                float(puntos_ruta[indice][1]),
                float(puntos_ruta[indice + 1][0]),
                float(puntos_ruta[indice + 1][1]),
            )
            for indice in range(len(puntos_ruta) - 1)
        )
    else:
        # Una geometria parcial nunca se presenta como longitud total.
        ruta_obj.distancia_m = None
    ruta_obj.save(update_fields=['distancia_m'])
    return len(coordenadas), len({
        dato['tramo'].pk
        for dato in coordenadas
    })


@transaction.atomic
def _procesar_coordenadas_inventario_zip(file, lote=None):
    """Importa geometrías por archivo manteniendo IDs y relaciones existentes."""
    total = creadas = rechazadas = 0
    with zipfile.ZipFile(file, 'r') as zip_ref:
        for filename in zip_ref.namelist():
            if not filename.lower().endswith('.csv'):
                continue
            ruta_nombre = os.path.splitext(os.path.basename(filename))[0]
            ruta_obj = Ruta.objects.filter(nombre=ruta_nombre).first()
            if not ruta_obj:
                rechazadas += 1
                continue
            with zip_ref.open(filename) as csv_file:
                contenido = csv_file.read().decode('utf-8')
                df_coords = pd.read_csv(io.StringIO(contenido))
            cantidad, _ = _vincular_geometria_existente(
                ruta_obj, df_coords, lote=lote
            )
            total += cantidad
            creadas += cantidad
    return _resultado(total + rechazadas, creadas, rechazadas=rechazadas)


@transaction.atomic
def _procesar_coordenadas_inventario_zip_legacy(file, lote=None):
    """
    Importa coordenadas y metadatos de inventario simultáneamente.
    Se crea un nuevo "tramo" si `new_seg` es True o si el `tipo_trazado` cambia.
    """
    total = creadas = rechazadas = 0
    with zipfile.ZipFile(file, 'r') as zip_ref:
        for filename in zip_ref.namelist():
            if filename.endswith('.csv'):
                ruta_nombre = os.path.splitext(os.path.basename(filename))[0]
                ruta_obj = Ruta.objects.filter(nombre=ruta_nombre).first()

                if ruta_obj:
                    # Respaldar datos manuales previos (del primer tramo) para no perder "Capacidad", "Hub", etc.
                    tramo_previo = InventarioTramo.objects.filter(ruta=ruta_obj).order_by('tramo_secuencia').first()
                    datos_respaldo = {}
                    if tramo_previo:
                        datos_respaldo = {
                            'estado': tramo_previo.estado,
                            'capacidad': tramo_previo.capacidad,
                            'hub_site': tramo_previo.hub_site,
                            'destino': tramo_previo.destino,
                            'marca_modelo': tramo_previo.marca_modelo,
                            'tipo_fibra': tramo_previo.tipo_fibra,
                            'serial': tramo_previo.serial,
                            'mufas': tramo_previo.mufas,
                            'splitters': tramo_previo.splitters,
                            'odf_nombre': tramo_previo.odf_nombre,
                            'hilos_ocupados': tramo_previo.hilos_ocupados,
                            'hilos_libres': tramo_previo.hilos_libres,
                            'distancia_m': tramo_previo.distancia_m,
                            'reservas_m': tramo_previo.reservas_m
                        }

                    # Limpiamos coordenadas antiguas e inventario para evitar traslapes
                    CoordenadaRuta.objects.filter(ruta=ruta_obj).delete()
                    InventarioTramo.objects.filter(ruta=ruta_obj).delete()
                    
                    with zip_ref.open(filename) as csv_file:
                        csv_content = csv_file.read().decode('utf-8')
                        df_coords = pd.read_csv(io.StringIO(csv_content))
                        
                        coords_a_crear = []
                        tramos_data = {}
                        
                        tramo_actual = 1
                        tipo_trazado_actual = None
                        
                        distancia_total = 0.0
                        prev_lat = None
                        prev_lon = None
                        
                        for i, row_coord in df_coords.iterrows():
                            new_seg = str(row_coord.get('new_seg', 'False')).strip().lower()
                            es_new_seg = new_seg in ['true', '1']
                            
                            tipo_trazado_raw = str(row_coord.get('tipo_trazado', '')).strip().upper()
                            if not tipo_trazado_raw or tipo_trazado_raw == 'NAN':
                                tipo_trazado_raw = 'DESCONOCIDO'
                                
                            if i == 0:
                                tipo_trazado_actual = tipo_trazado_raw
                            else:
                                if tipo_trazado_raw != tipo_trazado_actual:
                                    es_new_seg = True
                                    tipo_trazado_actual = tipo_trazado_raw
                                    
                            if es_new_seg and i > 0:
                                tramo_actual += 1

                            if tramo_actual not in tramos_data:
                                tramos_data[tramo_actual] = {
                                    'tipo_trazado': tipo_trazado_actual if pd.notna(row_coord.get('tipo_trazado')) else None,
                                    'distancia_m': 0.0
                                }
                                
                            lat = float(row_coord['Latitude'])
                            lon = float(row_coord['Longitude'])
                            
                            distancia_segmento = 0.0
                            if prev_lat is not None and prev_lon is not None:
                                distancia_segmento = calcular_distancia_haversine(prev_lat, prev_lon, lat, lon)
                                distancia_total += distancia_segmento
                                tramos_data[tramo_actual]['distancia_m'] += distancia_segmento
                                
                            prev_lat = lat
                            prev_lon = lon
                                
                            coords_a_crear.append(
                                CoordenadaRuta(
                                    ruta=ruta_obj,
                                    latitud=Decimal(str(lat)),
                                    longitud=Decimal(str(lon)),
                                    orden=i + 1,
                                    tramo_secuencia=tramo_actual,
                                    lote_importacion=lote,
                                )
                            )
                            
                        # Guardamos Coordenadas
                        CoordenadaRuta.objects.bulk_create(coords_a_crear)
                        total += len(df_coords)
                        creadas += len(coords_a_crear)
                        
                        # Actualizar distancia de la ruta
                        ruta_obj.distancia_m = distancia_total
                        if lote is not None:
                            ruta_obj.lote_importacion = lote
                            ruta_obj.save(update_fields=['distancia_m', 'lote_importacion'])
                        else:
                            ruta_obj.save(update_fields=['distancia_m'])
                        
                        # Guardamos InventarioTramos (solo inicial con tipo_trazado)
                        inventario_a_crear = []
                        for num_tramo, meta in tramos_data.items():
                            tramo_datos = dict(datos_respaldo)
                            if num_tramo != 1:
                                tramo_datos.update({
                                    'mufas': 0,
                                    'splitters': 0,
                                    'reservas_m': 0.0,
                                    'hilos_ocupados': 0,
                                    'hilos_libres': 0,
                                })
                            tramo_datos['distancia_m'] = meta['distancia_m']
                            inventario_a_crear.append(
                                InventarioTramo(
                                    ruta=ruta_obj,
                                    tramo_secuencia=num_tramo,
                                    codigo_tramo=f"T{num_tramo:03d}",
                                    tipo_trazado=meta['tipo_trazado'],
                                    estado_calidad=InventarioTramo.CALIDAD_LEGACY,
                                    lote_importacion=lote,
                                    **tramo_datos
                                )
                            )
                        InventarioTramo.objects.bulk_create(inventario_a_crear)
                        tramos_por_secuencia = {
                            tramo.tramo_secuencia: tramo
                            for tramo in InventarioTramo.objects.filter(ruta=ruta_obj)
                        }
                        for secuencia, tramo in tramos_por_secuencia.items():
                            CoordenadaRuta.objects.filter(
                                ruta=ruta_obj,
                                tramo_secuencia=secuencia,
                            ).update(tramo=tramo)
                else:
                    rechazadas += 1

    return _resultado(total + rechazadas, creadas, rechazadas=rechazadas)

def _capacidad_desde_fila(row):
    valor = _valor_fila(
        row,
        'capacidad_hilos_declarada',
        'capacidad',
    )
    texto = _texto(valor)
    if not texto:
        return None
    coincidencia = re.search(r'\d+', texto)
    if not coincidencia:
        raise ValueError(f'Capacidad inválida: {texto}.')
    capacidad = int(coincidencia.group(0))
    if capacidad < 1:
        raise ValueError('La capacidad debe ser mayor que cero.')
    return capacidad


def _procesar_troncales_dataframe(df, lote=None):
    from ..models import InventarioODF

    if not {'ruta', 'troncal', 'ident'} & set(df.columns):
        raise ValueError('Falta la columna obligatoria ruta.')

    creadas = actualizadas = rechazadas = 0
    nombres_vistos = set()
    for numero_fila, row in enumerate(df.to_dict('records'), start=2):
        ruta_nombre = _texto(_valor_fila(row, 'ruta', 'troncal', 'ident'))
        if not ruta_nombre:
            rechazadas += 1
            continue
        clave_nombre = ruta_nombre.casefold()
        if clave_nombre in nombres_vistos:
            raise ValueError(
                f'Fila {numero_fila}: la troncal {ruta_nombre} aparece más de una vez.'
            )
        nombres_vistos.add(clave_nombre)

        ruta_obj = Ruta.objects.filter(nombre__iexact=ruta_nombre).first()
        creada = ruta_obj is None
        if creada:
            ruta_obj = Ruta(nombre=ruta_nombre)

        columnas_capacidad = {
            'capacidad_hilos_declarada',
            'capacidad',
        }
        capacidad = (
            _capacidad_desde_fila(row)
            if columnas_capacidad & set(row)
            else ruta_obj.capacidad_hilos_declarada
        )
        ocupados = _entero_no_negativo(
            _valor_fila(
                row,
                'hilos_ocupados_declarados',
                'hilos_ocupados',
            ),
            'Los hilos ocupados',
        )
        libres = _entero_no_negativo(
            _valor_fila(
                row,
                'hilos_libres_declarados',
                'hilos_libres',
            ),
            'Los hilos libres',
        )
        reservados = _entero_no_negativo(
            _valor_fila(
                row,
                'hilos_reservados_declarados',
                'hilos_reservados',
            ),
            'Los hilos reservados',
        )
        ocupados_validar = (
            ocupados
            if {
                'hilos_ocupados_declarados',
                'hilos_ocupados',
            } & set(row)
            else ruta_obj.hilos_ocupados_declarados
        )
        libres_validar = (
            libres
            if {
                'hilos_libres_declarados',
                'hilos_libres',
            } & set(row)
            else ruta_obj.hilos_libres_declarados
        )
        reservados_validar = (
            reservados
            if {
                'hilos_reservados_declarados',
                'hilos_reservados',
            } & set(row)
            else ruta_obj.hilos_reservados_declarados
        )
        if (
            capacidad is not None
            and any(
                valor is not None
                for valor in (
                    ocupados_validar,
                    libres_validar,
                    reservados_validar,
                )
            )
            and sum(
                valor or 0
                for valor in (
                    ocupados_validar,
                    libres_validar,
                    reservados_validar,
                )
            )
            > capacidad
        ):
            raise ValueError(
                f'Fila {numero_fila}: los estados de fibras superan la '
                f'capacidad {capacidad} de {ruta_nombre}.'
            )

        distancia = _float_no_negativo(
            _valor_fila(
                row,
                'distancia_documentada_m',
                'distancia',
            ),
            'La distancia documentada',
        )
        ruta_obj.capacidad_hilos_declarada = capacidad
        if {'distancia_documentada_m', 'distancia'} & set(row):
            ruta_obj.distancia_documentada_m = distancia
        for campo in (
            'estado',
            'tipo_fibra',
            'origen',
            'destino',
            'hub_site',
            'marca_modelo',
            'serial',
        ):
            if campo in row:
                setattr(ruta_obj, campo, _texto(row.get(campo)) or None)
        if {'odf_nombre', 'odf_origen'} & set(row):
            ruta_obj.odf_nombre = _texto(
                _valor_fila(row, 'odf_nombre', 'odf_origen')
            ) or None
        if 'mufas' in row:
            ruta_obj.mufas = _entero_no_negativo(
                row.get('mufas'),
                'Las mufas',
                default=0,
            )
        if 'splitters' in row:
            ruta_obj.splitters = _entero_no_negativo(
                row.get('splitters'),
                'Los splitters',
                default=0,
            )
        if 'reservas_m' in row:
            ruta_obj.reservas_m = _float_no_negativo(
                row.get('reservas_m'),
                'La reserva lineal',
                default=0.0,
            )
        if {
            'hilos_ocupados_declarados',
            'hilos_ocupados',
        } & set(row):
            ruta_obj.hilos_ocupados_declarados = ocupados
        if {
            'hilos_libres_declarados',
            'hilos_libres',
        } & set(row):
            ruta_obj.hilos_libres_declarados = libres
        if {
            'hilos_reservados_declarados',
            'hilos_reservados',
        } & set(row):
            ruta_obj.hilos_reservados_declarados = reservados
        if lote is not None:
            ruta_obj.lote_importacion = lote

        odf_origen = ruta_obj.odf_nombre
        if odf_origen:
            odf = InventarioODF.objects.filter(
                odf__iexact=odf_origen
            ).first()
            if odf:
                ruta_obj.odf_origen = odf
        odf_destino = _texto(row.get('odf_destino'))
        if odf_destino:
            odf = InventarioODF.objects.filter(
                odf__iexact=odf_destino
            ).first()
            if odf:
                ruta_obj.odf_destino = odf

        ruta_obj.full_clean()
        ruta_obj.save()
        creadas += int(creada)
        actualizadas += int(not creada)

    return _resultado(
        len(df),
        creadas=creadas,
        actualizadas=actualizadas,
        rechazadas=rechazadas,
    )


@transaction.atomic
def _procesar_troncales_inventario(file, lote=None):
    """Crea o actualiza el inventario global de troncales sin requerir geometría."""
    return _procesar_troncales_dataframe(
        _leer_csv_normalizado(file),
        lote=lote,
    )


@transaction.atomic
def _procesar_tramos_inventario(file, lote=None):
    """Crea o actualiza tramos técnicos; nunca crea ni modifica coordenadas."""
    df = _leer_csv_normalizado(file)
    columnas_ruta = [
        columna
        for columna in ('ruta', 'troncal', 'ident')
        if columna in df.columns
    ]
    if not columnas_ruta:
        raise ValueError('Falta la columna obligatoria ruta.')

    segmentacion_explicita = (
        'tramo_secuencia' in df.columns
        or 'codigo_tramo' in df.columns
    )
    if not segmentacion_explicita:
        resultado = _procesar_troncales_dataframe(df, lote=lote)
        resultado['formato_heredado'] = True
        return resultado

    creadas = actualizadas = rechazadas = 0
    columna_ruta = columnas_ruta[0]
    for ruta_raw, filas_ruta in df.groupby(columna_ruta, sort=False):
        ruta_nombre = _texto(ruta_raw)
        if not ruta_nombre:
            rechazadas += len(filas_ruta)
            continue
        ruta_obj = Ruta.objects.filter(nombre__iexact=ruta_nombre).first()
        if ruta_obj is None:
            raise ValueError(
                f'La ruta {ruta_nombre} no existe en el inventario. '
                'Carga primero el inventario de troncales.'
            )

        filas = filas_ruta.to_dict('records')
        preparados = []
        secuencias = set()
        codigos = set()
        for indice, row in enumerate(filas, start=2):
            secuencia = _entero(row.get('tramo_secuencia'))
            if not secuencia or secuencia < 1:
                raise ValueError(
                    f'{ruta_nombre}, fila {indice}: tramo_secuencia inválida.'
                )
            codigo = _codigo_tramo(row.get('codigo_tramo'), secuencia)
            if secuencia in secuencias:
                raise ValueError(
                    f'{ruta_nombre}: la secuencia {secuencia} está duplicada.'
                )
            if codigo in codigos:
                raise ValueError(
                    f'{ruta_nombre}: el código {codigo} está duplicado.'
                )
            secuencias.add(secuencia)
            codigos.add(codigo)
            preparados.append((row, secuencia, codigo))

        if sorted(secuencias) != list(range(1, max(secuencias) + 1)):
            raise ValueError(
                f'{ruta_nombre}: las secuencias deben comenzar en 1 y no tener saltos.'
            )

        existentes = list(
            InventarioTramo.objects.filter(ruta=ruta_obj).order_by(
                'tramo_secuencia',
                'id',
            )
        )
        por_codigo = {tramo.codigo_tramo: tramo for tramo in existentes}
        por_secuencia = {
            tramo.tramo_secuencia: tramo
            for tramo in existentes
        }
        for row, secuencia, codigo in preparados:
            por_codigo_obj = por_codigo.get(codigo)
            por_secuencia_obj = por_secuencia.get(secuencia)
            if (
                por_codigo_obj
                and por_secuencia_obj
                and por_codigo_obj.pk != por_secuencia_obj.pk
            ):
                raise ValueError(
                    f'{ruta_nombre}: {codigo} y la secuencia {secuencia} '
                    'corresponden a tramos diferentes.'
                )
            tramo = por_codigo_obj or por_secuencia_obj
            creada = tramo is None
            if creada:
                tramo = InventarioTramo(
                    ruta=ruta_obj,
                    tramo_secuencia=secuencia,
                    codigo_tramo=codigo,
                    tipo_trazado='SIN_CLASIFICAR',
                )
            else:
                tramo.tramo_secuencia = secuencia
                tramo.codigo_tramo = codigo

            if 'origen' in row:
                tramo.origen = _texto(row.get('origen')) or None
            if 'destino' in row:
                tramo.destino = _texto(row.get('destino')) or None
            if 'tipo_trazado' in row and _texto(row.get('tipo_trazado')):
                tramo.tipo_trazado = _normalizar_tipo_trazado(
                    row.get('tipo_trazado')
                )
            if {'estado_tramo', 'estado'} & set(row):
                tramo.estado = _texto(
                    _valor_fila(row, 'estado_tramo', 'estado')
                ) or None
            if {
                'distancia_documentada_m',
                'distancia',
            } & set(row):
                tramo.distancia_documentada_m = _float_no_negativo(
                    _valor_fila(
                        row,
                        'distancia_documentada_m',
                        'distancia',
                    ),
                    'La distancia documentada del tramo',
                )
            tramo.estado_calidad = InventarioTramo.CALIDAD_VALIDADO
            tramo.vigente = True
            if lote is not None:
                tramo.lote_importacion = lote
            tramo.full_clean()
            tramo.save()
            CoordenadaRuta.objects.filter(tramo=tramo).update(
                tramo_secuencia=secuencia,
            )
            CoordenadaRuta.objects.filter(
                ruta=ruta_obj,
                tramo_secuencia=secuencia,
                tramo__isnull=True,
            ).update(tramo=tramo)
            por_codigo[codigo] = tramo
            por_secuencia[secuencia] = tramo
            creadas += int(creada)
            actualizadas += int(not creada)

    return _resultado(
        len(df),
        creadas=creadas,
        actualizadas=actualizadas,
        rechazadas=rechazadas,
    )

@transaction.atomic
def _procesar_fibras_inventario(file, lote=None):
    """Carga el detalle individual de fibras/hilos por ruta desde un CSV (Archivo C)"""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))

    rutas_actualizadas = set()
    creadas = actualizadas = rechazadas = 0
    for row in df.to_dict('records'):
        ruta_nombre = _texto(row.get('Ruta'))
        if not ruta_nombre:
            rechazadas += 1
            continue
            
        ruta_obj = Ruta.objects.filter(nombre__iexact=ruta_nombre).first()
        if ruta_obj:
            estado_raw = _texto(row.get('Estado')).capitalize()
            if estado_raw in ['Ocupada', 'Ocupado', 'Active', 'Activo']:
                estado = 'Ocupado'
            elif estado_raw in ['Reservada', 'Reservado']:
                estado = 'Reservado'
            else:
                estado = 'Libre'
            
            fibra_datos = {
                'estado': estado,
                'nombre_fibra': _texto(row.get('Fibra')) if estado in ['Ocupado', 'Reservado'] else 'Libre',
                'origen_odf': _texto(row.get('Origen')) or None,
                'destino': _texto(row.get('Destino')) or None,
                'tipo_conector': _texto(row.get('Conector') if 'Conector' in row else row.get('Tipo de conector', row.get('Tipo conector'))),
                'lote_importacion': lote,
            }
            
            fibra_numero = _texto(row.get('Fibra')).upper()
            if not fibra_numero:
                rechazadas += 1
                continue
            
            fibra_obj = InventarioFibra.objects.filter(
                ruta=ruta_obj,
                fibra_numero__iexact=fibra_numero,
            ).first()
            creada = fibra_obj is None
            if creada:
                InventarioFibra.objects.create(
                    ruta=ruta_obj,
                    fibra_numero=fibra_numero,
                    **fibra_datos,
                )
            else:
                for campo, valor in fibra_datos.items():
                    setattr(fibra_obj, campo, valor)
                fibra_obj.fibra_numero = fibra_numero
                fibra_obj.save()
            creadas += int(creada)
            actualizadas += int(not creada)
            rutas_actualizadas.add(ruta_obj.id)
        else:
            rechazadas += 1
            
    # Recalcular conteo de hilos (libres/ocupados) para las rutas modificadas
    from ..models import InventarioTramo
    for ruta_id in rutas_actualizadas:
        fibras_ruta = InventarioFibra.objects.filter(ruta_id=ruta_id)
        ocupados = fibras_ruta.filter(
            estado__in=['Ocupado', 'Active', 'Activo']
        ).count()
        libres = fibras_ruta.filter(estado='Libre').count()
        reservados = fibras_ruta.filter(
            estado__in=['Reservado', 'Reservada']
        ).count()
        ruta_obj = Ruta.objects.get(pk=ruta_id)
        total_fibras = fibras_ruta.count()
        if (
            ruta_obj.capacidad_hilos_declarada is not None
            and total_fibras > ruta_obj.capacidad_hilos_declarada
        ):
            raise ValueError(
                f'La ruta {ruta_obj.nombre} declara '
                f'{ruta_obj.capacidad_hilos_declarada} hilos, pero existen '
                f'{total_fibras} fibras cargadas.'
            )
        ruta_obj.hilos_ocupados_declarados = ocupados
        ruta_obj.hilos_libres_declarados = libres
        ruta_obj.hilos_reservados_declarados = reservados
        ruta_obj.save(update_fields=[
            'hilos_ocupados_declarados',
            'hilos_libres_declarados',
            'hilos_reservados_declarados',
        ])

    return _resultado(len(df), creadas, actualizadas, rechazadas)

@transaction.atomic
def _procesar_odfs_inventario(file, lote=None):
    """Carga la información general de ODFs desde un CSV (Archivo D)"""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))
    from ..services.inventario import guardar_odf_normalizado, limpiar_texto

    creadas = actualizadas = rechazadas = 0
    for row in df.to_dict('records'):
        odf_nombre = limpiar_texto(row.get('odf'))
        hub_site = limpiar_texto(row.get('hub_site'))
        sala = limpiar_texto(row.get('sala'))
        rack = limpiar_texto(row.get('rack'))
        if not odf_nombre or not hub_site or not sala or not rack:
            raise ValueError(f"ODF con ubicación incompleta: {odf_nombre or '[sin nombre]'}")
            
        odf_datos = {
            'capacidad_puertos': row.get('capacidad_puertos', 0) if pd.notna(row.get('capacidad_puertos')) else 0,
            'tipo_conector': row.get('tipo_conector'),
            'estado': row.get('estado'),
            'observaciones': row.get('observaciones'),
        }
        
        _, creada = guardar_odf_normalizado(
            odf_nombre=odf_nombre,
            hub_nombre=hub_site,
            sala_nombre=sala,
            rack_nombre=rack,
            defaults=odf_datos,
            lote=lote,
        )
        creadas += int(creada)
        actualizadas += int(not creada)

    return _resultado(len(df), creadas, actualizadas, rechazadas)

@transaction.atomic
def _procesar_puertos_odf_inventario(file, lote=None):
    """Carga el detalle granular de puertos de ODF desde un CSV (Archivo E)"""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))
    from ..models import (
        DetallePuertoODF, InventarioODF,
        normalizar_estado_puerto_odf_con_destino,
    )

    requeridas = {'hub_site', 'odf', 'puerto_odf'}
    faltantes = requeridas - set(df.columns)
    if faltantes:
        raise ValueError(f"Faltan columnas obligatorias: {', '.join(sorted(faltantes))}")

    def limpio(valor, default=''):
        if pd.isna(valor):
            return default
        texto = str(valor).strip()
        return default if texto.lower() in {'nan', 'none'} else texto

    odfs = {
        (obj.hub_site.strip().casefold(), obj.odf.strip().casefold()): obj
        for obj in InventarioODF.objects.all()
    }
    filas = []
    claves_archivo = set()
    odf_ids = set()
    for numero_fila, row in enumerate(df.to_dict('records'), start=2):
        hub_site = limpio(row.get('hub_site'))
        odf_nombre = limpio(row.get('odf'))
        puerto_num = limpio(row.get('puerto_odf'))
        if not all((hub_site, odf_nombre, puerto_num)):
            raise ValueError(f'Fila {numero_fila}: puerto ODF con campos obligatorios vacíos.')
        odf_obj = odfs.get((hub_site.casefold(), odf_nombre.casefold()))
        if not odf_obj:
            raise ValueError(
                f"Fila {numero_fila}: ODF inexistente {hub_site} / {odf_nombre}."
            )
        clave = (odf_obj.pk, puerto_num.casefold())
        if clave in claves_archivo:
            raise ValueError(f'Fila {numero_fila}: puerto duplicado {odf_nombre} / {puerto_num}.')
        claves_archivo.add(clave)
        odf_ids.add(odf_obj.pk)
        estado_original = limpio(row.get('estado_puerto'), 'Libre')
        destino = limpio(row.get('destino'))
        estado = normalizar_estado_puerto_odf_con_destino(
            estado_original,
            destino,
            default=None,
        )
        if not estado:
            raise ValueError(f'Fila {numero_fila}: estado de puerto inválido: {estado_original}.')
        filas.append((odf_obj, puerto_num, {
            'odf': odf_nombre,
            'bandeja': limpio(row.get('bandeja')),
            'fibra': limpio(row.get('fibra')),
            'estado_puerto': estado,
            'tipo_conector': limpio(row.get('tipo_conector')),
            'patchcord': limpio(row.get('patchcord')),
            'destino': destino,
            'observaciones': limpio(row.get('observaciones')),
            'lote_importacion': lote,
        }))

    conteos_archivo = {}
    for odf_obj, _, _ in filas:
        conteos_archivo[odf_obj.pk] = conteos_archivo.get(odf_obj.pk, 0) + 1
    for odf_id, cantidad_archivo in conteos_archivo.items():
        odf_obj = next(obj for obj in odfs.values() if obj.pk == odf_id)
        if odf_obj.capacidad_puertos and cantidad_archivo > odf_obj.capacidad_puertos:
            raise ValueError(
                f'El ODF {odf_obj.odf} declara capacidad {odf_obj.capacidad_puertos}, '
                f'pero el archivo contiene {cantidad_archivo} puertos.'
            )

    existentes = {
        (obj.odf_obj_id, obj.puerto_odf.casefold()): obj
        for obj in DetallePuertoODF.objects.filter(odf_obj_id__in=odf_ids)
    }
    crear = []
    actualizar = []
    campos = [
        'odf', 'bandeja', 'fibra', 'estado_puerto', 'tipo_conector',
        'patchcord', 'destino', 'observaciones', 'lote_importacion',
    ]
    for odf_obj, puerto_num, valores in filas:
        obj = existentes.get((odf_obj.pk, puerto_num.casefold()))
        if obj:
            for campo, valor in valores.items():
                setattr(obj, campo, valor)
            actualizar.append(obj)
        else:
            crear.append(DetallePuertoODF(
                odf_obj=odf_obj,
                puerto_odf=puerto_num,
                **valores,
            ))

    DetallePuertoODF.objects.bulk_create(crear, batch_size=2000)
    if actualizar:
        # 80 filas mantienen la sentencia bajo el límite de variables de SQLite;
        # PostgreSQL también procesa este tamaño de manera estable.
        DetallePuertoODF.objects.bulk_update(actualizar, campos, batch_size=80)

    from django.db.models import Count
    conteos = {
        (fila['odf_obj_id'], fila['estado_puerto']): fila['total']
        for fila in DetallePuertoODF.objects.filter(odf_obj_id__in=odf_ids)
        .values('odf_obj_id', 'estado_puerto')
        .annotate(total=Count('id'))
    }
    odfs_actualizar = list(InventarioODF.objects.filter(pk__in=odf_ids))
    for odf_obj in odfs_actualizar:
        ocupados = conteos.get((odf_obj.pk, 'Ocupado'), 0)
        reservados = conteos.get((odf_obj.pk, 'Reservado'), 0)
        total_detalle = sum(
            conteos.get((odf_obj.pk, estado), 0)
            for estado in ('Libre', 'Ocupado', 'Reservado')
        )
        capacidad = max(odf_obj.capacidad_puertos or 0, total_detalle)
        odf_obj.capacidad_puertos = capacidad
        odf_obj.puertos_ocupados = ocupados
        odf_obj.puertos_reservados = reservados
        odf_obj.puertos_libres = max(capacidad - ocupados - reservados, 0)
    InventarioODF.objects.bulk_update(
        odfs_actualizar,
        ['capacidad_puertos', 'puertos_ocupados', 'puertos_libres', 'puertos_reservados'],
        batch_size=200,
    )

    return _resultado(len(df), len(crear), len(actualizar))

@transaction.atomic
def _procesar_coordenadas_csv(file, lote=None):
    """
    Vincula geometrías con inventario existente de forma idempotente.

    Nunca crea rutas ni tramos. Acepta la estructura heredada ``new_seg``
    únicamente para localizar tramos ya registrados.
    """
    df = _leer_csv_normalizado(file)
    faltantes = {'ruta', 'latitude', 'longitude'} - set(df.columns)
    if faltantes:
        raise ValueError(
            f"Faltan columnas obligatorias: {', '.join(sorted(faltantes))}"
        )

    rutas_actualizadas = 0
    coordenadas_actualizadas = 0
    for ruta_raw, df_coords in df.groupby('ruta', sort=False):
        ruta_nombre = _texto(ruta_raw)
        if not ruta_nombre:
            raise ValueError('Existe una fila sin nombre de ruta.')
        ruta_obj = Ruta.objects.filter(nombre__iexact=ruta_nombre).first()
        if not ruta_obj:
            raise ValueError(
                f'La ruta {ruta_nombre} no existe en el inventario. '
                'Carga primero las troncales y sus tramos.'
            )
        cantidad, _ = _vincular_geometria_existente(
            ruta_obj, df_coords, lote=lote
        )
        coordenadas_actualizadas += cantidad
        rutas_actualizadas += 1

    resultado = _resultado(len(df), actualizadas=coordenadas_actualizadas)
    resultado['rutas_actualizadas'] = rutas_actualizadas
    return resultado


@transaction.atomic
def _procesar_coordenadas_csv_legacy(file, lote=None):
    """
    Importa coordenadas y segmentación en un solo archivo CSV para múltiples rutas.
    Columnas requeridas: Ruta, Latitude, Longitude
    Columnas opcionales: tipo_trazado, new_seg
    """
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))
    
    # Normalizar nombres de columnas a minúsculas
    df.columns = [c.strip().lower() for c in df.columns]
    
    # Verificar columnas mínimas
    req_cols = ['ruta', 'latitude', 'longitude']
    for col in req_cols:
        if col not in df.columns:
            raise ValueError(f"Falta la columna requerida en el CSV: {col}")
            
    rutas_actualizadas = 0
    coordenadas_creadas = 0
    # Agrupar por ruta para procesar
    for ruta_nombre, df_coords in df.groupby('ruta'):
        ruta_nombre = str(ruta_nombre).strip()
        if not ruta_nombre or ruta_nombre == 'nan':
            continue
            
        ruta_obj = Ruta.objects.filter(nombre=ruta_nombre).first()
        if not ruta_obj:
            ruta_obj = Ruta.objects.create(nombre=ruta_nombre, lote_importacion=lote)
            logger.info(f"Ruta '{ruta_nombre}' creada automáticamente al cargar coordenadas.")
            
        # Respaldar datos manuales previos del primer tramo
        tramo_previo = InventarioTramo.objects.filter(ruta=ruta_obj).order_by('tramo_secuencia').first()
        datos_respaldo = {}
        if tramo_previo:
            datos_respaldo = {
                'estado': tramo_previo.estado,
                'capacidad': tramo_previo.capacidad,
                'hub_site': tramo_previo.hub_site,
                'destino': tramo_previo.destino,
                'marca_modelo': tramo_previo.marca_modelo,
                'tipo_fibra': tramo_previo.tipo_fibra,
                'serial': tramo_previo.serial,
                'mufas': tramo_previo.mufas,
                'splitters': tramo_previo.splitters,
                'odf_nombre': tramo_previo.odf_nombre,
                'hilos_ocupados': tramo_previo.hilos_ocupados,
                'hilos_libres': tramo_previo.hilos_libres,
                'distancia_m': tramo_previo.distancia_m,
                'reservas_m': tramo_previo.reservas_m
            }
            
        # Extraer hub_site del CSV si existe para asignarlo inmediatamente
        if 'hub_site' in df_coords.columns:
            primer_valor = df_coords['hub_site'].dropna().first_valid_index()
            if primer_valor is not None:
                val = str(df_coords.at[primer_valor, 'hub_site']).strip()
                if val and val.lower() != 'nan':
                    datos_respaldo['hub_site'] = val
            
        # Limpiar anteriores de esta ruta
        CoordenadaRuta.objects.filter(ruta=ruta_obj).delete()
        InventarioTramo.objects.filter(ruta=ruta_obj).delete()
        
        coords_a_crear = []
        tramos_data = {}
        
        tramo_actual = 1
        tipo_trazado_actual = None
        
        distancia_total = 0.0
        prev_lat = None
        prev_lon = None
        
        df_coords = df_coords.reset_index(drop=True)
        
        for i, row_coord in df_coords.iterrows():
            new_seg_val = str(row_coord.get('new_seg', 'False')).strip().lower()
            es_new_seg = new_seg_val in ['true', '1']
            
            tipo_trazado_raw = str(row_coord.get('tipo_trazado', '')).strip().upper()
            if not tipo_trazado_raw or tipo_trazado_raw == 'NAN':
                tipo_trazado_raw = 'DESCONOCIDO'
                
            if i == 0:
                tipo_trazado_actual = tipo_trazado_raw
            else:
                if tipo_trazado_raw != tipo_trazado_actual:
                    es_new_seg = True
                    tipo_trazado_actual = tipo_trazado_raw
                    
            if es_new_seg and i > 0:
                tramo_actual += 1
                
            if tramo_actual not in tramos_data:
                tramos_data[tramo_actual] = {
                    'tipo_trazado': tipo_trazado_actual,
                    'distancia_m': 0.0
                }
                
            lat = float(row_coord['latitude'])
            lon = float(row_coord['longitude'])
            
            distancia_segmento = 0.0
            if prev_lat is not None and prev_lon is not None:
                distancia_segmento = calcular_distancia_haversine(prev_lat, prev_lon, lat, lon)
                distancia_total += distancia_segmento
                tramos_data[tramo_actual]['distancia_m'] += distancia_segmento
                
            prev_lat = lat
            prev_lon = lon
                
            coords_a_crear.append(
                CoordenadaRuta(
                    ruta=ruta_obj,
                    latitud=Decimal(str(lat)),
                    longitud=Decimal(str(lon)),
                    orden=i + 1,
                    tramo_secuencia=tramo_actual,
                    lote_importacion=lote,
                )
            )
            
        # Guardar en base de datos
        CoordenadaRuta.objects.bulk_create(coords_a_crear)
        coordenadas_creadas += len(coords_a_crear)
        
        # Guardar distancia total
        ruta_obj.distancia_m = distancia_total
        if lote is not None:
            ruta_obj.lote_importacion = lote
            ruta_obj.save(update_fields=['distancia_m', 'lote_importacion'])
        else:
            ruta_obj.save(update_fields=['distancia_m'])
        
        inventario_a_crear = []
        for num_tramo, meta in tramos_data.items():
            tramo_datos = dict(datos_respaldo)
            if num_tramo != 1:
                tramo_datos.update({
                    'mufas': 0,
                    'splitters': 0,
                    'reservas_m': 0.0,
                    'hilos_ocupados': 0,
                    'hilos_libres': 0,
                })
            tramo_datos['distancia_m'] = meta['distancia_m']
            inventario_a_crear.append(
                InventarioTramo(
                    ruta=ruta_obj,
                    tramo_secuencia=num_tramo,
                    codigo_tramo=f"T{num_tramo:03d}",
                    tipo_trazado=meta['tipo_trazado'],
                    estado_calidad=InventarioTramo.CALIDAD_LEGACY,
                    lote_importacion=lote,
                    **tramo_datos
                )
            )
        InventarioTramo.objects.bulk_create(inventario_a_crear)
        tramos_por_secuencia = {
            tramo.tramo_secuencia: tramo
            for tramo in InventarioTramo.objects.filter(ruta=ruta_obj)
        }
        for secuencia, tramo in tramos_por_secuencia.items():
            CoordenadaRuta.objects.filter(
                ruta=ruta_obj,
                tramo_secuencia=secuencia,
            ).update(tramo=tramo)
        rutas_actualizadas += 1
        
    resultado = _resultado(len(df), coordenadas_creadas)
    resultado['rutas_actualizadas'] = rutas_actualizadas
    return resultado



@login_required
@require_POST
def cargar_csv(request, tipo_csv):
    """Vista que maneja la carga de diferentes archivos y los procesa."""
    PROCESADORES = {
        'reservas': (_procesar_reservas, "Reservas"),
        'ruta_otu': (_procesar_ruta_otu, "Relación Ruta/OTU"),
        'equipos_otu': (_procesar_equipos_otu, "Equipos OTU"),
        'coordenadas_rutas': (_procesar_coordenadas_zip, "Coordenadas de Rutas (ZIP)"),

        'asociar_puertos': (_asociar_puertos_a_rutas, "Asociar Puertos a Rutas"),
        'id_rutas': (_procesar_id_rutas, "Cargar IDs de Rutas (ONMSI)"),
        'coordenadas_inventario': (_procesar_coordenadas_inventario_zip, "Coordenadas Segmentadas (.zip)"),
        'coordenadas_csv': (_procesar_coordenadas_csv, "Geometría de Tramos (.csv)"),
        'troncales_inventario': (_procesar_troncales_inventario, "Inventario de Troncales (.csv)"),
        'tramos_inventario': (_procesar_tramos_inventario, "Inventario de Tramos (.csv)"),
        'fibras_inventario': (_procesar_fibras_inventario, "Detalle de Fibras / Hilos (.csv)"),
        'odf_inventario': (_procesar_odfs_inventario, "Inventario de ODFs (.csv)"),
        'puertos_odf_inventario': (_procesar_puertos_odf_inventario, "Detalle de Puertos ODF (.csv)"),
    }

    if tipo_csv not in PROCESADORES:
        messages.error(request, f"Tipo de archivo '{tipo_csv}' no es válido.")
        return redirect('configuracion')

    permisos_por_tipo = {
        'reservas': ('mapas.add_reserva', 'mapas.change_reserva'),
        'ruta_otu': ('mapas.add_ruta', 'mapas.change_ruta', 'mapas.add_otu'),
        'equipos_otu': ('mapas.add_otu', 'mapas.change_otu', 'mapas.add_puertootu'),
        'coordenadas_rutas': ('mapas.add_coordenadaruta', 'mapas.change_coordenadaruta'),
        'asociar_puertos': ('mapas.change_puertootu',),
        'id_rutas': ('mapas.add_idruta', 'mapas.change_idruta'),
        'coordenadas_inventario': ('mapas.add_coordenadaruta', 'mapas.change_coordenadaruta'),
        'coordenadas_csv': ('mapas.add_coordenadaruta', 'mapas.change_coordenadaruta'),
        'troncales_inventario': ('mapas.add_ruta', 'mapas.change_ruta'),
        'tramos_inventario': ('mapas.add_inventariotramo', 'mapas.change_inventariotramo'),
        'fibras_inventario': ('mapas.add_inventariofibra', 'mapas.change_inventariofibra'),
        'odf_inventario': ('mapas.add_inventarioodf', 'mapas.change_inventarioodf'),
        'puertos_odf_inventario': (
            'mapas.add_detallepuertoodf',
            'mapas.change_detallepuertoodf',
        ),
    }
    if not request.user.has_perms(permisos_por_tipo[tipo_csv]):
        raise PermissionDenied

    procesador_func, nombre_amigable = PROCESADORES[tipo_csv]

    if request.method == 'POST':
        if 'csv_file' in request.FILES:
            archivo_subido = request.FILES['csv_file']

            extension_valida = '.zip' if tipo_csv in ['coordenadas_rutas', 'coordenadas_inventario'] else '.csv'
            if not archivo_subido.name.casefold().endswith(extension_valida):
                messages.error(request, f"El archivo debe ser de formato {extension_valida}.")
                return redirect('configuracion')

            try:
                _validar_archivo_subido(
                    archivo_subido,
                    es_zip=extension_valida == '.zip',
                )
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect('configuracion')

            digest = hashlib.sha256()
            for chunk in archivo_subido.chunks():
                digest.update(chunk)
            archivo_subido.seek(0)
            lote = LoteImportacion.objects.create(
                tipo=tipo_csv,
                archivo_origen=os.path.basename(archivo_subido.name),
                hash_sha256=digest.hexdigest(),
                estado='PROCESANDO',
                usuario=request.user,
            )

            try:
                kwargs_procesador = {'lote': lote}
                resultado = procesador_func(archivo_subido, **kwargs_procesador)
                if not isinstance(resultado, dict):
                    resultado = _resultado()
                filas_rechazadas = int(resultado.get('rechazadas', 0) or 0)
                notificar = messages.warning if filas_rechazadas else messages.success

                if tipo_csv == 'asociar_puertos':
                    notificar(
                        request,
                        f"Proceso completado. Se asociaron {resultado['actualizadas']} puertos a sus rutas."
                    )
                elif tipo_csv == 'coordenadas_csv':
                    notificar(
                        request,
                        'Carga unificada exitosa. Se actualizaron las coordenadas de '
                        f"{resultado.get('rutas_actualizadas', 0)} rutas."
                    )
                elif tipo_csv == 'coordenadas_rutas_csv':
                    notificar(
                        request,
                        'Carga de trazado unificado exitosa. Se actualizaron las coordenadas de '
                        f"{resultado.get('rutas_actualizadas', 0)} rutas."
                    )
                else:
                    mensaje = f"Los datos de '{nombre_amigable}' han sido actualizados correctamente."
                    if filas_rechazadas:
                        mensaje += f' {filas_rechazadas} filas fueron rechazadas y requieren revisión.'
                    notificar(request, mensaje)

                lote.estado = 'COMPLETADO_CON_ERRORES' if filas_rechazadas else 'COMPLETADO'
                lote.total_filas = resultado['total']
                lote.filas_creadas = resultado['creadas']
                lote.filas_actualizadas = resultado['actualizadas']
                lote.filas_rechazadas = filas_rechazadas
                lote.finalizado_en = now()
                lote.save(update_fields=[
                    'estado', 'total_filas', 'filas_creadas', 'filas_actualizadas',
                    'filas_rechazadas', 'finalizado_en',
                ])

                return redirect('configuracion')
            except Exception as e:
                logger.exception("Error inesperado al procesar la carga %s", tipo_csv)
                lote.estado = 'FALLIDO'
                lote.detalle_errores = [str(e)[:1000]]
                lote.finalizado_en = now()
                lote.save(update_fields=['estado', 'detalle_errores', 'finalizado_en'])
                messages.error(request, "No se pudo procesar el archivo. Consulte el detalle del lote.")
                return redirect('configuracion')
        else:
            messages.error(request, "No se adjuntó ningún archivo.")
            return redirect('configuracion')

    return redirect('configuracion')
