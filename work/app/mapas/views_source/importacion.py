"""
Vistas de importación de datos CSV y configuración del sistema.
"""
import os
import io
import logging
import zipfile

import pandas as pd
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.db import transaction
import math
from ..models import OTU, Ruta, PuertoOTU, CoordenadaRuta, Reserva, IDRuta, InventarioTramo, InventarioFibra

logger = logging.getLogger('mapas')

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
    return render(request, 'configuracion/configuracion_index.html', {'rutas': rutas})


@login_required
@permission_required('mapas.delete_ruta', raise_exception=True)
def eliminar_ruta_config(request):
    """Permite a los superusuarios eliminar una ruta específica y toda su información relacionada."""
    if request.method == 'POST' and request.user.is_superuser:
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
def _procesar_equipos_otu(file):
    """Actualiza o crea OTUs y Puertos desde un CSV."""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))

    for _, row in df.iterrows():
        otu_identificador = {'nombre': row['Descripcion'].strip()}
        otu_datos = {
            'modelo': row['Modelo'],
            'latitud': row['Latitude'],
            'longitud': row['Longitude']
        }
        otu_obj, created = OTU.objects.update_or_create(
            defaults=otu_datos,
            **otu_identificador
        )

        puerto_identificadores = {
            'otu': otu_obj,
            'numero': row['Puerto']
        }
        puerto_datos = {
            'estado': row['Estado']
        }
        PuertoOTU.objects.update_or_create(
            defaults=puerto_datos,
            **puerto_identificadores
        )


@transaction.atomic
def _asociar_puertos_a_rutas(file):
    """Lee el CSV de equipos y actualiza los puertos existentes para asociarlos a las rutas."""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))

    PuertoOTU.objects.all().update(ruta_asociada=None)

    puertos_actualizados = 0
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
                    puerto.save()
                    puertos_actualizados += 1
            except PuertoOTU.DoesNotExist:
                logger.warning(f"Puerto {row['Puerto']} del OTU {row['Descripcion']} no encontrado en BD.")
                pass

    return puertos_actualizados


@transaction.atomic
def _procesar_ruta_otu(file):
    """Actualiza o crea Rutas desde un CSV."""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))

    for _, row in df.iterrows():
        otu_obj = OTU.objects.filter(nombre=row['OTU'].strip()).first()
        ruta_identificador = {'nombre': row['Ruta'].strip()}
        ruta_datos = {
            'otu': otu_obj,
            'distancia_m': row.get('Distancia (m)'),
            'olt': row.get('OLT'),
            'slot_olt': row.get('SLOT OLT'),
            'puerto_olt': row.get('PUERTO OLT'),
            'pon': row.get('PON'),
            'enlace': row.get('Enlace')
        }
        Ruta.objects.update_or_create(
            defaults=ruta_datos,
            **ruta_identificador
        )


@transaction.atomic
def _procesar_coordenadas_zip(file):
    """Para cada ruta en el ZIP, reemplaza sus coordenadas."""
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
                                    latitud=lat,
                                    longitud=lon,
                                    orden=i + 1
                                )
                            )
                        CoordenadaRuta.objects.bulk_create(coords_a_crear)
                        ruta_obj.distancia_m = distancia_total
                        ruta_obj.save(update_fields=['distancia_m'])


@transaction.atomic
def _procesar_reservas(file):
    """Actualiza o crea Reservas desde un CSV."""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))

    for _, row in df.iterrows():
        ruta_obj = Ruta.objects.filter(nombre=row.get('Enlace', '').strip()).first()
        if ruta_obj:
            nombre = str(row.get('Landmark name', row.get('Nombre', ''))).strip()
            tipo = str(row.get('Connection type', row.get('Tipo', ''))).strip()
            reserva_m = row.get('Reserva (m)', row.get('Reserva_m', 0))
            lat = row.get('Latitud', 0)
            lon = row.get('Longitud', 0)
            
            # Limpiar NaNs
            if pd.isna(reserva_m) or str(reserva_m).strip() == '-': reserva_m = 0
            if pd.isna(lat) or str(lat).strip() == '-': lat = 0
            if pd.isna(lon) or str(lon).strip() == '-': lon = 0
            if pd.isna(tipo) or tipo == 'nan': tipo = ''

            reserva_identificadores = {
                'ruta': ruta_obj,
                'nombre': nombre
            }
            reserva_datos = {
                'tipo': tipo,
                'reserva_m': float(reserva_m),
                'latitud': float(lat),
                'longitud': float(lon),
            }
            Reserva.objects.update_or_create(
                defaults=reserva_datos,
                **reserva_identificadores
            )


@transaction.atomic
def _procesar_id_rutas(file):
    """Carga el archivo CSV de IDs de Rutas."""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))

    for _, row in df.iterrows():
        ruta_nombre = row['Ruta'].strip()
        IDRuta.objects.update_or_create(
            ruta=ruta_nombre,
            defaults={
                'id_onmsi': row['ID']
            }
        )



@transaction.atomic
def _procesar_coordenadas_inventario_zip(file):
    """
    Importa coordenadas y metadatos de inventario simultáneamente.
    Se crea un nuevo "tramo" si `new_seg` es True o si el `tipo_trazado` cambia.
    """
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
                                    latitud=lat,
                                    longitud=lon,
                                    orden=i + 1,
                                    tramo_secuencia=tramo_actual
                                )
                            )
                            
                        # Guardamos Coordenadas
                        CoordenadaRuta.objects.bulk_create(coords_a_crear)
                        
                        # Actualizar distancia de la ruta
                        ruta_obj.distancia_m = distancia_total
                        ruta_obj.save(update_fields=['distancia_m'])
                        
                        # Guardamos InventarioTramos (solo inicial con tipo_trazado)
                        inventario_a_crear = []
                        for num_tramo, meta in tramos_data.items():
                            tramo_datos = dict(datos_respaldo)
                            tramo_datos['distancia_m'] = meta['distancia_m']
                            inventario_a_crear.append(
                                InventarioTramo(
                                    ruta=ruta_obj,
                                    tramo_secuencia=num_tramo,
                                    tipo_trazado=meta['tipo_trazado'],
                                    **tramo_datos
                                )
                            )
                        InventarioTramo.objects.bulk_create(inventario_a_crear)

@transaction.atomic
def _procesar_tramos_inventario(file):
    """Carga los metadatos suplementarios de la Ruta (aplica a todos sus tramos) desde el CSV"""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))

    for _, row in df.iterrows():
        # Aceptar columna "ruta" o "ident"
        ruta_nombre_raw = row.get('ruta') if 'ruta' in row else row.get('ident')
        if pd.isna(ruta_nombre_raw):
            continue
        ruta_nombre = str(ruta_nombre_raw).strip()
        ruta_obj = Ruta.objects.filter(nombre=ruta_nombre).first()
        
        if ruta_obj:
            inventario_datos = {
                'estado': row.get('estado') if pd.notna(row.get('estado')) else None,
                'distancia_m': row.get('distancia') if pd.notna(row.get('distancia')) else None,
                'mufas': row.get('mufas', 0) if pd.notna(row.get('mufas')) else 0,
                'splitters': row.get('splitters', 0) if pd.notna(row.get('splitters')) else 0,
                'reservas_m': row.get('reservas_m', 0.0) if pd.notna(row.get('reservas_m')) else 0.0,
                'capacidad': row.get('capacidad') if pd.notna(row.get('capacidad')) else None,
                'tipo_fibra': row.get('tipo_fibra') if pd.notna(row.get('tipo_fibra')) else None,
                'origen': row.get('origen') if 'origen' in row and pd.notna(row['origen']) else None,
                'destino': row.get('destino') if 'destino' in row and pd.notna(row['destino']) else None,
                'hub_site': row.get('hub_site') if 'hub_site' in row and pd.notna(row['hub_site']) else None,
                'marca_modelo': row.get('marca_modelo') if 'marca_modelo' in row and pd.notna(row['marca_modelo']) else None,
                'serial': row.get('serial') if 'serial' in row and pd.notna(row['serial']) else None,
                'odf_nombre': row.get('odf_nombre') if 'odf_nombre' in row and pd.notna(row['odf_nombre']) else None,
                'hilos_ocupados': row.get('hilos_ocupados', 0) if pd.notna(row.get('hilos_ocupados')) else 0,
                'hilos_libres': row.get('hilos_libres', 0) if pd.notna(row.get('hilos_libres')) else 0,
            }
            
            cap = inventario_datos.get('capacidad')
            if cap and str(cap).isdigit():
                inventario_datos['capacidad'] = f"{cap} Hilos"
            # Actualizamos todos los tramos (segmentos) de esta ruta con su metadata general
            InventarioTramo.objects.filter(ruta=ruta_obj).update(**inventario_datos)

@transaction.atomic
def _procesar_fibras_inventario(file):
    """Carga el detalle individual de fibras/hilos por ruta desde un CSV (Archivo C)"""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))

    rutas_actualizadas = set()
    for _, row in df.iterrows():
        ruta_nombre = str(row.get('Ruta', '')).strip()
        if not ruta_nombre:
            continue
            
        ruta_obj = Ruta.objects.filter(nombre=ruta_nombre).first()
        if ruta_obj:
            estado_raw = str(row.get('Estado', '')).strip().capitalize()
            if estado_raw in ['Ocupada', 'Ocupado', 'Active', 'Activo']:
                estado = 'Ocupado'
            elif estado_raw in ['Reservada', 'Reservado']:
                estado = 'Reservado'
            else:
                estado = 'Libre'
            
            fibra_datos = {
                'estado': estado,
                'nombre_fibra': row.get('Fibra') if estado in ['Ocupado', 'Reservado'] else 'Libre',
                'origen_odf': row.get('Origen'),
                'destino': row.get('Destino'),
                'tipo_conector': row.get('Conector') if 'Conector' in row else row.get('Tipo de conector', row.get('Tipo conector', '')),
            }
            
            fibra_numero = str(row.get('Fibra', '')).strip()
            
            InventarioFibra.objects.update_or_create(
                ruta=ruta_obj,
                fibra_numero=fibra_numero,
                defaults=fibra_datos
            )
            rutas_actualizadas.add(ruta_obj.id)
            
    # Recalcular conteo de hilos (libres/ocupados) para las rutas modificadas
    from ..models import InventarioTramo
    for ruta_id in rutas_actualizadas:
        ocupados = InventarioFibra.objects.filter(ruta_id=ruta_id, estado__in=['Ocupado', 'Active', 'Activo']).count()
        libres = InventarioFibra.objects.filter(ruta_id=ruta_id, estado='Libre').count()
        InventarioTramo.objects.filter(ruta_id=ruta_id).update(
            hilos_ocupados=ocupados,
            hilos_libres=libres
        )

@transaction.atomic
def _procesar_odfs_inventario(file):
    """Carga la información general de ODFs desde un CSV (Archivo D)"""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))
    from ..models import InventarioODF

    for _, row in df.iterrows():
        odf_nombre = str(row.get('odf', '')).strip()
        hub_site = str(row.get('hub_site', '')).strip()
        if not odf_nombre or not hub_site:
            continue
            
        odf_datos = {
            'sala': row.get('sala'),
            'rack': row.get('rack'),
            'capacidad_puertos': row.get('capacidad_puertos', 0) if pd.notna(row.get('capacidad_puertos')) else 0,
            'tipo_conector': row.get('tipo_conector'),
            'estado': row.get('estado'),
            'observaciones': row.get('observaciones'),
        }
        
        InventarioODF.objects.update_or_create(
            odf=odf_nombre,
            hub_site=hub_site,
            defaults=odf_datos
        )

@transaction.atomic
def _procesar_puertos_odf_inventario(file):
    """Carga el detalle granular de puertos de ODF desde un CSV (Archivo E)"""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))
    from ..models import DetallePuertoODF, InventarioODF

    for _, row in df.iterrows():
        odf_nombre = str(row.get('odf', '')).strip()
        hub_site = str(row.get('hub_site', '')).strip()
        puerto_num = str(row.get('puerto_odf', '')).strip()
        if not odf_nombre or not puerto_num or not hub_site:
            continue
            
        odf_obj = InventarioODF.objects.filter(odf=odf_nombre, hub_site=hub_site).first()
        
        puerto_datos = {
            'odf_obj': odf_obj,
            'odf': odf_nombre,
            'bandeja': row.get('bandeja'),
            'fibra': row.get('fibra'),
            'estado_puerto': row.get('estado_puerto', 'Libre'),
            'tipo_conector': row.get('tipo_conector'),
            'patchcord': row.get('patchcord'),
            'destino': row.get('destino'),
            'circuito_servicio': row.get('circuito_servicio'),
            'observaciones': row.get('observaciones'),
        }
        
        # Filtramos por odf_obj si existe, para asegurar que no se crucen puertos de distintos hubs
        if odf_obj:
            DetallePuertoODF.objects.update_or_create(
                odf_obj=odf_obj,
                puerto_odf=puerto_num,
                defaults=puerto_datos
            )
        else:
            DetallePuertoODF.objects.update_or_create(
                odf=odf_nombre,
                puerto_odf=puerto_num,
                defaults=puerto_datos
            )

    # Recalcular conteos para todos los ODFs afectados en este archivo
    # Usamos drop_duplicates para obtener combinaciones unicas de odf y hub_site
    if 'hub_site' in df.columns:
        odfs_a_actualizar = df[['odf', 'hub_site']].dropna(subset=['odf', 'hub_site']).drop_duplicates()
        for _, r in odfs_a_actualizar.iterrows():
            odf_name = str(r['odf']).strip()
            hub_site = str(r['hub_site']).strip()
            odf_obj = InventarioODF.objects.filter(odf=odf_name, hub_site=hub_site).first()
            if odf_obj:
                ocupados = DetallePuertoODF.objects.filter(odf_obj=odf_obj, estado_puerto='Ocupado').count()
                libres = DetallePuertoODF.objects.filter(odf_obj=odf_obj, estado_puerto='Libre').count()
                
                odf_obj.puertos_ocupados = ocupados
                odf_obj.puertos_libres = libres
                odf_obj.save()

@transaction.atomic
def _procesar_coordenadas_csv(file):
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
    # Agrupar por ruta para procesar
    for ruta_nombre, df_coords in df.groupby('ruta'):
        ruta_nombre = str(ruta_nombre).strip()
        if not ruta_nombre or ruta_nombre == 'nan':
            continue
            
        ruta_obj = Ruta.objects.filter(nombre=ruta_nombre).first()
        if not ruta_obj:
            ruta_obj = Ruta.objects.create(nombre=ruta_nombre)
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
                    latitud=lat,
                    longitud=lon,
                    orden=i + 1,
                    tramo_secuencia=tramo_actual
                )
            )
            
        # Guardar en base de datos
        CoordenadaRuta.objects.bulk_create(coords_a_crear)
        
        # Guardar distancia total
        ruta_obj.distancia_m = distancia_total
        ruta_obj.save(update_fields=['distancia_m'])
        
        inventario_a_crear = []
        for num_tramo, meta in tramos_data.items():
            tramo_datos = dict(datos_respaldo)
            tramo_datos['distancia_m'] = meta['distancia_m']
            inventario_a_crear.append(
                InventarioTramo(
                    ruta=ruta_obj,
                    tramo_secuencia=num_tramo,
                    tipo_trazado=meta['tipo_trazado'],
                    **tramo_datos
                )
            )
        InventarioTramo.objects.bulk_create(inventario_a_crear)
        rutas_actualizadas += 1
        
    return rutas_actualizadas



@login_required
@permission_required('mapas.add_ruta', raise_exception=True)
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

    procesador_func, nombre_amigable = PROCESADORES[tipo_csv]

    if request.method == 'POST':
        if 'csv_file' in request.FILES:
            archivo_subido = request.FILES['csv_file']

            extension_valida = '.zip' if tipo_csv in ['coordenadas_rutas', 'coordenadas_inventario'] else '.csv'
            if not archivo_subido.name.endswith(extension_valida):
                messages.error(request, f"El archivo debe ser de formato {extension_valida}.")
                return redirect('configuracion')

            try:
                if tipo_csv == 'asociar_puertos':
                    puertos_actualizados = procesador_func(archivo_subido)
                    messages.success(request, f"Proceso completado. Se asociaron {puertos_actualizados} puertos a sus rutas.")
                elif tipo_csv == 'coordenadas_csv':
                    rutas_actualizadas = procesador_func(archivo_subido)
                    messages.success(request, f"Carga unificada exitosa. Se actualizaron las coordenadas de {rutas_actualizadas} rutas.")
                elif tipo_csv == 'coordenadas_rutas_csv':
                    rutas_actualizadas = procesador_func(archivo_subido)
                    messages.success(request, f"Carga de trazado unificado exitosa. Se actualizaron las coordenadas de {rutas_actualizadas} rutas.")
                else:
                    procesador_func(archivo_subido)
                    messages.success(request, f"Los datos de '{nombre_amigable}' han sido actualizados correctamente.")

                return redirect('configuracion')
            except Exception as e:
                messages.error(request, f"Ocurrió un error al procesar el archivo: {e}")
                return redirect('configuracion')
        else:
            messages.error(request, "No se adjuntó ningún archivo.")
            return redirect('configuracion')

    return redirect('configuracion')
