"""Controles consultables de calidad. Solo lectura, sin inferir ni reparar datos.

Los pendientes no impiden importar. Las contradicciones se separan de la
información ausente; las fallas físicas pertenecen al dashboard operativo.
"""
from dataclasses import dataclass

from django.db.models import Count, Exists, F, OuterRef, Q

from ..models import (
    DetallePuertoODF, FibraTramo, HubSite, InventarioFibra,
    InventarioODF, InventarioTramo, Ruta, TerminacionFibra,
)
from .filtros_inventario import (
    anotar_puertos_odf, filtrar_rutas_site, filtro_site_fibras,
)
from .inventario import UBICACION_ODF_SIN_ASIGNAR


@dataclass
class Control:
    codigo: str
    titulo: str
    grupo: str
    entidad: str
    explicacion: str
    siguiente_paso: str
    queryset: object


def controles_calidad(user, site=''):
    """Cada queryset cuenta registros de su propia entidad, nunca filas de joins."""
    controles = []

    def add(codigo, titulo, grupo, entidad, explicacion, paso, queryset):
        controles.append(Control(codigo, titulo, grupo, entidad, explicacion, paso, queryset))

    if user.has_perm('mapas.view_inventariofibra'):
        fibras = InventarioFibra.objects.all()
        if site:
            fibras = fibras.filter(filtro_site_fibras(site)).distinct()
        add('fibras_estado', 'Fibras sin estado informado', 'pendientes', 'fibra',
            'Estado global registrado como Sin información; no equivale a libre ni ocupado.',
            'Confirmar el estado de uso y actualizar la fibra por el flujo habitual.',
            fibras.filter(estado='SIN_INFORMACION'))
        add('fibras_troncal', 'Fibras sin troncal asociada', 'pendientes', 'fibra',
            'La fibra existe, pero su troncal todavía no se ha informado. Es una carga válida.',
            'Asociar la troncal cuando se disponga de información confirmada.',
            fibras.filter(ruta__isnull=True))
        terminaciones = TerminacionFibra.objects.filter(fibra_id=OuterRef('pk'))
        extremos = fibras.alias(
            _tiene_a=Exists(terminaciones.filter(extremo='A')),
            _tiene_b=Exists(terminaciones.filter(extremo='B')),
        )
        add('fibras_terminaciones', 'Fibras con extremos pendientes', 'pendientes', 'fibra',
            'Falta registrar A, B o ambos. Cada fibra se cuenta una sola vez en este control.',
            'Completar la terminación conocida desde Gestionar conexión o la carga A/B.',
            extremos.filter(Q(_tiene_a=False) | Q(_tiene_b=False)))
        if user.has_perm('mapas.view_fibratramo'):
            # Tramos de la ruta sin asignación para ESTA fibra. Se usan subconsultas
            # para no multiplicar las filas al combinar terminaciones y recorrido.
            asignaciones = FibraTramo.objects.filter(fibra_id=OuterRef(OuterRef('pk')))
            faltantes = InventarioTramo.objects.filter(ruta_id=OuterRef('ruta_id')).exclude(
                pk__in=asignaciones.values('tramo_id')
            )
            add('fibras_recorrido', 'Fibras con tramos por vincular', 'pendientes', 'fibra',
                'La troncal tiene tramos técnicos, pero falta vincular la fibra a uno o más de ellos.',
                'Revisar la ficha de calidad y completar Fibras por tramo con datos confirmados.',
                fibras.alias(_faltan_tramos=Exists(faltantes)).filter(_faltan_tramos=True))
            ajenas = FibraTramo.objects.filter(fibra_id=OuterRef('pk')).filter(
                Q(fibra__ruta__isnull=True) | ~Q(tramo__ruta_id=F('fibra__ruta_id'))
            )
            add('fibras_tramos_ajenos', 'Fibras vinculadas a otra troncal', 'inconsistencias', 'fibra',
                'Existe una asignación a un tramo de una troncal distinta de la fibra.',
                'Revisar la relación fibra–tramo. No se reasigna ni elimina automáticamente.',
                fibras.alias(_tramos_ajenos=Exists(ajenas)).filter(_tramos_ajenos=True))

    if user.has_perm('mapas.view_ruta'):
        rutas = filtrar_rutas_site(Ruta.objects.all(), site)
        add('troncales_geografia', 'Troncales con coordenadas pendientes', 'pendientes', 'troncal',
            'Hay menos de dos puntos de recorrido registrados. Las coordenadas del Site no sustituyen el recorrido.',
            'Cargar las coordenadas de la troncal. Dos puntos no certifican la geometría completa.',
            rutas.annotate(_puntos=Count('coordenadas', distinct=True)).filter(_puntos__lt=2))
        add('troncales_tramos', 'Troncales sin tramos técnicos', 'pendientes', 'troncal',
            'La troncal está registrada, pero todavía no tiene tramos técnicos.',
            'Completar los tramos cuando se conozca el recorrido físico.',
            rutas.filter(tramos_inventario__isnull=True))

    if user.has_perm('mapas.view_inventarioodf'):
        odfs = InventarioODF.objects.all()
        if site:
            odfs = odfs.filter(rack_obj__sala__hub_site__nombre__iexact=site)
        ubicacion = Q()
        for field in ('rack_obj__nombre', 'rack_obj__sala__nombre', 'rack_obj__sala__hub_site__nombre'):
            ubicacion |= Q(**{field + '__iexact': UBICACION_ODF_SIN_ASIGNAR}) | Q(**{field: ''})
        add('odfs_ubicacion', 'ODF con ubicación pendiente', 'pendientes', 'odf',
            'El Site, sala o rack figura sin asignar. No impide registrar ODF ni conexiones.',
            'Confirmar la ubicación y completar la jerarquía Site → Sala → Rack.', odfs.filter(ubicacion))
        if user.has_perm('mapas.view_detallepuertoodf'):
            odfs = anotar_puertos_odf(odfs)
            add('odfs_puertos', 'ODF con puertos por registrar', 'pendientes', 'odf',
                'La cantidad de puertos registrados es menor que la capacidad declarada. Se cuentan ODF, no puertos libres.',
                'Revisar capacidad y puertos del ODF. Los puertos ausentes no se consideran disponibles.',
                odfs.filter(capacidad_puertos__gt=F('_puertos_total')))
            add('odfs_capacidad', 'ODF con exceso de puertos', 'inconsistencias', 'odf',
                'Los puertos registrados superan una capacidad positiva declarada.',
                'Verificar la capacidad física y el inventario de puertos antes de corregir.',
                odfs.filter(capacidad_puertos__gt=0, capacidad_puertos__lt=F('_puertos_total')))

    if user.has_perm('mapas.view_detallepuertoodf'):
        puertos = DetallePuertoODF.objects.all()
        if site:
            puertos = puertos.filter(odf_obj__rack_obj__sala__hub_site__nombre__iexact=site)
        terminacion = TerminacionFibra.objects.filter(puerto_odf_id=OuterRef('pk'))
        add('puertos_conexion', 'Puertos y conexiones contradictorios', 'inconsistencias', 'puerto',
            'Un puerto con terminación no figura ocupado, o figura ocupado sin una terminación oficial.',
            'Contrastar la conexión y su historial; corregir mediante Gestionar conexión.',
            puertos.alias(_conectado=Exists(terminacion)).filter(
                (Q(_conectado=True) & ~Q(estado_puerto='OCUPADO'))
                | Q(_conectado=False, estado_puerto='OCUPADO')
            ))

    if user.has_perm('mapas.view_hubsite'):
        sites = HubSite.objects.exclude(nombre__iexact=UBICACION_ODF_SIN_ASIGNAR)
        if site:
            sites = sites.filter(nombre__iexact=site)
        add('sites_coordenadas', 'Sites sin coordenadas completas', 'pendientes', 'site',
            'Falta latitud, longitud o ambas. El valor cero sí es una coordenada válida.',
            'Confirmar y completar las coordenadas del Site.',
            sites.filter(Q(latitud__isnull=True) | Q(longitud__isnull=True)))
    return controles
