import re
import unicodedata
from collections import defaultdict

from django.db import migrations, models
from django.db.models.functions import Lower


TIPOS_NODO = {
    'SITE',
    'ODF',
    'MUFA',
    'CAMARA',
    'POSTE',
    'CAJA_EMPALME',
    'PUNTO',
    'OTRO',
}
ESTADOS_FIBRA = {'Libre', 'Ocupado', 'Reservado'}
ESTADOS_FIBRA_TRAMO = ESTADOS_FIBRA | {'Desconocido'}
MARCADORES_SIN_DATO = {
    '',
    '-',
    '—',
    'N/A',
    'NA',
    'NAN',
    'NONE',
    'NULL',
    'NO APLICA',
    'NO_APLICA',
    'POR LEVANTAR',
    'POR_LEVANTAR',
    'SIN DATO',
    'SIN_DATO',
}


def _texto(valor):
    return str(valor or '').strip()


def _clave(valor):
    return _texto(valor).casefold()


def _es_marcador(valor):
    return _texto(valor).upper() in MARCADORES_SIN_DATO


def _sin_acentos(valor):
    texto = unicodedata.normalize('NFKD', _texto(valor))
    return ''.join(
        caracter
        for caracter in texto
        if not unicodedata.combining(caracter)
    )


def _tipo_reserva(valor):
    tipo = re.sub(
        r'[^A-Z0-9]+',
        '_',
        _sin_acentos(valor).upper(),
    ).strip('_')
    equivalencias = {
        'CAMARA_DE_EMPAME': 'CAMARA',
        'CAMARA_DE_EMPALME': 'CAMARA',
        'CAJA_EMPAME': 'CAJA_EMPALME',
        'CAJA_DE_EMPAME': 'CAJA_EMPALME',
        'CAJA_DE_EMPALME': 'CAJA_EMPALME',
    }
    tipo = equivalencias.get(tipo, tipo)
    return tipo if tipo in TIPOS_NODO else 'OTRO'


def _unicos_por_clave(filas, campo):
    agrupadas = defaultdict(list)
    for fila in filas:
        clave = _clave(fila.get(campo))
        if clave:
            agrupadas[clave].append(fila)
    return {
        clave: valores[0]
        for clave, valores in agrupadas.items()
        if len(valores) == 1
    }


def validar_y_completar_nodos_legacy(apps, schema_editor):
    FibraTramo = apps.get_model('mapas', 'FibraTramo')
    HubSite = apps.get_model('mapas', 'HubSite')
    InventarioFibra = apps.get_model('mapas', 'InventarioFibra')
    InventarioODF = apps.get_model('mapas', 'InventarioODF')
    InventarioTramo = apps.get_model('mapas', 'InventarioTramo')
    NodoRed = apps.get_model('mapas', 'NodoRed')
    Reserva = apps.get_model('mapas', 'Reserva')
    Ruta = apps.get_model('mapas', 'Ruta')
    db_alias = schema_editor.connection.alias

    rutas_vistas = {}
    rutas_duplicadas = []
    for ruta_id, nombre in (
        Ruta.objects.using(db_alias).values_list('pk', 'nombre')
    ):
        clave = _clave(nombre)
        if clave in rutas_vistas:
            rutas_duplicadas.append(
                f'{rutas_vistas[clave]} y {ruta_id}: {_texto(nombre)}'
            )
        else:
            rutas_vistas[clave] = ruta_id
    if rutas_duplicadas:
        raise RuntimeError(
            'No se puede aplicar la unicidad de rutas sin distinguir '
            'mayúsculas: ' + '; '.join(rutas_duplicadas[:20])
        )

    tipos_invalidos = list(
        NodoRed.objects.using(db_alias)
        .exclude(tipo__in=TIPOS_NODO)
        .values_list('pk', 'tipo')[:20]
    )
    estados_logicos_invalidos = list(
        InventarioFibra.objects.using(db_alias)
        .exclude(estado__in=ESTADOS_FIBRA)
        .values_list('pk', 'estado')[:20]
    )
    estados_fisicos_invalidos = list(
        FibraTramo.objects.using(db_alias)
        .exclude(estado__in=ESTADOS_FIBRA_TRAMO)
        .values_list('pk', 'estado')[:20]
    )
    if (
        tipos_invalidos
        or estados_logicos_invalidos
        or estados_fisicos_invalidos
    ):
        raise RuntimeError(
            'Existen tipos o estados no válidos antes de reforzar las '
            'restricciones. '
            f'Nodos={tipos_invalidos}; '
            f'Fibras={estados_logicos_invalidos}; '
            f'FibrasTramo={estados_fisicos_invalidos}.'
        )

    sites = list(
        HubSite.objects.using(db_alias).values(
            'pk',
            'nombre',
            'lote_importacion_id',
        )
    )
    odfs = list(
        InventarioODF.objects.using(db_alias).values(
            'pk',
            'odf',
            'lote_importacion_id',
        )
    )
    reservas = list(
        Reserva.objects.using(db_alias).values(
            'pk',
            'codigo',
            'nombre',
            'tipo',
            'lote_importacion_id',
        )
    )
    sites_por_nombre = _unicos_por_clave(sites, 'nombre')
    odfs_por_nombre = _unicos_por_clave(odfs, 'odf')
    reservas_por_codigo = _unicos_por_clave(reservas, 'codigo')
    reservas_por_nombre = _unicos_por_clave(reservas, 'nombre')

    nodos_cache = {
        (nodo['tipo'], _clave(nodo['codigo'])): nodo['pk']
        for nodo in NodoRed.objects.using(db_alias).values(
            'pk',
            'tipo',
            'codigo',
        )
    }

    def descriptor_exacto(valor):
        if _es_marcador(valor):
            return None
        clave = _clave(valor)
        site = sites_por_nombre.get(clave)
        if site:
            return {
                'tipo': 'SITE',
                'codigo': _texto(site['nombre']).upper(),
                'nombre': _texto(site['nombre']),
                'lote_importacion_id': site['lote_importacion_id'],
                'exacto': True,
            }
        odf = odfs_por_nombre.get(clave)
        if odf:
            return {
                'tipo': 'ODF',
                'codigo': _texto(odf['odf']).upper(),
                'nombre': _texto(odf['odf']),
                'lote_importacion_id': odf['lote_importacion_id'],
                'exacto': True,
            }
        reserva = (
            reservas_por_codigo.get(clave)
            or reservas_por_nombre.get(clave)
        )
        if reserva:
            codigo = (
                _texto(reserva['codigo'])
                if not _es_marcador(reserva['codigo'])
                else _texto(reserva['nombre'])
            )
            return {
                'tipo': _tipo_reserva(reserva['tipo']),
                'codigo': codigo.upper(),
                'nombre': _texto(reserva['nombre']),
                'lote_importacion_id': reserva['lote_importacion_id'],
                'exacto': True,
            }
        return None

    def descriptor_otro(valor, lote_importacion_id):
        if _es_marcador(valor):
            return None
        texto = _texto(valor)
        return {
            'tipo': 'OTRO',
            'codigo': texto.upper(),
            'nombre': texto,
            'lote_importacion_id': lote_importacion_id,
            'exacto': False,
        }

    def resolver_descriptor(valores, lote_importacion_id):
        candidatos = [
            valor for valor in valores
            if not _es_marcador(valor)
        ]
        for valor in candidatos:
            descriptor = descriptor_exacto(valor)
            if descriptor:
                return descriptor
        return (
            descriptor_otro(candidatos[0], lote_importacion_id)
            if candidatos
            else None
        )

    def obtener_nodo(descriptor):
        if not descriptor:
            return None
        clave = (descriptor['tipo'], _clave(descriptor['codigo']))
        nodo_id = nodos_cache.get(clave)
        if nodo_id:
            return nodo_id
        nodo = NodoRed.objects.using(db_alias).create(
            tipo=descriptor['tipo'],
            codigo=descriptor['codigo'],
            nombre=descriptor['nombre'],
            lote_importacion_id=descriptor['lote_importacion_id'],
        )
        nodos_cache[clave] = nodo.pk
        return nodo.pk

    for tramo in (
        InventarioTramo.objects.using(db_alias)
        .filter(
            models.Q(origen_nodo_id__isnull=True)
            | models.Q(destino_nodo_id__isnull=True)
        )
        .order_by('ruta_id', 'tramo_secuencia', 'pk')
        .values(
            'pk',
            'origen',
            'destino',
            'hub_site',
            'odf_nombre',
            'origen_nodo_id',
            'destino_nodo_id',
            'lote_importacion_id',
        )
    ):
        cambios = {}
        origen_id = tramo['origen_nodo_id']
        destino_id = tramo['destino_nodo_id']
        if origen_id is None:
            descriptor = resolver_descriptor(
                (
                    tramo['origen'],
                    tramo['hub_site'],
                    tramo['odf_nombre'],
                ),
                tramo['lote_importacion_id'],
            )
            candidato = obtener_nodo(descriptor)
            if candidato is not None and candidato != destino_id:
                origen_id = candidato
                cambios['origen_nodo_id'] = candidato
        if destino_id is None:
            descriptor = resolver_descriptor(
                (tramo['destino'],),
                tramo['lote_importacion_id'],
            )
            candidato = obtener_nodo(descriptor)
            if candidato is not None and candidato != origen_id:
                cambios['destino_nodo_id'] = candidato
        if cambios:
            InventarioTramo.objects.using(db_alias).filter(
                pk=tramo['pk'],
            ).update(**cambios)


class Migration(migrations.Migration):
    dependencies = [
        ('mapas', '0029_inventario_tecnico_por_tramo'),
    ]

    operations = [
        migrations.RunPython(
            validar_y_completar_nodos_legacy,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name='ruta',
            constraint=models.UniqueConstraint(
                Lower('nombre'),
                name='uq_ruta_nombre_ci',
            ),
        ),
        migrations.AddConstraint(
            model_name='nodored',
            constraint=models.CheckConstraint(
                condition=models.Q(tipo__in=(
                    'SITE',
                    'ODF',
                    'MUFA',
                    'CAMARA',
                    'POSTE',
                    'CAJA_EMPALME',
                    'PUNTO',
                    'OTRO',
                )),
                name='ck_nodo_tipo_valido',
            ),
        ),
        migrations.AddConstraint(
            model_name='inventariofibra',
            constraint=models.CheckConstraint(
                condition=models.Q(estado__in=(
                    'Libre',
                    'Ocupado',
                    'Reservado',
                )),
                name='ck_inventario_fibra_estado',
            ),
        ),
        migrations.AddConstraint(
            model_name='fibratramo',
            constraint=models.CheckConstraint(
                condition=models.Q(estado__in=(
                    'Libre',
                    'Ocupado',
                    'Reservado',
                    'Desconocido',
                )),
                name='ck_fibra_tramo_estado_valido',
            ),
        ),
    ]
