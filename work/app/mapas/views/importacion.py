"""
Vistas de importación de datos CSV y configuración del sistema.
"""
import os
import io
import hashlib
import logging
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
from django.utils.timezone import now
from django.views.decorators.http import require_POST
import math
from ..models import (
    OTU, Ruta, PuertoOTU, CoordenadaRuta, Reserva, IDRuta,
    InventarioTramo, InventarioFibra, LoteImportacion,
)

logger = logging.getLogger('mapas')

VALORES_VACIOS = {'', '-', 'nan', 'none', 'null', 'sin_dato', 'no_aplica', 'n/a'}


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
    except Exception as e:
        logger.error(f"Error al guardar el archivo en {destination_path}: {e}")
        return False


@login_required
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
    """Para cada ruta en el ZIP, reemplaza sus coordenadas."""
    total = creadas = rechazadas = 0
    with zipfile.ZipFile(file, 'r') as zip_ref:
        for filename in zip_ref.namelist():
            if filename.endswith('.csv'):
                ruta_nombre = os.path.splitext(os.path.basename(filename))[0]
                ruta_obj = Ruta.objects.filter(nombre=ruta_nombre).first()

                if ruta_obj:
                    CoordenadaRuta.objects.filter(ruta=ruta_obj).delete()
                    with zip_ref.open(filename) as csv_file:
                        csv_content = csv_file.read().decode('utf-8')
                        df_coords = pd.read_csv(io.StringIO(csv_content))
                        coords_a_crear = []
                        distancia_total = 0.0
                        prev_lat = None
                        prev_lon = None

                        for i, row_coord in df_coords.iterrows():
                            lat = float(row_coord['Latitude'])
                            lon = float(row_coord['Longitude'])
                            
                            if prev_lat is not None and prev_lon is not None:
                                distancia_total += calcular_distancia_haversine(prev_lat, prev_lon, lat, lon)
                                
                            prev_lat = lat
                            prev_lon = lon

                            coords_a_crear.append(
                                CoordenadaRuta(
                                    ruta=ruta_obj,
                                    latitud=Decimal(str(lat)),
                                    longitud=Decimal(str(lon)),
                                    orden=i + 1,
                                    lote_importacion=lote,
                                )
                            )
                        CoordenadaRuta.objects.bulk_create(coords_a_crear)
                        total += len(df_coords)
                        creadas += len(coords_a_crear)
                        ruta_obj.distancia_m = distancia_total
                        if lote is not None:
                            ruta_obj.lote_importacion = lote
                            ruta_obj.save(update_fields=['distancia_m', 'lote_importacion'])
                        else:
                            ruta_obj.save(update_fields=['distancia_m'])
                else:
                    rechazadas += 1

    return _resultado(total + rechazadas, creadas, rechazadas=rechazadas)


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



@transaction.atomic
def _procesar_coordenadas_inventario_zip(file, lote=None):
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
                                    tipo_trazado=meta['tipo_trazado'],
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

@transaction.atomic
def _procesar_tramos_inventario(file, lote=None):
    """Carga metadatos de troncal sin sobrescribir la distancia de cada segmento."""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))

    actualizadas = rechazadas = 0
    for row in df.to_dict('records'):
        # Aceptar columna "ruta" o "ident"
        ruta_nombre_raw = row.get('ruta') if 'ruta' in row else row.get('ident')
        ruta_nombre = _texto(ruta_nombre_raw)
        if not ruta_nombre:
            rechazadas += 1
            continue
        ruta_obj = Ruta.objects.filter(nombre__iexact=ruta_nombre).first()
        
        if ruta_obj:
            distancia_total = _float(row.get('distancia'))
            if distancia_total is not None:
                ruta_obj.distancia_m = distancia_total
            if lote is not None:
                ruta_obj.lote_importacion = lote

            inventario_datos = {
                'estado': _texto(row.get('estado')) or None,
                'capacidad': _texto(row.get('capacidad')) or None,
                'tipo_fibra': _texto(row.get('tipo_fibra')) or None,
                'origen': _texto(row.get('origen')) or None,
                'destino': _texto(row.get('destino')) or None,
                'hub_site': _texto(row.get('hub_site')) or None,
                'marca_modelo': _texto(row.get('marca_modelo')) or None,
                'serial': _texto(row.get('serial')) or None,
                'odf_nombre': _texto(row.get('odf_nombre')) or None,
                'lote_importacion': lote,
            }
            
            cap = inventario_datos.get('capacidad')
            if cap and cap.isdigit():
                inventario_datos['capacidad'] = f"{cap} Hilos"

            # Los datos descriptivos pueden repetirse; las cantidades globales se guardan
            # una sola vez para que no se multipliquen al sumar segmentos.
            InventarioTramo.objects.filter(ruta=ruta_obj).update(**inventario_datos)
            primer_tramo = InventarioTramo.objects.filter(ruta=ruta_obj).order_by('tramo_secuencia').first()
            if primer_tramo:
                InventarioTramo.objects.filter(ruta=ruta_obj).exclude(pk=primer_tramo.pk).update(
                    mufas=0,
                    splitters=0,
                    reservas_m=0.0,
                    hilos_ocupados=0,
                    hilos_libres=0,
                )
                primer_tramo.mufas = _entero(row.get('mufas'), 0)
                primer_tramo.splitters = _entero(row.get('splitters'), 0)
                primer_tramo.reservas_m = _float(row.get('reservas_m'), 0.0)
                primer_tramo.hilos_ocupados = _entero(row.get('hilos_ocupados'), 0)
                primer_tramo.hilos_libres = _entero(row.get('hilos_libres'), 0)
                primer_tramo.save(update_fields=[
                    'mufas', 'splitters', 'reservas_m',
                    'hilos_ocupados', 'hilos_libres',
                ])

            odf_nombre = inventario_datos['odf_nombre']
            if odf_nombre and ruta_obj.odf_origen_id is None:
                from ..models import InventarioODF
                odf = InventarioODF.objects.filter(odf__iexact=odf_nombre).first()
                if odf:
                    ruta_obj.odf_origen = odf
            ruta_obj.save()
            actualizadas += 1
        else:
            rechazadas += 1

    return _resultado(len(df), actualizadas=actualizadas, rechazadas=rechazadas)

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
        ocupados = InventarioFibra.objects.filter(ruta_id=ruta_id, estado__in=['Ocupado', 'Active', 'Activo']).count()
        libres = InventarioFibra.objects.filter(ruta_id=ruta_id, estado='Libre').count()
        tramos = InventarioTramo.objects.filter(ruta_id=ruta_id).order_by('tramo_secuencia')
        primer_tramo = tramos.first()
        tramos.update(hilos_ocupados=0, hilos_libres=0)
        if primer_tramo:
            InventarioTramo.objects.filter(pk=primer_tramo.pk).update(
                hilos_ocupados=ocupados,
                hilos_libres=libres,
            )

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
        DetallePuertoODF, InventarioODF, normalizar_estado_puerto_odf,
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
        estado = normalizar_estado_puerto_odf(estado_original, default=None)
        if not estado:
            raise ValueError(f'Fila {numero_fila}: estado de puerto inválido: {estado_original}.')
        filas.append((odf_obj, puerto_num, {
            'odf': odf_nombre,
            'bandeja': limpio(row.get('bandeja')),
            'fibra': limpio(row.get('fibra')),
            'estado_puerto': estado,
            'tipo_conector': limpio(row.get('tipo_conector')),
            'patchcord': limpio(row.get('patchcord')),
            'destino': limpio(row.get('destino')),
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
                    tipo_trazado=meta['tipo_trazado'],
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
        'coordenadas_csv': (_procesar_coordenadas_csv, "Cargar Coordenadas e Inventario Unificado (.csv)"),
        'tramos_inventario': (_procesar_tramos_inventario, "Inventario Técnico Tramos (.csv)"),
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
                lote.estado = 'FALLIDO'
                lote.detalle_errores = [str(e)[:1000]]
                lote.finalizado_en = now()
                lote.save(update_fields=['estado', 'detalle_errores', 'finalizado_en'])
                messages.error(request, f"Ocurrió un error al procesar el archivo: {e}")
                return redirect('configuracion')
        else:
            messages.error(request, "No se adjuntó ningún archivo.")
            return redirect('configuracion')

    return redirect('configuracion')
