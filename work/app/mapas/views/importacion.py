"""
Vistas de importación de datos CSV y configuración del sistema.
"""
import os
import io
import csv
import hashlib
import json
import logging
import re
import threading
import unicodedata
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
from django.conf import settings
from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import URLValidator
from django.db import close_old_connections, transaction
from django.db.models import Count, Q
from django.utils.timezone import now
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
import math
from ..models import (
    OTU, Ruta, PuertoOTU, CoordenadaRuta, Reserva, IDRuta,
    InventarioTramo, InventarioFibra, InventarioODF, LoteImportacion, NodoRed,
    HubSite,
    FibraTramo, TerminacionFibra, DetallePuertoODF,
)
from ..services.topologia import (
    codigo_tramo_automatico,
    inferir_tipo_nodo,
    nodo_identifica_texto,
    normalizar_tipo_nodo,
    resolver_nodo,
)
from ..services.ubicacion import nombre_site

logger = logging.getLogger('fibergenius.import')

_IMPORT_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix='fibergenius-import',
)
_IMPORT_PROGRESS_LOCAL = threading.local()

VALORES_VACIOS = {'', '-', 'nan', 'none', 'null', 'sin_dato', 'no_aplica', 'n/a'}

TIPOS_NODO_CSV = {
    'SITE', 'ODF', 'MUFA', 'CAMARA', 'POSTE', 'CAJA_EMPALME', 'PUNTO', 'OTRO',
}

ESTADOS_FIBRA_CSV = {
    'disponible': 'DISPONIBLE',
    'ocupado': 'OCUPADO',
    'ocupada': 'OCUPADO',
    'reservado': 'RESERVADO',
    'reservada': 'RESERVADO',
    'sin informacion': 'SIN_INFORMACION',
    'sin información': 'SIN_INFORMACION',
}

ESTADOS_FIBRA_GLOBAL_CSV = {
    **ESTADOS_FIBRA_CSV,
    'disponibles': 'DISPONIBLE',
}


def _directorio_importaciones():
    base = Path(
        getattr(
            settings,
            'DATA_ROOT',
            getattr(settings, 'MEDIA_ROOT', Path(settings.BASE_DIR) / 'media'),
        )
    )
    directorio = base / 'importaciones'
    (directorio / 'pendientes').mkdir(parents=True, exist_ok=True)
    (directorio / 'progreso').mkdir(parents=True, exist_ok=True)
    return directorio


def _ruta_progreso(codigo):
    return _directorio_importaciones() / 'progreso' / f'{codigo}.json'


def _guardar_progreso(codigo, **cambios):
    """Publica avance fuera de la BD para que SQLite no bloquee el sondeo."""
    ruta = _ruta_progreso(codigo)
    actual = {}
    if ruta.exists():
        try:
            actual = json.loads(ruta.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            actual = {}
    actual.update(cambios)
    actual['codigo'] = str(codigo)
    actual['actualizado_en'] = now().isoformat()
    temporal = ruta.with_suffix(f'.{threading.get_ident()}.tmp')
    temporal.write_text(
        json.dumps(actual, ensure_ascii=False),
        encoding='utf-8',
    )
    os.replace(temporal, ruta)
    return actual


def _reportar_progreso(
    porcentaje,
    etapa,
    *,
    procesadas=None,
    total=None,
    creadas=None,
    actualizadas=None,
    rechazadas=None,
):
    reporter = getattr(_IMPORT_PROGRESS_LOCAL, 'reporter', None)
    if reporter is None:
        return
    datos = {
        'porcentaje': max(0, min(100, int(porcentaje))),
        'etapa': str(etapa),
        'estado': 'PROCESANDO',
    }
    for clave, valor in (
        ('procesadas', procesadas),
        ('total', total),
        ('creadas', creadas),
        ('actualizadas', actualizadas),
        ('rechazadas', rechazadas),
    ):
        if valor is not None:
            datos[clave] = int(valor)
    reporter(**datos)


def _dividir_en_lotes(valores, tamano=500):
    """Divide identificadores para no superar el límite de parámetros SQL."""
    valores = list(valores)
    for indice in range(0, len(valores), tamano):
        yield valores[indice:indice + tamano]


def _observacion_global_desde_terminacion(valor):
    """Retira de la nota global datos redundantes del extremo y su puerto."""
    texto = _texto(valor)
    texto = re.sub(
        r'(?i)(^|;\s*)Terminacion=[AB];\s*',
        r'\1',
        texto,
    )
    texto = re.sub(
        r'(?i)(^|;\s*)Puerto local=[^;]*;\s*',
        r'\1',
        texto,
    )
    texto = re.sub(r';\s*;', ';', texto)
    return texto.strip(' ;')


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


def _texto_extremo(valor):
    """Descarta marcadores operativos que no identifican un nodo real."""
    texto = _texto(valor)
    clave = re.sub(r'[\s\-/]+', '_', texto.casefold()).strip('_')
    if clave in {
        'sin_dato', 'sin_informacion', 'por_levantar',
        'pendiente', 'desconocido', 'no_aplica',
    }:
        return ''
    return texto


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


def _normalizar_columna_csv(nombre):
    """Convierte encabezados humanos a una clave estable sin acentos."""
    texto = unicodedata.normalize('NFKD', str(nombre or '').strip())
    texto = ''.join(caracter for caracter in texto if not unicodedata.combining(caracter))
    return re.sub(r'[^a-z0-9]+', '_', texto.casefold()).strip('_')


def _normalizar_dataframe_csv(df, aliases=None):
    aliases = aliases or {}
    columnas = {}
    repetidas = []
    for original in df.columns:
        normalizada = aliases.get(
            _normalizar_columna_csv(original),
            _normalizar_columna_csv(original),
        )
        if normalizada in columnas.values():
            repetidas.append(normalizada)
        columnas[original] = normalizada
    if repetidas:
        raise ValueError(
            'El CSV contiene encabezados equivalentes repetidos: '
            + ', '.join(sorted(set(repetidas)))
        )
    return df.rename(columns=columnas)


def _leer_csv_normalizado(file, aliases=None):
    try:
        contenido = file.read().decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('El archivo debe estar codificado en UTF-8.') from exc
    try:
        df = pd.read_csv(
            io.StringIO(contenido),
            dtype=str,
            keep_default_na=False,
        )
    except pd.errors.ParserError as exc:
        raise ValueError(f'No se pudo interpretar el CSV: {exc}') from exc
    if df.empty:
        raise ValueError('El CSV no contiene filas de datos.')
    df = _normalizar_dataframe_csv(df, aliases=aliases)
    _reportar_progreso(
        20,
        'Archivo leído; validando estructura y relaciones',
        procesadas=0,
        total=len(df),
    )
    return df


def _capacidad_hilos(valor, *, permitir_vacio=True):
    """Acepta 48, 48.0, "48 Hilos" o "48 fibras" y devuelve un entero."""
    texto = _texto(valor)
    if not texto:
        if permitir_vacio:
            return None
        raise ValueError('la capacidad es obligatoria')
    coincidencia = re.fullmatch(
        r'(\d+)(?:[.,]0+)?(?:\s*(?:hilos?|fibras?))?',
        texto,
        flags=re.IGNORECASE,
    )
    if not coincidencia:
        raise ValueError(f'capacidad inválida: {valor}')
    return int(coincidencia.group(1))


def _numero_no_negativo(valor, nombre, *, entero=True, default=None):
    try:
        numero = _entero(valor, default) if entero else _float(valor, default)
    except ValueError as exc:
        raise ValueError(f'{nombre}: {exc}') from exc
    if numero is not None and numero < 0:
        raise ValueError(f'{nombre} no puede ser negativo')
    return numero


def _errores_csv(errores, etiqueta):
    if not errores:
        return
    muestra = '; '.join(errores[:20])
    restante = len(errores) - 20
    sufijo = f'; y {restante} error(es) adicional(es)' if restante > 0 else ''
    raise ValueError(f'{etiqueta}: {muestra}{sufijo}. No se realizó ningún cambio.')

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


def _normalizar_tipo_trazado(valor):
    """Normaliza el dato cartográfico sin mezclarlo con el inventario técnico."""
    return _texto(valor, default='DESCONOCIDO').upper()


def _preparar_coordenadas_geograficas(df, ruta, lote=None):
    """
    Valida y construye el trazado sin escribir todavía en la base de datos.

    `tramo_secuencia` se conserva como compatibilidad temporal; la geografía
    nueva usa `inicio_segmento` y no se enlaza con InventarioTramo.
    """
    df = df.copy()
    df.columns = [str(columna).strip().lower() for columna in df.columns]
    faltantes = [
        columna for columna in ('latitude', 'longitude')
        if columna not in df.columns
    ]
    if faltantes:
        raise ValueError(
            f"Ruta '{ruta.nombre}': faltan columnas requeridas: "
            f"{', '.join(faltantes)}."
        )
    if len(df) < 2:
        raise ValueError(
            f"La ruta '{ruta.nombre}' necesita al menos dos coordenadas."
        )

    coordenadas = []
    distancia_total = 0.0
    lat_anterior = None
    lon_anterior = None
    tipo_anterior = None
    segmento_secuencia = 1

    for posicion, (_, fila) in enumerate(df.iterrows(), start=1):
        numero_fila = posicion + 1
        latitud = _decimal(fila.get('latitude'))
        longitud = _decimal(fila.get('longitude'))
        if latitud is None or longitud is None:
            raise ValueError(
                f"Ruta '{ruta.nombre}', fila {numero_fila}: "
                "latitude y longitude son obligatorias."
            )
        if not Decimal('-90') <= latitud <= Decimal('90'):
            raise ValueError(
                f"Ruta '{ruta.nombre}', fila {numero_fila}: "
                f"latitude fuera de rango ({latitud})."
            )
        if not Decimal('-180') <= longitud <= Decimal('180'):
            raise ValueError(
                f"Ruta '{ruta.nombre}', fila {numero_fila}: "
                f"longitude fuera de rango ({longitud})."
            )

        tipo_trazado = _normalizar_tipo_trazado(
            fila.get('tipo_trazado')
        )
        valor_corte = _texto(
            fila.get('new_seg'),
            default='false',
        ).casefold()
        if valor_corte not in {'true', '1', 'false', '0'}:
            raise ValueError(
                f"Ruta '{ruta.nombre}', fila {numero_fila}: "
                f"new_seg inválido ({fila.get('new_seg')})."
            )
        marca_corte = valor_corte in {'true', '1'}
        inicio_segmento = (
            posicion == 1
            or marca_corte
            or tipo_trazado != tipo_anterior
        )
        if posicion > 1 and inicio_segmento:
            segmento_secuencia += 1

        lat_float = float(latitud)
        lon_float = float(longitud)
        if lat_anterior is not None and lon_anterior is not None:
            distancia_total += calcular_distancia_haversine(
                lat_anterior,
                lon_anterior,
                lat_float,
                lon_float,
            )
        lat_anterior = lat_float
        lon_anterior = lon_float
        tipo_anterior = tipo_trazado

        coordenadas.append(CoordenadaRuta(
            ruta=ruta,
            latitud=latitud,
            longitud=longitud,
            orden=posicion,
            tipo_trazado=tipo_trazado,
            inicio_segmento=inicio_segmento,
            tramo_secuencia=segmento_secuencia,
            tramo=None,
            lote_importacion=lote,
        ))

    return coordenadas, distancia_total


def _reemplazar_geografia_ruta(ruta, df, lote=None):
    """
    Reemplaza solo la geometría. Toda la validación precede al borrado para
    evitar que un CSV inválido deje la ruta sin coordenadas.
    """
    ruta = Ruta.objects.select_for_update().get(pk=ruta.pk)
    coordenadas, distancia_total = _preparar_coordenadas_geograficas(
        df,
        ruta,
        lote,
    )
    CoordenadaRuta.objects.filter(ruta=ruta).delete()
    CoordenadaRuta.objects.bulk_create(coordenadas, batch_size=2000)

    ruta.distancia_m = distancia_total
    campos_actualizados = ['distancia_m']
    if lote is not None:
        ruta.lote_importacion = lote
        campos_actualizados.append('lote_importacion')
    ruta.save(update_fields=campos_actualizados)
    return len(coordenadas)


logger = logging.getLogger('fibergenius.import')


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
def configuracion(request):
    rutas = Ruta.objects.all().order_by('nombre')
    lotes = LoteImportacion.objects.select_related('usuario').order_by('-creado_en')[:20]
    return render(
        request,
        'configuracion/configuracion_index.html',
        {'rutas': rutas, 'lotes_importacion': lotes},
    )


@login_required
def descargar_plantilla_terminaciones_fibra(request):
    """Genera una plantilla con una fila por fibra lógica del inventario."""
    permisos = (
        'mapas.view_terminacionfibra',
        'mapas.add_terminacionfibra',
        'mapas.change_terminacionfibra',
    )
    if not request.user.is_superuser and not any(
        request.user.has_perm(permiso) for permiso in permisos
    ):
        raise PermissionDenied

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        'attachment; filename="terminaciones_fibra_plantilla.csv"'
    )
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow([
        'Ruta',
        'ID Fibra',
        'Fibra',
        'Codigo Fibra',
        'Extremo',
        'Site',
        'ODF',
        'Puerto',
        'Conector',
        'Estado Global',
        'Condicion Fisica',
        'Servicio',
        'Observaciones',
    ])

    fibras = (
        InventarioFibra.objects
        .select_related('ruta')
        .prefetch_related(
            'terminaciones__puerto_odf__odf_obj__rack_obj__sala__hub_site'
        )
        .order_by('ruta__nombre', 'fibra_numero', 'pk')
    )
    for fibra in fibras:
        terminaciones = list(fibra.terminaciones.all()) or [None]
        for terminacion in terminaciones:
            odf = terminacion.puerto_odf.odf_obj if terminacion else None
            writer.writerow([
                fibra.ruta.nombre if fibra.ruta_id else '',
                fibra.pk,
                fibra.fibra_numero,
                fibra.codigo_fibra,
                terminacion.extremo if terminacion else '',
                nombre_site(odf, '') if odf else '',
                odf.odf if odf else '',
                terminacion.puerto_odf.puerto_odf if terminacion else '',
                terminacion.tipo_conector if terminacion else '',
                (
                    fibra.get_estado_display()
                    if fibra.origen_estado == 'INFORMADO'
                    else ''
                ),
                fibra.get_condicion_fisica_display(),
                fibra.nombre_fibra or '',
                fibra.observaciones or '',
            ])
    return response


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
def _procesar_sites_inventario(file, lote=None):
    """Crea o actualiza Sites con validación completa antes de escribir."""
    df = _leer_csv_normalizado(file, aliases={
        'site': 'nombre',
        'hub': 'nombre',
        'hub_site': 'nombre',
        'direccion_site': 'direccion',
    })
    if 'nombre' not in df.columns:
        raise ValueError('Sites: falta la columna obligatoria Nombre.')

    preparadas = []
    errores = []
    nombres_archivo = set()
    for numero_fila, row in enumerate(df.to_dict('records'), start=2):
        nombre = _texto(row.get('nombre'))
        try:
            if not nombre:
                raise ValueError('Nombre es obligatorio')
            clave = nombre.casefold()
            if clave in nombres_archivo:
                raise ValueError(f"el Site '{nombre}' está repetido")
            nombres_archivo.add(clave)

            latitud = _decimal(row.get('latitud')) if 'latitud' in df.columns else None
            longitud = _decimal(row.get('longitud')) if 'longitud' in df.columns else None
            if (latitud is None) != (longitud is None):
                raise ValueError('Latitud y Longitud deben informarse juntas')
            if latitud is not None and not Decimal('-90') <= latitud <= Decimal('90'):
                raise ValueError(f'Latitud fuera de rango ({latitud})')
            if longitud is not None and not Decimal('-180') <= longitud <= Decimal('180'):
                raise ValueError(f'Longitud fuera de rango ({longitud})')

            preparadas.append({
                'nombre': nombre,
                'latitud': latitud,
                'longitud': longitud,
                'direccion': (
                    _texto(row.get('direccion')) or None
                    if 'direccion' in df.columns else None
                ),
            })
        except (ValueError, ValidationError) as exc:
            errores.append(f'fila {numero_fila}: {exc}')
    _errores_csv(errores, 'Sites')

    existentes = {
        site.nombre.casefold(): site
        for site in HubSite.objects.select_for_update().all()
    }
    creadas = actualizadas = 0
    for item in preparadas:
        site = existentes.get(item['nombre'].casefold())
        creada = site is None
        if creada:
            site = HubSite(nombre=item['nombre'])
        if creada or item['latitud'] is not None:
            site.latitud = item['latitud']
            site.longitud = item['longitud']
        if creada or item['direccion'] is not None:
            site.direccion = item['direccion']
        site.lote_importacion = lote
        site.full_clean()
        site.save()
        existentes[item['nombre'].casefold()] = site
        creadas += int(creada)
        actualizadas += int(not creada)
    return _resultado(len(df), creadas, actualizadas)


@transaction.atomic
def _procesar_fibras_globales(file, lote=None):
    """Crea o actualiza la identidad y el estado global de cada fibra."""
    df = _leer_csv_normalizado(file, aliases={
        'codigo_de_fibra': 'codigo_fibra',
        'fibra_logica': 'codigo_fibra',
        'numero_de_fibra': 'fibra',
        'n_de_fibra': 'fibra',
        'hilo': 'fibra',
        'troncal': 'ruta',
        'estado_global': 'estado',
        'condicion_fisica': 'condicion',
        'tipo_de_servicio': 'servicio',
        'nombre_servicio': 'servicio',
        'tipo_de_conector': 'conector',
        'notas': 'observaciones',
    })
    requeridas = {'codigo_fibra', 'fibra', 'estado'}
    faltantes = sorted(requeridas - set(df.columns))
    if faltantes:
        raise ValueError(
            'Fibras globales: faltan columnas obligatorias: '
            + ', '.join(faltantes)
        )

    condiciones = {
        'operativa': 'OPERATIVA',
        'con falla': 'CON_FALLA',
        'con_falla': 'CON_FALLA',
        'sin verificar': 'SIN_VERIFICAR',
        'sin_verificar': 'SIN_VERIFICAR',
    }
    rutas = {
        ruta.nombre.casefold(): ruta
        for ruta in Ruta.objects.all().only('pk', 'nombre')
    }
    preparadas = []
    errores = []
    codigos_archivo = set()
    posiciones_archivo = set()
    for numero_fila, row in enumerate(df.to_dict('records'), start=2):
        codigo = _texto(row.get('codigo_fibra')).upper()
        fibra_numero = _texto(row.get('fibra')).upper()
        ruta_nombre = _texto(row.get('ruta')) if 'ruta' in df.columns else ''
        try:
            if codigo and re.fullmatch(r'F[1-9]\d*', codigo):
                raise ValueError(
                    'Codigo Fibra debe ser global y estable; no use '
                    f'{codigo}, que representa una posición física'
                )
            if codigo and len(codigo) > 64:
                raise ValueError('Codigo Fibra no puede superar 64 caracteres')
            if not fibra_numero:
                raise ValueError('Fibra es obligatoria')
            if codigo and codigo.casefold() in codigos_archivo:
                raise ValueError(f"Codigo Fibra '{codigo}' está repetido")
            if codigo:
                codigos_archivo.add(codigo.casefold())

            ruta = rutas.get(ruta_nombre.casefold()) if ruta_nombre else None
            if ruta_nombre and ruta is None:
                raise ValueError(f"la troncal '{ruta_nombre}' no existe")
            if not codigo and ruta is None:
                raise ValueError(
                    'sin Codigo Fibra debe informar una Ruta para identificar '
                    'inequívocamente Ruta + Fibra'
                )
            if ruta is not None:
                clave_posicion = (ruta.pk, fibra_numero.casefold())
                if clave_posicion in posiciones_archivo:
                    raise ValueError(
                        f'{fibra_numero} está repetida dentro de la troncal'
                    )
                posiciones_archivo.add(clave_posicion)

            estado_texto = _texto(row.get('estado')).casefold()
            condicion_por_estado = None
            if estado_texto in {'malo', 'mala'}:
                estado = 'SIN_INFORMACION'
                condicion_por_estado = 'CON_FALLA'
            else:
                estado = ESTADOS_FIBRA_GLOBAL_CSV.get(estado_texto)
            if estado is None:
                raise ValueError(
                    f"Estado '{_texto(row.get('estado'))}' no válido; use "
                    'Disponible, Ocupado, Reservado o Sin información'
                )
            condicion_texto = (
                _texto(row.get('condicion')).casefold()
                if 'condicion' in df.columns else ''
            )
            condicion = (
                condiciones.get(condicion_texto)
                if condicion_texto else condicion_por_estado
            )
            if condicion_texto and condicion_texto not in condiciones:
                raise ValueError(
                    f"Condición '{_texto(row.get('condicion'))}' no válida"
                )

            preparadas.append({
                'fila': numero_fila,
                'codigo': codigo,
                'fibra_numero': fibra_numero,
                'ruta': ruta,
                'estado': estado,
                'origen_estado': (
                    'NO_INFORMADO'
                    if estado == 'SIN_INFORMACION' else 'INFORMADO'
                ),
                'condicion_fisica': condicion,
                'servicio': (
                    _texto(row.get('servicio'))
                    if 'servicio' in df.columns else None
                ),
                'conector': (
                    _texto(row.get('conector'))
                    if 'conector' in df.columns else None
                ),
                'observaciones': (
                    _texto(row.get('observaciones'))
                    if 'observaciones' in df.columns else ''
                ),
            })
        except (ValueError, ValidationError) as exc:
            errores.append(f'fila {numero_fila}: {exc}')
    _errores_csv(errores, 'Fibras globales')

    existentes = {
        fibra.codigo_fibra.casefold(): fibra
        for fibra in InventarioFibra.objects.select_for_update()
        .select_related('ruta').all()
    }
    por_ruta_numero = {
        (fibra.ruta_id, fibra.fibra_numero.casefold()): fibra
        for fibra in existentes.values()
        if fibra.ruta_id
    }
    conflictos = []
    for item in preparadas:
        fibra = (
            existentes.get(item['codigo'].casefold())
            if item['codigo'] else por_ruta_numero.get(
                (item['ruta'].pk, item['fibra_numero'].casefold())
            )
        )
        if (
            fibra is not None
            and fibra.ruta_id
            and item['ruta'] is not None
            and fibra.ruta_id != item['ruta'].pk
        ):
            conflictos.append(
                f"fila {item['fila']}: {item['codigo']} ya pertenece a "
                f"la troncal {fibra.ruta.nombre}"
            )
        if item['ruta'] is not None:
            ocupante = por_ruta_numero.get(
                (item['ruta'].pk, item['fibra_numero'].casefold())
            )
            if (
                ocupante is not None
                and item['codigo']
                and ocupante.codigo_fibra.casefold() != item['codigo'].casefold()
            ):
                conflictos.append(
                    f"fila {item['fila']}: {item['fibra_numero']} ya está "
                    f"identificada como {ocupante.codigo_fibra}"
                )
    _errores_csv(conflictos, 'Fibras globales')

    from ..services.fibras import (
        actualizar_metadatos_fibra,
        asignar_ruta_fibra,
        crear_fibra,
        establecer_estado_fibra_informado,
        restablecer_estado_fibra,
    )

    usuario = getattr(lote, 'usuario', None) if lote else None
    creadas = actualizadas = 0
    for item in preparadas:
        fibra = (
            existentes.get(item['codigo'].casefold())
            if item['codigo'] else por_ruta_numero.get(
                (item['ruta'].pk, item['fibra_numero'].casefold())
            )
        )
        creada = fibra is None
        if creada:
            fibra = crear_fibra(
                fibra_numero=item['fibra_numero'],
                codigo_fibra=item['codigo'],
                ruta=item['ruta'],
                estado=item['estado'],
                condicion_fisica=(
                    item['condicion_fisica'] or 'SIN_VERIFICAR'
                ),
                nombre_fibra=item['servicio'] or '',
                tipo_conector=item['conector'] or '',
                observaciones=item['observaciones'],
                usuario=usuario,
                origen='EXCEL',
                lote_importacion=lote,
            )
        else:
            cambios = {'fibra_numero': item['fibra_numero']}
            if item['condicion_fisica'] is not None:
                cambios['condicion_fisica'] = item['condicion_fisica']
            if item['servicio'] is not None:
                cambios['nombre_fibra'] = item['servicio']
            if item['conector'] is not None:
                cambios['tipo_conector'] = item['conector']
            if 'observaciones' in df.columns:
                cambios['observaciones'] = item['observaciones']
            actualizar_metadatos_fibra(
                fibra=fibra,
                usuario=usuario,
                origen='EXCEL',
                lote_importacion=lote,
                **cambios,
            )
            if item['estado'] == 'SIN_INFORMACION':
                restablecer_estado_fibra(
                    fibra=fibra,
                    usuario=usuario,
                    origen='EXCEL',
                    lote_importacion=lote,
                )
            else:
                establecer_estado_fibra_informado(
                    fibra=fibra,
                    estado=item['estado'],
                    usuario=usuario,
                    origen='EXCEL',
                    lote_importacion=lote,
                )
            if item['ruta'] is not None and fibra.ruta_id is None:
                fibra, _, _ = asignar_ruta_fibra(
                    fibra=fibra,
                    ruta=item['ruta'],
                    usuario=usuario,
                    origen='EXCEL',
                    lote_importacion=lote,
                )
        existentes[fibra.codigo_fibra.casefold()] = fibra
        if fibra.ruta_id:
            por_ruta_numero[(fibra.ruta_id, fibra.fibra_numero.casefold())] = fibra
        creadas += int(creada)
        actualizadas += int(not creada)

    resultado = _resultado(len(df), creadas, actualizadas)
    resultado['informaciones'] = [
        'La identidad, metadatos, estado y Ruta se aplicaron mediante los '
        'servicios oficiales. Las Rutas de un tramo materializan su FibraTramo.'
    ]
    return resultado


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
    df = _leer_csv_normalizado(file, aliases={
        'nombre_ruta': 'ruta',
        'troncal': 'ruta',
        'distancia': 'distancia_m',
        'enlace_pon_traxium': 'enlace',
        'enlace_pon_view': 'enlace',
        'capacidad_hilos': 'capacidad',
        'capacidad_declarada': 'capacidad',
        'capacidad_declarada_hilos': 'capacidad',
        'hilos_ocupados_declarados': 'hilos_ocupados',
        'hilos_reservados_declarados': 'hilos_reservados',
        'hilos_libres_declarados': 'hilos_libres',
        'reserva_m': 'reservas_m',
        'reservas_declaradas_m': 'reservas_m',
    })
    columnas = set(df.columns)
    if 'ruta' not in columnas:
        raise ValueError(
            'Troncales y datos generales: falta la columna Ruta.'
        )
    creadas = actualizadas = rechazadas = 0
    advertencias = []
    for numero_fila, row in enumerate(df.to_dict('records'), start=2):
        ruta_nombre = _texto(row.get('ruta'))
        if not ruta_nombre:
            rechazadas += 1
            advertencias.append(
                f'Fila {numero_fila}: falta el nombre de la troncal.'
            )
            continue
        try:
            ruta_datos = {'lote_importacion': lote}
            if 'otu' in columnas:
                otu_nombre = _texto(row.get('otu'))
                otu = (
                    OTU.objects.filter(nombre__iexact=otu_nombre).first()
                    if otu_nombre else None
                )
                if otu_nombre and otu is None:
                    raise ValueError(f"OTU '{otu_nombre}' no existe")
                ruta_datos['otu'] = otu
            if 'distancia_m' in columnas:
                distancia = _numero_no_negativo(
                    row.get('distancia_m'),
                    'Distancia (m)',
                    entero=False,
                )
                ruta_datos['distancia_m'] = distancia
                ruta_datos['distancia_declarada_m'] = distancia
            if 'olt' in columnas:
                ruta_datos['olt'] = _texto(row.get('olt')) or None
            if 'slot_olt' in columnas:
                ruta_datos['slot_olt'] = _numero_no_negativo(
                    row.get('slot_olt'),
                    'SLOT OLT',
                )
            if 'puerto_olt' in columnas:
                ruta_datos['puerto_olt'] = _numero_no_negativo(
                    row.get('puerto_olt'),
                    'PUERTO OLT',
                )
            if 'pon' in columnas:
                ruta_datos['pon'] = _texto(row.get('pon')) or None
            if 'enlace' in columnas:
                ruta_datos['enlace'] = _url_o_none(row.get('enlace'))

            if 'capacidad' in columnas:
                capacidad = _capacidad_hilos(row.get('capacidad'))
                if capacidad == 0:
                    raise ValueError(
                        'Capacidad debe ser mayor que cero o quedar vacía'
                    )
                ruta_datos['capacidad_declarada_hilos'] = capacidad
            for columna, campo, etiqueta in (
                (
                    'hilos_ocupados',
                    'hilos_ocupados_declarados',
                    'Hilos Ocupados',
                ),
                (
                    'hilos_reservados',
                    'hilos_reservados_declarados',
                    'Hilos Reservados',
                ),
                (
                    'hilos_libres',
                    'hilos_libres_declarados',
                    'Hilos Libres',
                ),
            ):
                if columna in columnas:
                    ruta_datos[campo] = _numero_no_negativo(
                        row.get(columna),
                        etiqueta,
                    )
            if 'reservas_m' in columnas:
                ruta_datos['reservas_declaradas_m'] = (
                    _numero_no_negativo(
                        row.get('reservas_m'),
                        'Reservas (m)',
                        entero=False,
                    )
                )

            campos_declarados = {
                'distancia_declarada_m',
                'capacidad_declarada_hilos',
                'hilos_ocupados_declarados',
                'hilos_reservados_declarados',
                'hilos_libres_declarados',
                'reservas_declaradas_m',
            }
            if campos_declarados & set(ruta_datos):
                ruta_datos.update({
                    'fuente_declaracion': (
                        lote.archivo_origen
                        if lote is not None
                        else 'Carga CSV'
                    ),
                    'fecha_declaracion': (
                        lote.fecha_origen
                        if lote is not None and lote.fecha_origen is not None
                        else now()
                    ),
                    'declarado_por': (
                        lote.usuario if lote is not None else None
                    ),
                })

            ruta = Ruta.objects.filter(
                nombre__iexact=ruta_nombre,
            ).first()
            creada = ruta is None
            if creada:
                Ruta.objects.create(
                    nombre=ruta_nombre,
                    **ruta_datos,
                )
            else:
                for campo, valor in ruta_datos.items():
                    setattr(ruta, campo, valor)
                ruta.save()
            creadas += int(creada)
            actualizadas += int(not creada)
        except (ValueError, ValidationError) as exc:
            rechazadas += 1
            advertencias.append(f'Fila {numero_fila}: {exc}')

    resultado = _resultado(
        len(df),
        creadas,
        actualizadas,
        rechazadas,
    )
    if advertencias:
        resultado['advertencias'] = advertencias
    return resultado


@transaction.atomic
def _procesar_coordenadas_zip(file, lote=None):
    """Para cada ruta en el ZIP, reemplaza únicamente su geometría."""
    total = creadas = rechazadas = 0
    with zipfile.ZipFile(file, 'r') as zip_ref:
        for filename in zip_ref.namelist():
            if filename.lower().endswith('.csv'):
                ruta_nombre = os.path.splitext(os.path.basename(filename))[0]
                ruta_obj = Ruta.objects.filter(nombre=ruta_nombre).first()

                if ruta_obj:
                    with zip_ref.open(filename) as csv_file:
                        csv_content = csv_file.read().decode('utf-8-sig')
                        df_coords = pd.read_csv(io.StringIO(csv_content))
                    cantidad = _reemplazar_geografia_ruta(
                        ruta_obj,
                        df_coords,
                        lote,
                    )
                    total += cantidad
                    creadas += cantidad
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
    Importa el trazado geográfico segmentado sin modificar tramos técnicos.

    Un segmento visual comienza cuando `new_seg` es verdadero o cambia
    `tipo_trazado`. Se conserva el archivo y el resultado visual existentes,
    pero este proceso ya no crea, elimina ni actualiza InventarioTramo.
    """
    total = creadas = rechazadas = 0
    with zipfile.ZipFile(file, 'r') as zip_ref:
        for filename in zip_ref.namelist():
            if filename.lower().endswith('.csv'):
                ruta_nombre = os.path.splitext(os.path.basename(filename))[0]
                ruta_obj = Ruta.objects.filter(nombre=ruta_nombre).first()

                if ruta_obj:
                    with zip_ref.open(filename) as csv_file:
                        csv_content = csv_file.read().decode('utf-8-sig')
                        df_coords = pd.read_csv(io.StringIO(csv_content))
                    cantidad = _reemplazar_geografia_ruta(
                        ruta_obj,
                        df_coords,
                        lote,
                    )
                    total += cantidad
                    creadas += cantidad
                else:
                    rechazadas += 1

    return _resultado(total + rechazadas, creadas, rechazadas=rechazadas)

def _procesar_tramos_legacy_validado(df, lote=None):
    """Adapta RC1/V5 a un solo tramo sin modificar los demás."""
    if 'ruta' not in df.columns:
        raise ValueError(
            'Detalles técnicos legacy: falta la columna ruta o ident.'
        )

    rutas = {
        ruta.nombre.casefold(): ruta
        for ruta in Ruta.objects.all().only('pk', 'nombre')
    }
    preparadas = []
    rechazadas = 0
    advertencias = []
    informaciones = [
        'Se conservó el formato habitual de Detalles Técnicos '
        'Complementarios. Las columnas de identificación de tramo solo son '
        'necesarias para rutas con varios tramos.'
    ]
    for indice, row in enumerate(df.to_dict('records'), start=2):
        ruta_nombre = _texto(row.get('ruta'))
        ruta = rutas.get(ruta_nombre.casefold()) if ruta_nombre else None
        try:
            if not ruta_nombre:
                raise ValueError('ruta vacía')
            if not ruta:
                raise ValueError(f"la ruta '{ruta_nombre}' no existe")
            capacidad = _capacidad_hilos(row.get('capacidad'))
            if capacidad == 0:
                capacidad = None
                advertencias.append(
                    f'{ruta.nombre}: capacidad 0 tratada como desconocida.'
                )
            hilos_ocupados = _numero_no_negativo(
                row.get('hilos_ocupados'),
                'hilos_ocupados',
                default=0,
            )
            hilos_libres = _numero_no_negativo(
                row.get('hilos_libres'),
                'hilos_libres',
                default=0,
            )
            if (
                capacidad is not None
                and hilos_ocupados + hilos_libres > capacidad
            ):
                raise ValueError(
                    'los contadores legacy '
                    f'({hilos_ocupados} ocupados + {hilos_libres} libres) '
                    f'exceden la capacidad declarada ({capacidad})'
                )
            preparadas.append({
                'fila': indice,
                'ruta': ruta,
                'distancia_m': _numero_no_negativo(
                    row.get('distancia_m'), 'distancia',
                    entero=False, default=None,
                ),
                'mufas': _numero_no_negativo(
                    row.get('mufas'), 'mufas', default=0
                ),
                'splitters': _numero_no_negativo(
                    row.get('splitters'), 'splitters', default=0
                ),
                'reservas_m': _numero_no_negativo(
                    row.get('reservas_m'), 'reservas_m',
                    entero=False, default=0.0,
                ),
                'capacidad_hilos': capacidad,
                'estado': _texto(row.get('estado')) or None,
                'tipo_fibra': _texto(row.get('tipo_fibra')) or None,
                'origen': _texto(row.get('origen')) or None,
                'destino': _texto(row.get('destino')) or None,
                'hub_site': _texto(row.get('hub_site')) or None,
                'marca_modelo': _texto(row.get('marca_modelo')) or None,
                'serial': _texto(row.get('serial')) or None,
                'odf_nombre': _texto(row.get('odf_nombre')) or None,
                'hilos_ocupados': hilos_ocupados,
                'hilos_libres': hilos_libres,
            })
        except Exception as exc:
            rechazadas += 1
            advertencias.append(f'Fila {indice} rechazada: {exc}')

    ids_ruta = sorted({item['ruta'].pk for item in preparadas})
    list(Ruta.objects.select_for_update().filter(pk__in=ids_ruta))
    tramos_por_ruta = defaultdict(list)
    for tramo_existente in (
        InventarioTramo.objects.select_for_update()
        .filter(ruta_id__in=ids_ruta)
        .order_by('ruta_id', 'tramo_secuencia', 'pk')
    ):
        tramos_por_ruta[tramo_existente.ruta_id].append(tramo_existente)
    cantidad_tramos_por_ruta = {
        ruta_id: len(tramos)
        for ruta_id, tramos in tramos_por_ruta.items()
    }
    creadas = actualizadas = 0
    for item in preparadas:
        ruta = item['ruta']
        tramos_existentes = tramos_por_ruta.get(ruta.pk, [])
        if len(tramos_existentes) > 1:
            rechazadas += 1
            advertencias.append(
                f'{ruta.nombre}: el archivo anterior no identifica a cuál '
                f'de sus {len(tramos_existentes)} tramos aplicar los datos; '
                'use las columnas de tramo aprobadas para esta ruta.'
            )
            continue
        tramo = tramos_existentes[0] if tramos_existentes else None
        creada = tramo is None
        if creada:
            tramo = InventarioTramo(
                ruta=ruta,
                tramo_secuencia=1,
                codigo_tramo=None,
            )

        tiene_detalle = (
            not creada
            and FibraTramo.objects.filter(tramo=tramo).exists()
        )
        for campo in (
            'estado', 'mufas', 'splitters', 'reservas_m',
            'capacidad_hilos', 'tipo_fibra', 'origen', 'destino', 'hub_site',
            'marca_modelo', 'serial', 'odf_nombre',
        ):
            setattr(tramo, campo, item[campo])
        if cantidad_tramos_por_ruta.get(ruta.pk, 0) <= 1:
            tramo.distancia_m = item['distancia_m']
        elif item['distancia_m'] is not None:
            advertencias.append(
                f'{ruta.nombre}: la distancia legacy es total de ruta y no '
                'reemplazó las distancias de sus tramos existentes.'
            )
        if tramo.origen_nodo_id is None:
            origen_codigo = _texto_extremo(
                item['origen'] or item['hub_site']
            )
            if origen_codigo:
                tramo.origen_nodo = resolver_nodo(
                    tipo=inferir_tipo_nodo(origen_codigo),
                    codigo=origen_codigo,
                    nombre=origen_codigo,
                    lote=lote,
                )
        if tramo.destino_nodo_id is None:
            destino_codigo = _texto_extremo(item['destino'])
            if destino_codigo:
                destino_nodo = resolver_nodo(
                    tipo=inferir_tipo_nodo(destino_codigo),
                    codigo=destino_codigo,
                    nombre=destino_codigo,
                    lote=lote,
                )
                if destino_nodo.pk != getattr(
                    tramo.origen_nodo,
                    'pk',
                    None,
                ):
                    tramo.destino_nodo = destino_nodo
        tramo.capacidad = (
            f"{item['capacidad_hilos']} Hilos"
            if item['capacidad_hilos'] is not None
            else None
        )
        tramo.lote_importacion = lote
        if tiene_detalle:
            informaciones.append(
                f'{ruta.nombre}: se conservaron los contadores calculados '
                'desde fibras por tramo.'
            )
        else:
            tramo.hilos_ocupados = item['hilos_ocupados']
            tramo.hilos_libres = item['hilos_libres']
            tramo.hilos_reservados = None
        tramo.save()

        if item['distancia_m'] is not None and not ruta.coordenadas.exists():
            ruta.distancia_m = item['distancia_m']
        if lote is not None:
            ruta.lote_importacion = lote
        ruta.save()
        creadas += int(creada)
        actualizadas += int(not creada)

    # El contrato legacy cuenta filas procesadas como actualizadas, incluso
    # cuando tuvo que crear el registro provisional de secuencia 1.
    resultado = _resultado(
        len(df),
        actualizadas=creadas + actualizadas,
        rechazadas=rechazadas,
    )
    resultado['advertencias'] = sorted(set(advertencias))
    resultado['formato'] = 'legacy'
    resultado['informaciones'] = sorted(set(informaciones))
    return resultado


@transaction.atomic
def _procesar_tramos_inventario(file, lote=None):
    """Carga inventario por tramo y conserva el adaptador legacy RC1/V5."""
    df = _leer_csv_normalizado(file, aliases={
        'ident': 'ruta',
        'troncal': 'ruta',
        'distancia': 'distancia_m',
        'codigo_de_tramo': 'codigo_tramo',
        'numero_de_tramo': 'secuencia',
        'n_de_tramo': 'secuencia',
    })
    columnas = set(df.columns)
    discriminadoras = {
        'codigo_tramo', 'secuencia', 'origen_tipo', 'origen_codigo',
        'destino_tipo', 'destino_codigo',
    }
    if not (columnas & discriminadoras):
        return _procesar_tramos_legacy_validado(df, lote=lote)

    faltantes = []
    for columna in ('ruta', 'secuencia'):
        if columna not in columnas:
            faltantes.append(columna)
    if not columnas & {'origen', 'origen_codigo'}:
        faltantes.append('origen')
    if not columnas & {'destino', 'destino_codigo'}:
        faltantes.append('destino')
    if faltantes:
        raise ValueError(
            'Inventario técnico de tramos: faltan columnas obligatorias: '
            + ', '.join(faltantes)
        )

    rutas = {
        ruta.nombre.casefold(): ruta
        for ruta in Ruta.objects.all().only('pk', 'nombre')
    }
    preparadas = []
    errores = []
    codigos_archivo = set()
    secuencias_archivo = set()
    nombres_nodo_archivo = {}
    por_ruta = defaultdict(list)
    tipos_inferidos = {}

    def tipo_extremo(valor, tipo_informado):
        clave = valor.casefold()
        if tipo_informado:
            tipo = normalizar_tipo_nodo(tipo_informado)
            tipos_inferidos.setdefault(clave, tipo)
            return tipo
        if clave not in tipos_inferidos:
            tipos_inferidos[clave] = inferir_tipo_nodo(valor)
        return tipos_inferidos[clave]

    for indice, row in enumerate(df.to_dict('records'), start=2):
        ruta_nombre = _texto(row.get('ruta'))
        try:
            ruta = rutas.get(ruta_nombre.casefold()) if ruta_nombre else None
            if not ruta_nombre:
                raise ValueError('ruta es obligatoria')
            if not ruta:
                raise ValueError(f"la ruta '{ruta_nombre}' no existe")
            secuencia = _numero_no_negativo(
                row.get('secuencia'), 'secuencia'
            )
            if not secuencia:
                raise ValueError('secuencia debe ser mayor que cero')

            codigo_informado = bool(_texto(row.get('codigo_tramo')))
            codigo = (
                _texto(row.get('codigo_tramo')).upper()
                or codigo_tramo_automatico(secuencia)
            )
            origen_valor = (
                _texto(row.get('origen'))
                or _texto(row.get('origen_codigo'))
            )
            destino_valor = (
                _texto(row.get('destino'))
                or _texto(row.get('destino_codigo'))
            )
            if not origen_valor or not destino_valor:
                raise ValueError('origen y destino son obligatorios')
            origen_codigo = origen_valor.upper()
            destino_codigo = destino_valor.upper()

            origen_tipo_raw = _texto(row.get('origen_tipo'))
            destino_tipo_raw = _texto(row.get('destino_tipo'))
            origen_tipo = tipo_extremo(origen_valor, origen_tipo_raw)
            destino_tipo = tipo_extremo(destino_valor, destino_tipo_raw)
            if (
                origen_tipo == destino_tipo
                and origen_codigo.casefold() == destino_codigo.casefold()
            ):
                raise ValueError('el origen y el destino deben ser diferentes')

            capacidad = _capacidad_hilos(row.get('capacidad'))
            if capacidad == 0:
                raise ValueError(
                    'capacidad debe ser mayor que cero o quedar vacía'
                )
            cantidades = {
                'mufas': _numero_no_negativo(
                    row.get('mufas'), 'mufas', default=0
                ) if 'mufas' in columnas else None,
                'splitters': _numero_no_negativo(
                    row.get('splitters'), 'splitters', default=0
                ) if 'splitters' in columnas else None,
                'reservas_m': _numero_no_negativo(
                    row.get('reservas_m'), 'reservas_m',
                    entero=False, default=0.0,
                ) if 'reservas_m' in columnas else None,
                'hilos_ocupados': _numero_no_negativo(
                    row.get('hilos_ocupados'), 'hilos_ocupados'
                ) if 'hilos_ocupados' in columnas else None,
                'hilos_reservados': _numero_no_negativo(
                    row.get('hilos_reservados'), 'hilos_reservados'
                ) if 'hilos_reservados' in columnas else None,
                'hilos_libres': _numero_no_negativo(
                    row.get('hilos_libres'), 'hilos_libres'
                ) if 'hilos_libres' in columnas else None,
            }
            if capacidad is not None:
                suma = sum(
                    cantidades[campo] or 0
                    for campo in (
                        'hilos_ocupados', 'hilos_reservados', 'hilos_libres'
                    )
                )
                if suma > capacidad:
                    raise ValueError(
                        f'los contadores ({suma}) superan la capacidad '
                        f'({capacidad})'
                    )

            clave_codigo = (ruta.pk, codigo.casefold())
            clave_secuencia = (ruta.pk, secuencia)
            if clave_codigo in codigos_archivo:
                raise ValueError(f"codigo_tramo '{codigo}' repetido")
            if clave_secuencia in secuencias_archivo:
                raise ValueError(f'secuencia {secuencia} repetida')
            codigos_archivo.add(clave_codigo)
            secuencias_archivo.add(clave_secuencia)
            origen_nombre = (
                _texto(row.get('origen_nombre')) or origen_valor
            )
            destino_nombre = (
                _texto(row.get('destino_nombre')) or destino_valor
            )
            for tipo_nodo, codigo_nodo, nombre_nodo in (
                (origen_tipo, origen_codigo, origen_nombre),
                (destino_tipo, destino_codigo, destino_nombre),
            ):
                clave_nodo = (tipo_nodo, codigo_nodo.casefold())
                nombre_anterior = nombres_nodo_archivo.get(clave_nodo)
                if (
                    nombre_anterior
                    and nombre_nodo
                    and nombre_anterior.casefold() != nombre_nodo.casefold()
                ):
                    raise ValueError(
                        f"el nodo {tipo_nodo}/{codigo_nodo} tiene nombres "
                        'incoherentes dentro del archivo'
                    )
                if nombre_nodo:
                    nombres_nodo_archivo[clave_nodo] = nombre_nodo
                else:
                    nombres_nodo_archivo.setdefault(clave_nodo, None)
            preparada = {
                'fila': indice,
                'ruta': ruta,
                'codigo_tramo': codigo,
                'codigo_informado': codigo_informado,
                'secuencia': secuencia,
                'origen_tipo': origen_tipo,
                'origen_codigo': origen_codigo,
                'origen_nombre': origen_nombre,
                'destino_tipo': destino_tipo,
                'destino_codigo': destino_codigo,
                'destino_nombre': destino_nombre,
                'estado': _texto(row.get('estado')) or None,
                'distancia_m': _numero_no_negativo(
                    row.get('distancia_m'), 'distancia_m',
                    entero=False, default=None,
                ),
                'capacidad_hilos': capacidad,
                'tipo_fibra': _texto(row.get('tipo_fibra')) or None,
                'marca_modelo': _texto(row.get('marca_modelo')) or None,
                'serial': _texto(row.get('serial')) or None,
                'observaciones': _texto(row.get('observaciones')),
                'tipo_trazado': (
                    _texto(row.get('tipo_trazado')).upper() or None
                ),
                **cantidades,
            }
            preparadas.append(preparada)
            por_ruta[ruta.pk].append(preparada)
        except Exception as exc:
            errores.append(f'fila {indice}: {exc}')

    for filas in por_ruta.values():
        ordenadas = sorted(filas, key=lambda item: item['secuencia'])
        secuencias = [item['secuencia'] for item in ordenadas]
        if secuencias != list(range(1, max(secuencias) + 1)):
            errores.append(
                f"ruta '{ordenadas[0]['ruta'].nombre}': secuencias "
                f'{secuencias} no consecutivas'
            )
        for anterior, siguiente in zip(ordenadas, ordenadas[1:]):
            if (
                anterior['destino_tipo'],
                anterior['destino_codigo'].casefold(),
            ) != (
                siguiente['origen_tipo'],
                siguiente['origen_codigo'].casefold(),
            ):
                errores.append(
                    f"ruta '{anterior['ruta'].nombre}': el destino de "
                    f"{anterior['codigo_tramo']} no coincide con el origen "
                    f"de {siguiente['codigo_tramo']}"
                )
    _errores_csv(errores, 'Inventario técnico de tramos')

    ids_ruta = sorted(por_ruta)
    list(Ruta.objects.select_for_update().filter(pk__in=ids_ruta))
    existentes = list(
        InventarioTramo.objects.select_for_update()
        .filter(ruta_id__in=ids_ruta)
        .order_by('ruta_id', 'tramo_secuencia', 'pk')
    )
    por_ruta_existentes = defaultdict(list)
    for tramo in existentes:
        por_ruta_existentes[tramo.ruta_id].append(tramo)

    objetivos = {}
    objetivos_ids = set()
    for item in preparadas:
        candidatos = por_ruta_existentes[item['ruta'].pk]
        objetivo = None
        if item['codigo_informado']:
            objetivo = next(
                (
                    tramo for tramo in candidatos
                    if tramo.codigo_tramo
                    and tramo.codigo_tramo.casefold()
                    == item['codigo_tramo'].casefold()
                ),
                None,
            )
        if objetivo is None:
            objetivo = next(
                (
                    tramo for tramo in candidatos
                    if tramo.tramo_secuencia == item['secuencia']
                    and (
                        not item['codigo_informado']
                        or not tramo.codigo_tramo
                    )
                ),
                None,
            )
        objetivos[item['fila']] = objetivo
        if objetivo:
            objetivos_ids.add(objetivo.pk)

    conflictos = []
    for ruta_id, tramos_existentes in por_ruta_existentes.items():
        omitidos = [
            tramo for tramo in tramos_existentes
            if tramo.pk not in objetivos_ids
        ]
        if omitidos:
            ruta = por_ruta[ruta_id][0]['ruta']
            conflictos.append(
                f"ruta '{ruta.nombre}': el archivo omite tramos existentes "
                + ', '.join(
                    tramo.codigo_tramo or f'secuencia {tramo.tramo_secuencia}'
                    for tramo in omitidos
                )
                + '; la Fase 2 exige el snapshot técnico completo de cada '
                'ruta incluida'
            )
    for item in preparadas:
        objetivo = objetivos[item['fila']]
        conflicto = next(
            (
                tramo
                for tramo in por_ruta_existentes[item['ruta'].pk]
                if tramo.tramo_secuencia == item['secuencia']
                and tramo.pk != getattr(objetivo, 'pk', None)
                and tramo.pk not in objetivos_ids
            ),
            None,
        )
        if conflicto:
            conflictos.append(
                f"fila {item['fila']}: secuencia {item['secuencia']} "
                f"ocupada por '{conflicto.codigo_tramo or conflicto.pk}'"
            )
        tiene_detalle = (
            objetivo is not None
            and FibraTramo.objects.filter(tramo=objetivo).exists()
        )
        if item['capacidad_hilos'] is not None and objetivo is not None:
            excedidas = []
            for numero_hilo in FibraTramo.objects.filter(
                tramo=objetivo
            ).values_list('numero_hilo', flat=True):
                coincidencia = re.fullmatch(
                    r'F([1-9]\d*)', (numero_hilo or '').strip().upper()
                )
                if (
                    coincidencia
                    and int(coincidencia.group(1))
                    > item['capacidad_hilos']
                ):
                    excedidas.append(numero_hilo)
            if excedidas:
                conflictos.append(
                    f"fila {item['fila']}: la capacidad "
                    f"{item['capacidad_hilos']} dejaría fuera "
                    f"{', '.join(excedidas[:10])}"
                )

        if tiene_detalle:
            conteos_finales = {
                fila['estado']: fila['total']
                for fila in FibraTramo.objects.filter(tramo=objetivo)
                .values('estado')
                .annotate(total=Count('pk'))
            }
            suma_final = sum(
                conteos_finales.get(estado, 0)
                for estado in ('OCUPADO', 'RESERVADO', 'DISPONIBLE')
            )
        else:
            suma_final = 0
            for campo in (
                'hilos_ocupados', 'hilos_reservados', 'hilos_libres'
            ):
                if campo in columnas:
                    valor = item[campo]
                elif objetivo is not None:
                    valor = getattr(objetivo, campo)
                else:
                    valor = 0
                suma_final += valor or 0
        if (
            item['capacidad_hilos'] is not None
            and suma_final > item['capacidad_hilos']
        ):
            conflictos.append(
                f"fila {item['fila']}: el estado final conservaría "
                f'{suma_final} hilos contabilizados para una capacidad de '
                f"{item['capacidad_hilos']}"
            )
    _errores_csv(conflictos, 'Inventario técnico de tramos')

    max_secuencia = max(
        [tramo.tramo_secuencia for tramo in existentes] + [0]
    )
    for posicion, tramo in enumerate(
        [item for item in existentes if item.pk in objetivos_ids],
        start=1,
    ):
        InventarioTramo.objects.filter(pk=tramo.pk).update(
            tramo_secuencia=max_secuencia + 1000 + posicion
        )

    creadas = actualizadas = 0
    advertencias = []
    nodos_resueltos = {}
    for item in sorted(
        preparadas,
        key=lambda fila: (fila['ruta'].pk, fila['secuencia']),
    ):
        tramo = objetivos[item['fila']]
        creada = tramo is None
        if creada:
            tramo = InventarioTramo(ruta=item['ruta'])
        if creada or item['codigo_informado'] or not tramo.codigo_tramo:
            tramo.codigo_tramo = item['codigo_tramo']
        else:
            # Una recarga simplificada identifica por ruta + secuencia y no
            # reemplaza un código interno estable que ya exista.
            item['codigo_tramo'] = tramo.codigo_tramo
        tramo.tramo_secuencia = item['secuencia']
        clave_origen = (
            item['origen_tipo'],
            item['origen_codigo'].casefold(),
        )
        if clave_origen not in nodos_resueltos:
            nodos_resueltos[clave_origen] = resolver_nodo(
                tipo=item['origen_tipo'],
                codigo=item['origen_codigo'],
                nombre=nombres_nodo_archivo.get(clave_origen),
                lote=lote,
            )
        tramo.origen_nodo = nodos_resueltos[clave_origen]
        clave_destino = (
            item['destino_tipo'],
            item['destino_codigo'].casefold(),
        )
        if clave_destino not in nodos_resueltos:
            nodos_resueltos[clave_destino] = resolver_nodo(
                tipo=item['destino_tipo'],
                codigo=item['destino_codigo'],
                nombre=nombres_nodo_archivo.get(clave_destino),
                lote=lote,
            )
        tramo.destino_nodo = nodos_resueltos[clave_destino]
        tramo.origen = item['origen_codigo']
        tramo.destino = item['destino_codigo']
        tramo.estado = item['estado']
        tramo.distancia_m = item['distancia_m']
        tramo.capacidad_hilos = item['capacidad_hilos']
        tramo.capacidad = (
            f"{item['capacidad_hilos']} Hilos"
            if item['capacidad_hilos'] is not None else None
        )
        tramo.tipo_fibra = item['tipo_fibra']
        tramo.marca_modelo = item['marca_modelo']
        tramo.serial = item['serial']
        tramo.observaciones = item['observaciones']
        tramo.lote_importacion = lote
        for campo in ('tipo_trazado', 'mufas', 'splitters', 'reservas_m'):
            if campo in columnas:
                setattr(tramo, campo, item[campo])

        tiene_detalle = (
            not creada and FibraTramo.objects.filter(tramo=tramo).exists()
        )
        if tiene_detalle:
            if columnas & {
                'hilos_ocupados', 'hilos_reservados', 'hilos_libres',
            }:
                advertencias.append(
                    f"{item['ruta'].nombre}/{item['codigo_tramo']}: "
                    'se conservaron los contadores calculados desde fibras.'
                )
        else:
            for campo in (
                'hilos_ocupados', 'hilos_reservados', 'hilos_libres'
            ):
                if campo in columnas:
                    setattr(tramo, campo, item[campo])
        tramo.save()
        creadas += int(creada)
        actualizadas += int(not creada)
        if lote is not None:
            Ruta.objects.filter(pk=item['ruta'].pk).update(
                lote_importacion=lote
            )

    resultado = _resultado(len(df), creadas, actualizadas)
    if advertencias:
        resultado['advertencias'] = sorted(set(advertencias))
    return resultado


@transaction.atomic
def _procesar_fibras_inventario(file, lote=None):
    """Actualiza exclusivamente la posición/estado de fibras por tramo."""
    df = _leer_csv_normalizado(file, aliases={
        'troncal': 'ruta',
        'codigo_de_tramo': 'codigo_tramo',
        'numero_de_tramo': 'secuencia',
        'n_de_tramo': 'secuencia',
        'codigo_de_fibra': 'codigo_fibra',
        'numero_de_fibra': 'fibra',
        'n_de_fibra': 'fibra',
    })
    requeridas = {'ruta', 'fibra', 'codigo_fibra', 'estado'}
    faltantes = sorted(requeridas - set(df.columns))
    if faltantes:
        raise ValueError(
            'Detalle de fibras por tramo: faltan columnas obligatorias: '
            + ', '.join(faltantes)
        )

    rutas = {
        ruta.nombre.casefold(): ruta
        for ruta in Ruta.objects.all().only('pk', 'nombre')
    }
    tramos = list(
        InventarioTramo.objects.filter(
            ruta_id__in=[
                ruta.pk for ruta in rutas.values()
                if ruta.nombre.casefold() in {
                    _texto(nombre).casefold()
                    for nombre in df['ruta']
                    if _texto(nombre)
                }
            ]
        ).select_related('ruta')
    )
    tramos_por_ruta = defaultdict(list)
    for tramo in tramos:
        tramos_por_ruta[tramo.ruta_id].append(tramo)
    for ruta_id in tramos_por_ruta:
        tramos_por_ruta[ruta_id].sort(
            key=lambda item: (item.tramo_secuencia, item.pk)
        )

    preparadas = []
    errores = []
    claves_fisicas = set()
    claves_logicas_tramo = set()
    for indice, row in enumerate(df.to_dict('records'), start=2):
        ruta_nombre = _texto(row.get('ruta'))
        numero_hilo = _texto(row.get('fibra')).upper()
        codigo_estable = _texto(row.get('codigo_fibra')).upper()
        try:
            ruta = rutas.get(ruta_nombre.casefold()) if ruta_nombre else None
            if not ruta_nombre:
                raise ValueError('ruta es obligatoria')
            if not ruta:
                raise ValueError(f"la ruta '{ruta_nombre}' no existe")
            if not re.fullmatch(r'F[1-9]\d*', numero_hilo):
                raise ValueError(
                    f"Fibra '{numero_hilo or '[vacía]'}' debe usar F<n>"
                )
            if not codigo_estable:
                raise ValueError('Codigo Fibra es obligatorio')
            if re.fullmatch(r'F[1-9]\d*', codigo_estable):
                raise ValueError(
                    'Codigo Fibra debe ser una identidad global estable; '
                    f"no use la posición física {codigo_estable}"
                )
            if len(codigo_estable) > 64:
                raise ValueError('Codigo Fibra no puede superar 64 caracteres')

            estado = ESTADOS_FIBRA_CSV.get(
                _texto(row.get('estado')).casefold()
            )
            if not estado:
                raise ValueError(
                    f"estado '{_texto(row.get('estado'))}' no válido; "
                    'use Disponible, Ocupado, Reservado o Sin información'
                )

            codigo_tramo = _texto(row.get('codigo_tramo')).upper()
            secuencia = None
            if _texto(row.get('secuencia')):
                secuencia = _numero_no_negativo(
                    row.get('secuencia'), 'secuencia'
                )
                if not secuencia:
                    raise ValueError('secuencia debe ser mayor que cero')
            candidatos = tramos_por_ruta[ruta.pk]
            if secuencia is not None:
                tramo = next(
                    (
                        item for item in candidatos
                        if item.tramo_secuencia == secuencia
                    ),
                    None,
                )
                if not tramo:
                    raise ValueError(
                        f'la secuencia {secuencia} no existe en la ruta'
                    )
                if (
                    codigo_tramo
                    and tramo.codigo_tramo
                    and tramo.codigo_tramo.casefold()
                    != codigo_tramo.casefold()
                ):
                    raise ValueError(
                        f"la secuencia {secuencia} corresponde a "
                        f"'{tramo.codigo_tramo}', no a '{codigo_tramo}'"
                    )
            elif codigo_tramo:
                tramo = next(
                    (
                        item for item in candidatos
                        if item.codigo_tramo
                        and item.codigo_tramo.casefold()
                        == codigo_tramo.casefold()
                    ),
                    None,
                )
                if not tramo:
                    raise ValueError(
                        f"el tramo '{codigo_tramo}' no existe en la ruta"
                    )
            elif len(candidatos) == 1:
                tramo = candidatos[0]
            elif not candidatos:
                raise ValueError('la ruta no tiene inventario técnico')
            else:
                raise ValueError(
                    'Secuencia o Codigo Tramo es obligatorio porque la ruta '
                    f'tiene {len(candidatos)} tramos'
                )

            if (
                tramo.capacidad_hilos is not None
                and int(numero_hilo[1:]) > tramo.capacidad_hilos
            ):
                raise ValueError(
                    f'{numero_hilo} supera la capacidad '
                    f'{tramo.capacidad_hilos} de '
                    f'{tramo.codigo_tramo or tramo.pk}'
                )
            clave_fisica = (tramo.pk, numero_hilo.casefold())
            if clave_fisica in claves_fisicas:
                raise ValueError(
                    f'{numero_hilo} está repetida en el mismo tramo'
                )
            claves_fisicas.add(clave_fisica)
            clave_logica_tramo = (tramo.pk, codigo_estable.casefold())
            if clave_logica_tramo in claves_logicas_tramo:
                raise ValueError(
                    f"Codigo Fibra '{codigo_estable}' está repetido "
                    'en el mismo tramo'
                )
            claves_logicas_tramo.add(clave_logica_tramo)
            preparadas.append({
                'fila': indice,
                'ruta': ruta,
                'tramo': tramo,
                'numero_hilo': numero_hilo,
                'codigo_estable': codigo_estable,
                'estado': estado,
                'observaciones': (
                    _texto(row.get('observaciones'))
                    if 'observaciones' in df.columns else None
                ),
            })
        except Exception as exc:
            errores.append(f'fila {indice}: {exc}')
    _errores_csv(errores, 'Detalle de fibras por tramo')

    ids_tramo = sorted({item['tramo'].pk for item in preparadas})
    fibras_consultadas_por_codigo = {
        fibra.codigo_fibra.casefold(): fibra
        for fibra in InventarioFibra.objects.filter(
            codigo_fibra__in=[
                item['codigo_estable']
                for item in preparadas
                if item['codigo_estable']
            ]
        )
    }
    faltan_fibras = []
    rutas_por_fibra = defaultdict(set)
    for item in preparadas:
        fibra = fibras_consultadas_por_codigo.get(
            item['codigo_estable'].casefold()
        )
        if fibra and fibra.ruta_id not in (None, item['ruta'].pk):
            faltan_fibras.append(
                f"fila {item['fila']}: el Codigo Fibra "
                f"{item['codigo_estable']} pertenece a otra ruta"
            )
            continue
        item['fibra_consultada'] = fibra
        if fibra is None:
            faltan_fibras.append(
                f"fila {item['fila']}: la fibra global "
                f"{item['ruta'].nombre} / {item['codigo_estable']} no existe; "
                'cárguela previamente en el inventario de fibras'
            )
        else:
            rutas_por_fibra[fibra.pk].add(item['ruta'].pk)
    for fibra_id, rutas_objetivo in rutas_por_fibra.items():
        if len(rutas_objetivo) > 1:
            fibra = next(
                item for item in fibras_consultadas_por_codigo.values()
                if item.pk == fibra_id
            )
            faltan_fibras.append(
                f"el Codigo Fibra {fibra.codigo_fibra} aparece en más de "
                'una ruta dentro del archivo'
            )
    _errores_csv(faltan_fibras, 'Detalle de fibras por tramo')

    # Asociar una fibra provisional a sus tramos es precisamente el momento
    # en que queda confirmada su troncal. La identidad global no cambia.
    from ..services.fibras import asignar_ruta_fibra

    fibras_asignadas = {}
    for item in preparadas:
        fibra = item['fibra_consultada']
        if fibra.pk in fibras_asignadas:
            item['fibra_consultada'] = fibras_asignadas[fibra.pk]
            continue
        if fibra.ruta_id is None:
            fibra, _, _ = asignar_ruta_fibra(
                fibra=fibra,
                ruta=item['ruta'],
                usuario=getattr(lote, 'usuario', None) if lote else None,
                origen='EXCEL',
                lote_importacion=lote,
            )
        fibras_asignadas[fibra.pk] = fibra
        item['fibra_consultada'] = fibra

    ids_fibra = sorted({
        item['fibra_consultada'].pk for item in preparadas
    })
    fibras_bloqueadas = {
        fibra.pk: fibra
        for fibra in InventarioFibra.objects.select_for_update()
        .filter(pk__in=ids_fibra)
        .order_by('pk')
    }
    tramos_bloqueados = {
        tramo.pk: tramo
        for tramo in InventarioTramo.objects.select_for_update().filter(
            pk__in=ids_tramo
        ).order_by('pk')
    }
    fibras_existentes_por_codigo = {
        fibra.codigo_fibra.casefold(): fibra
        for fibra in fibras_bloqueadas.values()
    }
    asignaciones = list(
        FibraTramo.objects.select_for_update()
        .filter(tramo_id__in=ids_tramo)
        .select_related('fibra')
        .order_by('pk')
    )
    asignaciones_por_posicion = {
        (item.tramo_id, item.numero_hilo.casefold()): item
        for item in asignaciones
    }
    asignaciones_por_logica = {
        (item.tramo_id, item.fibra_id): item
        for item in asignaciones
        if item.fibra_id
    }

    conflictos = []
    for item in preparadas:
        fibra = fibras_existentes_por_codigo.get(
            item['codigo_estable'].casefold()
        )
        posicion = asignaciones_por_posicion.get(
            (item['tramo'].pk, item['numero_hilo'].casefold())
        )
        if (
            posicion
            and posicion.fibra_id
            and (
                fibra is None
                or posicion.fibra_id != fibra.pk
            )
        ):
            conflictos.append(
                f"fila {item['fila']}: {item['numero_hilo']} ya pertenece "
                'a otra fibra lógica'
            )
    _errores_csv(conflictos, 'Detalle de fibras por tramo')

    asignaciones_creadas = asignaciones_actualizadas = 0
    fibras_tocadas = {}
    for item in preparadas:
        tramo = tramos_bloqueados[item['tramo'].pk]
        fibra = fibras_existentes_por_codigo.get(
            item['codigo_estable'].casefold()
        )
        fibras_tocadas[fibra.pk] = fibra

        clave_posicion = (
            tramo.pk, item['numero_hilo'].casefold()
        )
        asignacion = asignaciones_por_posicion.get(clave_posicion)
        clave_asignacion_logica = (
            tramo.pk,
            fibra.pk,
        )
        asignacion_logica = asignaciones_por_logica.get(
            clave_asignacion_logica
        )
        if asignacion is None:
            if asignacion_logica is not None:
                clave_anterior = (
                    tramo.pk,
                    asignacion_logica.numero_hilo.casefold(),
                )
                asignaciones_por_posicion.pop(clave_anterior, None)
                asignacion = asignacion_logica
                asignacion.numero_hilo = item['numero_hilo']
            else:
                asignacion = FibraTramo(
                    tramo=tramo,
                    numero_hilo=item['numero_hilo'],
                )
        elif (
            asignacion_logica is not None
            and asignacion_logica.pk != asignacion.pk
        ):
            asignacion_logica.fibra = None
            asignacion_logica.estado = 'SIN_INFORMACION'
            asignacion_logica._omitir_sincronizacion_estado = True
            asignacion_logica.save(
                update_fields=['fibra', 'estado']
            )
        creada = asignacion.pk is None
        asignacion.fibra = fibra
        asignacion.estado = item['estado']
        if item['observaciones'] is not None:
            asignacion.observaciones = item['observaciones']
        asignacion.lote_importacion = lote
        asignacion._omitir_sincronizacion_estado = True
        asignacion.save()
        asignaciones_creadas += int(creada)
        asignaciones_actualizadas += int(not creada)
        asignaciones_por_posicion[clave_posicion] = asignacion
        asignaciones_por_logica[clave_asignacion_logica] = asignacion

    from ..services.fibras import sincronizar_estado_fibra

    for fibra in fibras_tocadas.values():
        if fibra.origen_estado != 'INFORMADO':
            sincronizar_estado_fibra(
                fibra,
                origen='EXCEL',
                lote_importacion=lote,
                causa='IMPORTACION_FIBRA_TRAMO',
            )

    for tramo_id in ids_tramo:
        conteos = {
            fila['estado']: fila['total']
            for fila in FibraTramo.objects.filter(tramo_id=tramo_id)
            .values('estado')
            .annotate(total=Count('pk'))
        }
        InventarioTramo.objects.filter(pk=tramo_id).update(
            hilos_ocupados=conteos.get('OCUPADO', 0),
            hilos_reservados=conteos.get('RESERVADO', 0),
            hilos_libres=conteos.get('DISPONIBLE', 0),
        )

    resultado = _resultado(
        len(df),
        creadas=asignaciones_creadas,
        actualizadas=asignaciones_actualizadas,
    )
    resultado['modo'] = 'parche'
    resultado['informaciones'] = [
        'La carga se aplicó en modo parche exclusivamente sobre FibraTramo; '
        'no creó fibras globales ni modificó sus metadatos.'
    ]
    return resultado


TERMINACIONES_ALIASES_CSV = {
    'troncal': 'ruta',
    'ruta_troncal': 'ruta',
    'hilo': 'fibra',
    'numero_hilo': 'fibra',
    'fibra_logica': 'codigo_fibra',
    'tipo_conector': 'conector',
    'tipo_de_conector': 'conector',
    'id_fibra': 'fibra_id',
    'estado_global': 'estado',
    'estado_de_uso': 'estado',
    'condicion_fisica': 'condicion',
    'condicion': 'condicion',
    'tipo_de_servicio': 'servicio',
    'nombre_servicio': 'servicio',
    'notas': 'observaciones',
    'puerto_odf': 'puerto',
    'sitio': 'site',
    'hub_site': 'site',
}


@transaction.atomic
def _procesar_terminaciones_fibra(file, lote=None):
    """Registra terminaciones oficiales con la troncal informada o pendiente.

    Codigo Fibra aporta siempre la identidad estable para crear o reconciliar
    la fibra. Ruta puede quedar pendiente. F1/F2 representa solo la posición
    física y nunca identifica por sí sola una fibra global.
    """
    from ..services.fibras import (
        establecer_estado_fibra_informado,
        normalizar_condicion_fisica,
        normalizar_estado_fibra,
        restablecer_estado_fibra,
    )
    from ..services.puertos import conectar_puertos_masivo

    df = _leer_csv_normalizado(file, aliases=TERMINACIONES_ALIASES_CSV)
    faltantes = sorted(
        {'fibra', 'codigo_fibra', 'extremo', 'odf', 'puerto'}
        - set(df.columns)
    )
    if faltantes:
        raise ValueError(
            'Terminaciones de fibra: faltan columnas obligatorias: '
            + ', '.join(faltantes)
        )

    rutas = {ruta.nombre.casefold(): ruta for ruta in Ruta.objects.all()}
    fibras_por_id = {
        fibra.pk: fibra
        for fibra in InventarioFibra.objects.select_related('ruta').all()
    }
    fibras_por_codigo = {
        fibra.codigo_fibra.casefold(): fibra
        for fibra in fibras_por_id.values()
    }
    fibras_por_ruta = {
        (fibra.ruta_id, fibra.fibra_numero.casefold()): fibra
        for fibra in InventarioFibra.objects.select_related('ruta').filter(
            ruta__isnull=False
        )
    }
    odfs = {
        odf.odf.casefold(): odf
        for odf in InventarioODF.objects.select_related(
            'rack_obj__sala__hub_site'
        )
    }

    preparadas = []
    errores = []
    claves_puerto = set()
    for numero_fila, row in enumerate(df.to_dict('records'), start=2):
        try:
            ruta_nombre = _texto(row.get('ruta'))
            ruta = None
            if ruta_nombre:
                ruta = rutas.get(ruta_nombre.casefold())
                if not ruta:
                    raise ValueError(f"la ruta '{ruta_nombre}' no existe")

            numero_fibra = _texto(row.get('fibra')).upper()
            codigo_fibra = _texto(row.get('codigo_fibra')).upper()
            if not numero_fibra:
                raise ValueError('Fibra es obligatoria')
            if codigo_fibra and re.fullmatch(r'F[1-9]\d*', codigo_fibra):
                raise ValueError(
                    'Codigo Fibra debe ser una identidad global estable; '
                    f'no use la posición física {codigo_fibra}'
                )
            if len(numero_fibra) > 50:
                raise ValueError('Fibra no puede superar 50 caracteres')
            if len(codigo_fibra) > 64:
                raise ValueError('Codigo Fibra no puede superar 64 caracteres')

            extremos = []
            extremo_informado = _texto(row.get('extremo')).upper()
            if not extremo_informado:
                raise ValueError('Extremo es obligatorio')
            if extremo_informado not in {'A', 'B'}:
                raise ValueError('Extremo debe ser A o B')
            for extremo in (extremo_informado,):
                odf_nombre = _texto(row.get('odf'))
                puerto_numero = _texto(row.get('puerto')).upper()
                conector = _texto(row.get('conector'))
                site_informado = _texto(row.get('site'))
                if bool(odf_nombre) != bool(puerto_numero):
                    raise ValueError(
                        f'el extremo {extremo} debe declarar juntos ODF y puerto'
                    )
                if not odf_nombre:
                    continue
                odf = odfs.get(odf_nombre.casefold())
                if not odf:
                    raise ValueError(
                        f"el ODF del extremo {extremo} '{odf_nombre}' no existe"
                    )
                site_real = nombre_site(odf)
                if (
                    site_informado
                    and site_informado.casefold() != site_real.casefold()
                ):
                    raise ValueError(
                        f"el Site '{site_informado}' no corresponde al ODF "
                        f"{odf.odf}; pertenece a '{site_real}'"
                    )
                clave = (odf.pk, puerto_numero.casefold())
                if clave in claves_puerto:
                    raise ValueError(
                        f'el puerto {odf.odf} / {puerto_numero} está repetido '
                        'en el archivo'
                    )
                claves_puerto.add(clave)
                extremos.append({
                    'extremo': extremo,
                    'odf': odf,
                    'puerto_numero': puerto_numero,
                    'conector': conector,
                    'site': site_real,
                    'clave': clave,
                })
            if not extremos:
                raise ValueError(
                    'debe declarar al menos un extremo completo (ODF y puerto)'
                )
            fibra_id_texto = _texto(row.get('fibra_id'))
            fibra_id = None
            if fibra_id_texto:
                try:
                    fibra_id = int(float(fibra_id_texto))
                except (TypeError, ValueError):
                    raise ValueError('ID Fibra debe ser numérico')
                fibra_id_obj = fibras_por_id.get(fibra_id)
                if not fibra_id_obj:
                    raise ValueError(f'la fibra ID {fibra_id} no existe')
                if fibra_id_obj.fibra_numero.casefold() != numero_fibra.casefold():
                    raise ValueError(
                        f'el ID {fibra_id} pertenece a {fibra_id_obj.fibra_numero}'
                    )
                if (
                    codigo_fibra
                    and fibra_id_obj.codigo_fibra.casefold()
                    != codigo_fibra.casefold()
                ):
                    raise ValueError(
                        f'el ID {fibra_id} pertenece al código estable '
                        f'{fibra_id_obj.codigo_fibra}'
                    )
                if ruta and fibra_id_obj.ruta_id not in {None, ruta.pk}:
                    raise ValueError('el ID Fibra pertenece a otra troncal')

            estado_texto = _texto(row.get('estado')) if 'estado' in df.columns else ''
            estado_informado = None
            restablecer_estado = False
            condicion_informada = None
            if estado_texto:
                normalizado = estado_texto.casefold()
                if normalizado in {'malo', 'mala'}:
                    condicion_informada = 'CON_FALLA'
                    restablecer_estado = True
                elif normalizado in {'sin informacion', 'sin información'}:
                    restablecer_estado = True
                else:
                    estado_informado = normalizar_estado_fibra(estado_texto)
            condicion_texto = (
                _texto(row.get('condicion')) if 'condicion' in df.columns else ''
            )
            if condicion_texto:
                condicion_informada = normalizar_condicion_fisica(condicion_texto)

            preparadas.append({
                'fila': numero_fila,
                'ruta': ruta,
                'ruta_nombre': ruta_nombre,
                'numero': numero_fibra,
                'codigo': codigo_fibra,
                'extremos': extremos,
                'fibra_id': fibra_id,
                'estado_informado': estado_informado,
                'restablecer_estado': restablecer_estado,
                'condicion_informada': condicion_informada,
                'servicio': (
                    _texto(row.get('servicio'))
                    if 'servicio' in df.columns else None
                ),
                'observaciones': (
                    _observacion_global_desde_terminacion(
                        row.get('observaciones')
                    )
                    if 'observaciones' in df.columns else None
                ),
            })
        except Exception as exc:
            errores.append(f'fila {numero_fila}: {exc}')
    _errores_csv(errores, 'Terminaciones de fibra')
    _reportar_progreso(
        28,
        'Filas interpretadas; verificando ODF y puertos',
        procesadas=len(preparadas),
        total=len(df),
    )

    odf_ids = sorted({clave[0] for clave in claves_puerto})
    puertos = {}
    for lote_odf_ids in _dividir_en_lotes(odf_ids):
        for puerto in (
            DetallePuertoODF.objects
            .select_related('odf_obj__rack_obj__sala__hub_site')
            .filter(odf_obj_id__in=lote_odf_ids)
            .iterator()
        ):
            puertos[
                (puerto.odf_obj_id, puerto.puerto_odf.casefold())
            ] = puerto
    errores = []
    for item in preparadas:
        for extremo in item['extremos']:
            puerto = puertos.get(extremo['clave'])
            if not puerto:
                errores.append(
                    f"fila {item['fila']}: el puerto "
                    f"{extremo['odf'].odf} / {extremo['puerto_numero']} no existe"
                )
            else:
                extremo['puerto'] = puerto
    _errores_csv(errores, 'Terminaciones de fibra')
    _reportar_progreso(
        38,
        'ODF y puertos verificados; comprobando terminaciones existentes',
        procesadas=len(preparadas),
        total=len(df),
    )

    terminaciones_objetivo = list(
        TerminacionFibra.objects
        .select_related('fibra__ruta', 'puerto_odf__odf_obj')
        .all()
        .iterator()
    )
    por_puerto = {
        terminacion.puerto_odf_id: terminacion
        for terminacion in terminaciones_objetivo
    }
    por_fibra_extremo = {
        (terminacion.fibra_id, terminacion.extremo): terminacion
        for terminacion in terminaciones_objetivo
    }

    errores = []
    extremos_archivo = set()
    grupos_globales = {}
    identidades_grupo = {}
    grupos_pendientes = set()
    for item in preparadas:
        fibra = fibras_por_id.get(item['fibra_id']) if item['fibra_id'] else None
        if fibra is None and item['codigo']:
            fibra = fibras_por_codigo.get(item['codigo'].casefold())
            if fibra and fibra.fibra_numero.casefold() != item['numero'].casefold():
                errores.append(
                    f"fila {item['fila']}: el código estable "
                    f"{item['codigo']} pertenece al hilo {fibra.fibra_numero}, "
                    f"no a {item['numero']}"
                )
                continue
            if (
                fibra
                and item['ruta']
                and fibra.ruta_id not in {None, item['ruta'].pk}
            ):
                errores.append(
                    f"fila {item['fila']}: el código estable "
                    f"{item['codigo']} pertenece a otra troncal"
                )
                continue
        if fibra is None and item['ruta'] is not None:
            fibra = fibras_por_ruta.get(
                (item['ruta'].pk, item['numero'].casefold())
            )
            if (
                fibra
                and item['codigo']
                and fibra.codigo_fibra.casefold() != item['codigo'].casefold()
            ):
                errores.append(
                    f"fila {item['fila']}: {item['ruta'].nombre} / "
                    f"{item['numero']} ya existe con el código estable "
                    f"{fibra.codigo_fibra}, no {item['codigo']}"
                )
                continue
        if fibra is None and item['ruta'] is None:
            candidatas = {
                por_puerto[extremo['puerto'].pk].fibra
                for extremo in item['extremos']
                if extremo['puerto'].pk in por_puerto
            }
            if len(candidatas) > 1:
                errores.append(
                    f"fila {item['fila']}: los puertos declarados pertenecen "
                    'a fibras diferentes'
                )
                continue
            if candidatas:
                fibra = next(iter(candidatas))
                if fibra.fibra_numero.casefold() != item['numero'].casefold():
                    errores.append(
                        f"fila {item['fila']}: el puerto ya pertenece al hilo "
                        f"{fibra.fibra_numero}, no a {item['numero']}"
                    )
                    continue
        if (
            fibra
            and item['codigo']
            and fibra.codigo_fibra.casefold() != item['codigo'].casefold()
        ):
            errores.append(
                f"fila {item['fila']}: la terminación existente pertenece al "
                f"código estable {fibra.codigo_fibra}, no {item['codigo']}"
            )
            continue
        item['fibra'] = fibra
        if fibra:
            grupo = ('fibra', fibra.pk)
        elif item['fibra_id']:
            grupo = ('fibra', item['fibra_id'])
        elif item['codigo']:
            grupo = ('codigo', item['codigo'].casefold())
            if item['ruta'] is None:
                grupos_pendientes.add(grupo)
        elif item['ruta']:
            grupo = ('ruta', item['ruta'].pk, item['numero'].casefold())
        else:
            grupo = (
                'puertos',
                tuple(sorted(extremo['clave'] for extremo in item['extremos'])),
            )
        item['grupo'] = grupo

        identidad = (
            item['numero'].casefold(),
            item['ruta'].pk if item['ruta'] else None,
        )
        identidad_anterior = identidades_grupo.get(grupo)
        if identidad_anterior is not None and identidad_anterior != identidad:
            errores.append(
                f"fila {item['fila']}: el mismo Codigo Fibra no puede declarar "
                'otro número de hilo o una troncal diferente'
            )
            continue
        identidades_grupo[grupo] = identidad

        globales = grupos_globales.setdefault(grupo, {})
        estado_operacion = None
        if item['estado_informado']:
            estado_operacion = ('INFORMADO', item['estado_informado'])
        elif item['restablecer_estado']:
            estado_operacion = ('RESTABLECER', 'SIN_INFORMACION')
        valores_globales = {
            'estado_operacion': estado_operacion,
            'condicion_informada': item['condicion_informada'],
            'servicio': item['servicio'],
            'observaciones': item['observaciones'],
        }
        for campo, valor in valores_globales.items():
            if valor is None:
                continue
            if (
                campo in globales
                and str(globales[campo]).casefold() != str(valor).casefold()
            ):
                errores.append(
                    f"fila {item['fila']}: {campo.replace('_', ' ')} "
                    'contradice otra fila A/B de la misma fibra'
                )
            else:
                globales[campo] = valor

        for extremo in item['extremos']:
            clave_extremo = (grupo, extremo['extremo'])
            if clave_extremo in extremos_archivo:
                errores.append(
                    f"fila {item['fila']}: el extremo {extremo['extremo']} "
                    'de la fibra está repetido en el archivo'
                )
                continue
            extremos_archivo.add(clave_extremo)
            ocupante = por_puerto.get(extremo['puerto'].pk)
            if ocupante and (
                fibra is None
                or ocupante.fibra_id != fibra.pk
                or ocupante.extremo != extremo['extremo']
            ):
                errores.append(
                    f"fila {item['fila']}: el puerto "
                    f"{extremo['odf'].odf} / {extremo['puerto_numero']} ya "
                    'está asignado a otra terminación'
                )
                continue
            existente_extremo = (
                por_fibra_extremo.get((fibra.pk, extremo['extremo']))
                if fibra else None
            )
            if (
                existente_extremo
                and existente_extremo.puerto_odf_id != extremo['puerto'].pk
            ):
                errores.append(
                    f"fila {item['fila']}: el extremo {extremo['extremo']} ya "
                    'está conectado en otro puerto; use una operación de movimiento'
                )
                continue
            if (
                extremo['puerto'].estado_puerto == 'RESERVADO'
                and existente_extremo is None
            ):
                errores.append(
                    f"fila {item['fila']}: el puerto "
                    f"{extremo['odf'].odf} / {extremo['puerto_numero']} está "
                    'reservado; consuma la reserva mediante una operación explícita'
                )
    _errores_csv(errores, 'Terminaciones de fibra')
    _reportar_progreso(
        50,
        'Relaciones validadas; preparando fibras globales',
        procesadas=len(preparadas),
        total=len(df),
    )

    for item in preparadas:
        globales = grupos_globales[item['grupo']]
        estado_operacion = globales.get('estado_operacion')
        item['estado_informado'] = (
            estado_operacion[1]
            if estado_operacion and estado_operacion[0] == 'INFORMADO'
            else None
        )
        item['restablecer_estado'] = bool(
            estado_operacion and estado_operacion[0] == 'RESTABLECER'
        )
        for campo in ('condicion_informada', 'servicio', 'observaciones'):
            item[campo] = globales.get(campo)

    from ..services.fibras import (
        actualizar_metadatos_fibra,
        actualizar_fibras_masivo,
        asignar_ruta_fibra,
        crear_fibra,
    )

    fibras_por_grupo = {
        item['grupo']: item['fibra']
        for item in preparadas
        if item['fibra'] is not None
    }
    grupos_existentes = set(fibras_por_grupo)
    grupos_aplicados = set()
    fibras_tocadas = set()
    conexiones = []
    usuario = getattr(lote, 'usuario', None) if lote else None

    operaciones_fibras = []
    grupos_preparados = set()
    for item in preparadas:
        if item['grupo'] in grupos_preparados or item['grupo'] not in grupos_existentes:
            continue
        cambios = {}
        if item['condicion_informada']:
            cambios['condicion_fisica'] = item['condicion_informada']
        if item['servicio'] is not None:
            cambios['nombre_fibra'] = item['servicio']
        if item['observaciones'] is not None:
            cambios['observaciones'] = item['observaciones']
        operaciones_fibras.append({
            'fibra': fibras_por_grupo[item['grupo']],
            'cambios': cambios,
            'estado_informado': item['estado_informado'],
            'restablecer_estado': item['restablecer_estado'],
        })
        grupos_preparados.add(item['grupo'])
    actualizar_fibras_masivo(
        operaciones_fibras,
        usuario=usuario,
        origen='EXCEL',
        lote_importacion=lote,
    )
    _reportar_progreso(
        70,
        'Metadatos de fibras actualizados; preparando conexiones ODF',
        total=len(df),
    )

    for item in preparadas:
        fibra = fibras_por_grupo.get(item['grupo'])
        if fibra is None:
            fibra = crear_fibra(
                ruta=None,
                fibra_numero=item['numero'],
                codigo_fibra=item['codigo'],
                condicion_fisica=(
                    item['condicion_informada'] or 'SIN_VERIFICAR'
                ),
                nombre_fibra=item['servicio'] or '',
                observaciones=item['observaciones'] or '',
                usuario=usuario,
                origen='EXCEL',
                lote_importacion=lote,
            )
            fibras_por_grupo[item['grupo']] = fibra
            fibras_por_codigo[fibra.codigo_fibra.casefold()] = fibra
            if item['ruta'] is None:
                grupos_pendientes.add(item['grupo'])
            if item['ruta']:
                clave_ruta = (item['ruta'].pk, item['numero'].casefold())
                fibras_por_ruta[clave_ruta] = fibra

        if item['grupo'] not in grupos_aplicados:
            cambios_metadatos = {}
            if item['condicion_informada']:
                cambios_metadatos['condicion_fisica'] = item['condicion_informada']
            if item['servicio'] is not None:
                cambios_metadatos['nombre_fibra'] = item['servicio']
            if item['observaciones'] is not None:
                cambios_metadatos['observaciones'] = item['observaciones']
            cambios_reales = {
                campo: valor
                for campo, valor in cambios_metadatos.items()
                if getattr(fibra, campo) != valor
            }
            if item['grupo'] not in grupos_existentes and cambios_reales:
                actualizar_metadatos_fibra(
                    fibra=fibra,
                    usuario=usuario,
                    origen='EXCEL',
                    lote_importacion=lote,
                    **cambios_reales,
                )
            if item['grupo'] not in grupos_existentes and item['estado_informado'] and (
                fibra.estado != item['estado_informado']
                or fibra.origen_estado != 'INFORMADO'
            ):
                establecer_estado_fibra_informado(
                    fibra=fibra,
                    estado=item['estado_informado'],
                    usuario=usuario,
                    origen='EXCEL',
                    lote_importacion=lote,
                )
            if item['ruta'] and fibra.ruta_id is None:
                fibra, _, _ = asignar_ruta_fibra(
                    fibra=fibra,
                    ruta=item['ruta'],
                    usuario=usuario,
                    origen='EXCEL',
                    lote_importacion=lote,
                )
            # Sin información se aplica al final. Así la materialización
            # 1:1 de una Ruta de un tramo no convierte el dato BHP en una
            # inferencia dentro de la misma acción.
            if item['grupo'] not in grupos_existentes and item['restablecer_estado']:
                restablecer_estado_fibra(
                    fibra=fibra,
                    usuario=usuario,
                    origen='EXCEL',
                    lote_importacion=lote,
                )
            grupos_aplicados.add(item['grupo'])
        item['fibra'] = fibra
        fibras_tocadas.add(fibra.pk)
        for extremo in item['extremos']:
            conexiones.append({
                'fibra': fibra,
                'puerto': extremo['puerto'],
                'extremo': extremo['extremo'],
                'tipo_conector': extremo['conector'],
            })

    # El lote identifica la procedencia del último dato sin generar un evento
    # de auditoría ficticio cuando ningún valor de negocio cambió.
    if lote is not None:
        for lote_ids in _dividir_en_lotes(sorted(fibras_tocadas)):
            InventarioFibra.objects.filter(pk__in=lote_ids).update(
                lote_importacion=lote,
            )

    _reportar_progreso(
        78,
        'Creando terminaciones y actualizando puertos',
        total=len(df),
    )
    _, creadas, actualizadas = conectar_puertos_masivo(
        conexiones,
        usuario=usuario,
        origen='EXCEL',
        lote_importacion=lote,
    )
    _reportar_progreso(
        92,
        'Terminaciones creadas; cerrando contadores y auditoría',
        procesadas=len(df),
        total=len(df),
        creadas=creadas,
        actualizadas=actualizadas,
    )

    resultado = _resultado(
        len(df),
        creadas=creadas,
        actualizadas=actualizadas,
    )
    resultado['modo'] = 'parche'
    resultado['informaciones'] = [
        'La carga no eliminó terminaciones omitidas. Cada puerto declarado '
        'quedó Ocupado mediante una terminación oficial.'
    ]
    pendientes_nuevas = len(grupos_pendientes)
    if pendientes_nuevas:
        resultado['informaciones'].append(
            f'{pendientes_nuevas} hilo(s) se crearon con troncal pendiente. '
            'Se reconciliaron mediante su identidad física o Codigo Fibra '
            'estable y requieren '
            'asociar su troncal cuando esa información esté disponible.'
        )
    return resultado


@transaction.atomic
def _procesar_odfs_inventario(file, lote=None):
    """Carga la información general de ODFs desde un CSV (Archivo D)"""
    decoded_file = file.read().decode('utf-8')
    df = pd.read_csv(io.StringIO(decoded_file))
    from ..services.inventario import (
        ajustar_puertos_a_capacidad,
        guardar_odf_normalizado,
        limpiar_texto,
    )

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
        
        odf_obj, creada = guardar_odf_normalizado(
            odf_nombre=odf_nombre,
            hub_nombre=hub_site,
            sala_nombre=sala,
            rack_nombre=rack,
            defaults=odf_datos,
            lote=lote,
        )
        ajustar_puertos_a_capacidad(
            odf_obj,
            odf_datos['capacidad_puertos'],
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
        estado_original = limpio(row.get('estado_puerto'), 'LIBRE')
        destino = limpio(row.get('destino'))
        estado = normalizar_estado_puerto_odf_con_destino(
            estado_original,
            destino,
            default=None,
        )
        if not estado:
            raise ValueError(f'Fila {numero_fila}: estado de puerto inválido: {estado_original}.')
        if estado == 'OCUPADO':
            raise ValueError(
                f'Fila {numero_fila}: un puerto no se importa como Ocupado. '
                'Cárguelo como Libre y cree la ocupación mediante la '
                'terminación oficial de la fibra.'
            )
        filas.append((odf_obj, puerto_num, {
            # La relación es la fuente oficial; no conserve variantes de
            # mayúsculas o alias procedentes del CSV.
            'bandeja': limpio(row.get('bandeja')),
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
        'bandeja', 'estado_puerto', 'tipo_conector',
        'patchcord', 'destino', 'observaciones', 'lote_importacion',
    ]
    for odf_obj, puerto_num, valores in filas:
        obj = existentes.get((odf_obj.pk, puerto_num.casefold()))
        if obj:
            # Una recarga de inventario actualiza metadatos, pero no ejecuta
            # transiciones operativas ni consume/cancela reservas. El estado
            # existente se conserva y se gestiona mediante los servicios de
            # conexión o la plantilla de operaciones.
            valores['estado_puerto'] = obj.estado_puerto
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
        ocupados = conteos.get((odf_obj.pk, 'OCUPADO'), 0)
        reservados = conteos.get((odf_obj.pk, 'RESERVADO'), 0)
        total_detalle = sum(
            conteos.get((odf_obj.pk, estado), 0)
            for estado in ('LIBRE', 'OCUPADO', 'RESERVADO')
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
    Importa geometría y segmentación visual para múltiples rutas.

    La carga puede crear la entidad básica Ruta, pero no crea ni modifica
    InventarioTramo. Los campos técnicos se cargan en su flujo independiente.
    Columnas requeridas: Ruta, Latitude, Longitude
    Columnas opcionales: tipo_trazado, new_seg
    """
    decoded_file = file.read().decode('utf-8-sig')
    df = pd.read_csv(io.StringIO(decoded_file))
    df.columns = [str(columna).strip().lower() for columna in df.columns]

    for columna in ('ruta', 'latitude', 'longitude'):
        if columna not in df.columns:
            raise ValueError(
                f"Falta la columna requerida en el CSV: {columna}"
            )
    rutas_normalizadas = df['ruta'].map(_texto)
    filas_sin_ruta = [
        str(indice + 2)
        for indice, nombre in enumerate(rutas_normalizadas)
        if not nombre
    ]
    if filas_sin_ruta:
        raise ValueError(
            'El CSV contiene filas sin nombre de ruta: '
            f"{', '.join(filas_sin_ruta[:10])}."
        )
    df['ruta'] = rutas_normalizadas
    df['_ruta_clave'] = rutas_normalizadas.str.casefold()
    hub_site_ignorado = (
        'hub_site' in df.columns
        and any(_texto(valor) for valor in df['hub_site'])
    )
    if hub_site_ignorado:
        logger.warning(
            'La columna hub_site del trazado geográfico se acepta solo por '
            'compatibilidad y no modifica el inventario técnico.'
        )

    rutas_actualizadas = 0
    coordenadas_creadas = 0

    # sort=False mantiene el orden de aparición de las rutas y cada grupo
    # conserva el orden de sus puntos en el archivo.
    for _, df_coords in df.groupby('_ruta_clave', sort=False):
        ruta_nombre = _texto(df_coords.iloc[0]['ruta'])
        ruta_obj = Ruta.objects.filter(nombre__iexact=ruta_nombre).first()
        if not ruta_obj:
            ruta_obj = Ruta.objects.create(
                nombre=ruta_nombre,
                lote_importacion=lote,
            )
            logger.info(
                "Ruta '%s' creada automáticamente al cargar coordenadas.",
                ruta_nombre,
            )

        coordenadas_creadas += _reemplazar_geografia_ruta(
            ruta_obj,
            df_coords.reset_index(drop=True),
            lote,
        )
        rutas_actualizadas += 1

    resultado = _resultado(len(df), coordenadas_creadas)
    resultado['rutas_actualizadas'] = rutas_actualizadas
    if hub_site_ignorado:
        resultado['informaciones'] = [
            'La columna hub_site no se guardó desde esta carga geográfica; '
            'adminístrala mediante Inventario Técnico de Tramos.'
        ]
    return resultado


def _cerrar_lote_exitoso(lote, resultado):
    if not isinstance(resultado, dict):
        resultado = _resultado()
    filas_rechazadas = int(resultado.get('rechazadas', 0) or 0)
    lote.estado = 'COMPLETADO_CON_ERRORES' if filas_rechazadas else 'COMPLETADO'
    lote.total_filas = int(resultado.get('total', 0) or 0)
    lote.filas_creadas = int(resultado.get('creadas', 0) or 0)
    lote.filas_actualizadas = int(resultado.get('actualizadas', 0) or 0)
    lote.filas_rechazadas = filas_rechazadas
    lote.finalizado_en = now()
    lote.save(update_fields=[
        'estado', 'total_filas', 'filas_creadas', 'filas_actualizadas',
        'filas_rechazadas', 'finalizado_en',
    ])
    return resultado


def _cerrar_lote_fallido(lote, exc):
    lote.estado = 'FALLIDO'
    lote.detalle_errores = [str(exc)[:1000]]
    lote.finalizado_en = now()
    lote.save(update_fields=['estado', 'detalle_errores', 'finalizado_en'])


def _guardar_archivo_importacion(archivo_subido, lote):
    nombre = os.path.basename(archivo_subido.name)
    ruta = _directorio_importaciones() / 'pendientes' / f'{lote.codigo}_{nombre}'
    archivo_subido.seek(0)
    with ruta.open('wb') as destino:
        for chunk in archivo_subido.chunks():
            destino.write(chunk)
    return ruta


def _ejecutar_importacion_en_segundo_plano(
    lote_id,
    codigo,
    tipo_csv,
    nombre_amigable,
    procesador_func,
    ruta_archivo,
):
    close_old_connections()
    _IMPORT_PROGRESS_LOCAL.reporter = lambda **datos: _guardar_progreso(
        codigo,
        **datos,
    )
    try:
        lote = LoteImportacion.objects.select_related('usuario').get(pk=lote_id)
        _reportar_progreso(10, f'Preparando {nombre_amigable}')
        with Path(ruta_archivo).open('rb') as archivo:
            _reportar_progreso(15, 'Validando archivo y datos relacionados')
            resultado = procesador_func(archivo, lote=lote)
        _reportar_progreso(95, 'Consolidando resultado y auditoría')
        resultado = _cerrar_lote_exitoso(lote, resultado)
        _guardar_progreso(
            codigo,
            estado=lote.estado,
            porcentaje=100,
            etapa='Importación completada',
            procesadas=resultado['total'],
            total=resultado['total'],
            creadas=resultado['creadas'],
            actualizadas=resultado['actualizadas'],
            rechazadas=resultado.get('rechazadas', 0),
            mensaje=(
                f"{nombre_amigable}: {resultado['total']} fila(s) procesadas; "
                f"{resultado['creadas']} creadas, "
                f"{resultado['actualizadas']} actualizadas y "
                f"{resultado.get('rechazadas', 0)} rechazadas."
            ),
        )
    except Exception as exc:
        logger.exception(
            'Error inesperado al procesar la carga asíncrona %s',
            tipo_csv,
        )
        close_old_connections()
        try:
            lote = LoteImportacion.objects.get(pk=lote_id)
            _cerrar_lote_fallido(lote, exc)
        except Exception:
            logger.exception('No se pudo cerrar como fallido el lote %s', codigo)
        _guardar_progreso(
            codigo,
            estado='FALLIDO',
            porcentaje=100,
            etapa='La importación falló',
            mensaje='No se pudo completar la importación. Revise el detalle del lote.',
        )
    finally:
        _IMPORT_PROGRESS_LOCAL.reporter = None
        try:
            Path(ruta_archivo).unlink(missing_ok=True)
        except OSError:
            logger.warning('No se pudo retirar el archivo temporal %s', ruta_archivo)
        close_old_connections()


@login_required
@require_GET
def progreso_importacion(request, codigo):
    """Estado por UUID; evita consultar BD durante el bloqueo de SQLite."""
    ruta = _ruta_progreso(codigo)
    if not ruta.exists():
        return JsonResponse(
            {'estado': 'PENDIENTE', 'porcentaje': 0, 'etapa': 'En cola'},
            status=404,
        )
    try:
        datos = json.loads(ruta.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return JsonResponse(
            {'estado': 'PROCESANDO', 'porcentaje': 0, 'etapa': 'Preparando estado'},
            status=503,
        )
    propietario_id = datos.get('usuario_id')
    if (
        not request.user.is_superuser
        and propietario_id != request.user.pk
    ):
        # Se responde como inexistente para no revelar UUID ni actividad de
        # importaciones pertenecientes a otro usuario.
        return JsonResponse(
            {'estado': 'NO_ENCONTRADO'},
            status=404,
        )
    respuesta = JsonResponse(datos)
    respuesta['Cache-Control'] = 'no-store, max-age=0'
    return respuesta


@login_required
@require_POST
def cargar_csv(request, tipo_csv):
    """Vista que maneja la carga de diferentes archivos y los procesa."""
    PROCESADORES = {
        'sites_inventario': (_procesar_sites_inventario, "Sites (.csv)"),
        'reservas': (_procesar_reservas, "Reservas"),
        'ruta_otu': (_procesar_ruta_otu, "Troncales y Datos Generales"),
        'equipos_otu': (_procesar_equipos_otu, "Equipos OTU"),
        'coordenadas_rutas': (_procesar_coordenadas_zip, "Coordenadas de Rutas (ZIP)"),

        'asociar_puertos': (_asociar_puertos_a_rutas, "Asociar Puertos a Rutas"),
        'id_rutas': (_procesar_id_rutas, "Cargar IDs de Rutas (ONMSI)"),
        'coordenadas_inventario': (_procesar_coordenadas_inventario_zip, "Trazado Geográfico Segmentado (.zip)"),
        'coordenadas_csv': (_procesar_coordenadas_csv, "Recorridos en el mapa (.csv)"),
        'tramos_inventario': (_procesar_tramos_inventario, "Inventario Técnico de Tramos (.csv)"),
        'fibras_globales': (_procesar_fibras_globales, "Inventario global de fibras (.csv)"),
        'fibras_inventario': (_procesar_fibras_inventario, "Fibras por Tramo (.csv)"),
        'terminaciones_fibra': (
            _procesar_terminaciones_fibra,
            "Terminaciones de Fibra (.csv)",
        ),
        'odf_inventario': (_procesar_odfs_inventario, "Inventario de ODFs (.csv)"),
        'puertos_odf_inventario': (_procesar_puertos_odf_inventario, "Detalle de Puertos ODF (.csv)"),
    }

    if tipo_csv not in PROCESADORES:
        messages.error(request, f"Tipo de archivo '{tipo_csv}' no es válido.")
        return redirect('configuracion')

    permisos_por_tipo = {
        'sites_inventario': ('mapas.add_hubsite', 'mapas.change_hubsite'),
        'reservas': ('mapas.add_reserva', 'mapas.change_reserva'),
        'ruta_otu': ('mapas.add_ruta', 'mapas.change_ruta', 'mapas.add_otu'),
        'equipos_otu': ('mapas.add_otu', 'mapas.change_otu', 'mapas.add_puertootu'),
        'coordenadas_rutas': ('mapas.add_coordenadaruta', 'mapas.change_coordenadaruta'),
        'asociar_puertos': ('mapas.change_puertootu',),
        'id_rutas': ('mapas.add_idruta', 'mapas.change_idruta'),
        'coordenadas_inventario': ('mapas.add_coordenadaruta', 'mapas.change_coordenadaruta'),
        'coordenadas_csv': ('mapas.add_coordenadaruta', 'mapas.change_coordenadaruta'),
        'tramos_inventario': ('mapas.add_inventariotramo', 'mapas.change_inventariotramo'),
        'fibras_globales': ('mapas.add_inventariofibra', 'mapas.change_inventariofibra'),
        'fibras_inventario': ('mapas.add_inventariofibra', 'mapas.change_inventariofibra'),
        'terminaciones_fibra': (
            'mapas.add_terminacionfibra',
            'mapas.change_terminacionfibra',
        ),
        'odf_inventario': ('mapas.add_inventarioodf', 'mapas.change_inventarioodf'),
        'puertos_odf_inventario': (
            'mapas.add_detallepuertoodf',
            'mapas.change_detallepuertoodf',
        ),
    }
    if not request.user.has_perms(permisos_por_tipo[tipo_csv]):
        raise PermissionDenied

    procesador_func, nombre_amigable = PROCESADORES[tipo_csv]
    solicitud_asincrona = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )

    if request.method == 'POST':
        if 'csv_file' in request.FILES:
            archivo_subido = request.FILES['csv_file']

            extension_valida = '.zip' if tipo_csv in ['coordenadas_rutas', 'coordenadas_inventario'] else '.csv'
            if not archivo_subido.name.casefold().endswith(extension_valida):
                if solicitud_asincrona:
                    return JsonResponse(
                        {'error': f'El archivo debe ser de formato {extension_valida}.'},
                        status=400,
                    )
                messages.error(request, f"El archivo debe ser de formato {extension_valida}.")
                return redirect('configuracion')

            try:
                _validar_archivo_subido(
                    archivo_subido,
                    es_zip=extension_valida == '.zip',
                )
            except ValueError as exc:
                if solicitud_asincrona:
                    return JsonResponse({'error': str(exc)}, status=400)
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

            if solicitud_asincrona:
                try:
                    ruta_archivo = _guardar_archivo_importacion(
                        archivo_subido,
                        lote,
                    )
                    _guardar_progreso(
                        lote.codigo,
                        usuario_id=request.user.pk,
                        estado='PENDIENTE',
                        porcentaje=2,
                        etapa='Archivo recibido; esperando turno',
                        procesadas=0,
                        total=0,
                        creadas=0,
                        actualizadas=0,
                        rechazadas=0,
                        tipo=tipo_csv,
                        archivo=lote.archivo_origen,
                    )
                    _IMPORT_EXECUTOR.submit(
                        _ejecutar_importacion_en_segundo_plano,
                        lote.pk,
                        str(lote.codigo),
                        tipo_csv,
                        nombre_amigable,
                        procesador_func,
                        str(ruta_archivo),
                    )
                except Exception as exc:
                    logger.exception('No se pudo encolar la importación %s', tipo_csv)
                    _cerrar_lote_fallido(lote, exc)
                    return JsonResponse(
                        {'error': 'No se pudo iniciar la importación.'},
                        status=500,
                    )
                return JsonResponse(
                    {
                        'lote': str(lote.codigo),
                        'estado': 'PENDIENTE',
                        'progreso_url': reverse(
                            'progreso_importacion',
                            args=[lote.codigo],
                        ),
                    },
                    status=202,
                )

            try:
                kwargs_procesador = {'lote': lote}
                resultado = procesador_func(archivo_subido, **kwargs_procesador)
                if not isinstance(resultado, dict):
                    resultado = _resultado()
                filas_rechazadas = int(resultado.get('rechazadas', 0) or 0)
                advertencias = resultado.get('advertencias') or []
                informaciones = resultado.get('informaciones') or []
                notificar = (
                    messages.warning
                    if filas_rechazadas or advertencias
                    else messages.success
                )

                if tipo_csv == 'asociar_puertos':
                    notificar(
                        request,
                        f"Proceso completado. Se asociaron {resultado['actualizadas']} puertos a sus rutas."
                    )
                elif tipo_csv == 'coordenadas_csv':
                    avisos = advertencias + informaciones
                    aviso = f" {' '.join(avisos)}" if avisos else ''
                    notificar(
                        request,
                        'Carga de trazado geográfico exitosa. Se actualizaron las coordenadas de '
                        f"{resultado.get('rutas_actualizadas', 0)} rutas."
                        f'{aviso}'
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
                    if advertencias:
                        mensaje += ' Avisos: ' + ' '.join(
                            str(aviso) for aviso in advertencias[:3]
                        )
                        if len(advertencias) > 3:
                            mensaje += (
                                f' Hay {len(advertencias) - 3} aviso(s) '
                                'adicional(es) en el lote.'
                            )
                    elif informaciones:
                        mensaje += ' ' + ' '.join(
                            str(aviso) for aviso in informaciones[:1]
                        )
                    notificar(request, mensaje)

                _cerrar_lote_exitoso(lote, resultado)

                return redirect('configuracion')
            except Exception as e:
                logger.exception("Error inesperado al procesar la carga %s", tipo_csv)
                _cerrar_lote_fallido(lote, e)
                messages.error(request, "No se pudo procesar el archivo. Consulte el detalle del lote.")
                return redirect('configuracion')
        else:
            messages.error(request, "No se adjuntó ningún archivo.")
            return redirect('configuracion')

    return redirect('configuracion')
