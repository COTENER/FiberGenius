"""Dashboard de calidad de información, independiente del estado operativo."""
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from ..models import HubSite, TerminacionFibra
from ..services.calidad_dashboard import controles_calidad
from ..ui_access import screen_required


def _url(params):
    return reverse('calidad_informacion') + '?' + urlencode(params)


def _resumen_visual(tarjetas):
    """Proyecciones de los mismos conteos, sin nuevas consultas ni score global."""
    pendientes = sorted(
        (t for t in tarjetas if t['control'].grupo == 'pendientes'),
        key=lambda t: (-t['total'], t['control'].codigo),
    )
    inconsistencias = sorted(
        (t for t in tarjetas if t['control'].grupo == 'inconsistencias'),
        key=lambda t: (-t['total'], t['control'].codigo),
    )
    maximo = max((t['total'] for t in pendientes), default=0)
    for tarjeta in pendientes:
        tarjeta['ancho_barra'] = tarjeta['total'] * 100 / maximo if maximo else 0
    segmentos = [
        {'clave': 'sin-casos', 'titulo': 'Sin casos detectados', 'total': sum(t['total'] == 0 for t in tarjetas)},
        {'clave': 'pendientes', 'titulo': 'Con información pendiente', 'total': sum(t['total'] > 0 for t in pendientes)},
        {'clave': 'inconsistencias', 'titulo': 'Con inconsistencias', 'total': sum(t['total'] > 0 for t in inconsistencias)},
    ]
    desplazamiento = 0
    for segmento in segmentos:
        segmento['longitud'] = segmento['total'] * 100 / len(tarjetas) if tarjetas else 0
        segmento['desfase'] = -desplazamiento
        desplazamiento += segmento['longitud']
    return {
        'pendientes': pendientes, 'inconsistencias': inconsistencias,
        'maximo_pendientes': maximo, 'segmentos_controles': segmentos,
        'controles_sin_casos': segmentos[0]['total'],
        'total_pendientes': sum(t['total'] for t in pendientes),
        'total_inconsistencias': sum(t['total'] for t in inconsistencias),
    }


def _registro(obj, control):
    tipo = control.entidad
    if tipo == 'fibra':
        extremos = {t.extremo: t for t in obj.terminaciones.all()}
        locations = sorted({t.puerto_odf.odf_obj.hub_site for t in extremos.values()})
        label = f'{obj.fibra_numero} · {obj.codigo_fibra}'
        context = obj.ruta.nombre if obj.ruta_id else 'Troncal pendiente'
        detail = ' · '.join(
            f'{e}: {extremos[e].puerto_odf.odf_obj.odf} / P{extremos[e].puerto_odf.puerto_odf}'
            if e in extremos else f'{e}: pendiente' for e in ('A', 'B')
        )
        site = ', '.join(locations) or 'Sin terminación ubicada'
        detail = f'{obj.get_estado_display()} · {detail}'
    elif tipo == 'troncal':
        label, context, site = obj.nombre, 'Troncal', 'Consultar recorrido'
        detail = f'{obj._puntos} punto(s) de recorrido' if hasattr(obj, '_puntos') else 'Sin tramos técnicos'
    elif tipo == 'odf':
        label, site = obj.odf, obj.hub_site
        context = f'{obj.sala} → {obj.rack}'
        detail = 'Ubicación por completar'
        if hasattr(obj, '_puertos_total'):
            detail = f'Capacidad: {obj.capacidad_puertos or 0} · Registrados: {obj._puertos_total}'
            if control.codigo == 'odfs_puertos':
                detail += f' · Por registrar: {obj.capacidad_puertos - obj._puertos_total}'
    elif tipo == 'puerto':
        label, site, context = f'{obj.odf_obj.odf} / P{obj.puerto_odf}', obj.odf_obj.hub_site, 'Puerto ODF'
        detail = f'Estado: {obj.get_estado_puerto_display()} · Terminación: {"sí" if obj.terminaciones_fibra.all() else "no"}'
    else:
        label, site, context = obj.nombre, obj.nombre, 'Site'
        detail = f'Latitud: {obj.latitud if obj.latitud is not None else "pendiente"} · Longitud: {obj.longitud if obj.longitud is not None else "pendiente"}'
    return {'nombre': label, 'contexto': context, 'site': site, 'detalle': detail,
            'url': reverse('asset_360', args=[tipo, obj.pk])}


@login_required
@screen_required('quality')
@require_GET
def calidad_informacion(request):
    site = request.GET.get('site', '').strip()[:150]
    query = request.GET.get('q', '').strip()[:150]
    codigo = request.GET.get('control', '').strip()
    sites = list(HubSite.objects.order_by('nombre').values_list('nombre', flat=True))
    site = next((nombre for nombre in sites if nombre.casefold() == site.casefold()), site)
    controles = controles_calidad(request.user, site)
    tarjetas = []
    for control in controles:
        tarjetas.append({
            'control': control, 'total': control.queryset.count(),
            'url': _url({'site': site, 'control': control.codigo}) + '#registros-calidad',
        })
    if codigo and codigo not in {c.codigo for c in controles}:
        raise Http404('Control de calidad no disponible para este usuario.')
    selected = next((c for c in tarjetas if c['control'].codigo == codigo), None)
    # La primera visita muestra el resumen visual. Los registros se consultan
    # solamente cuando se selecciona explícitamente un control.
    registros, page, previous, following = [], None, None, None
    if selected:
        control = selected['control']
        queryset = control.queryset
        if control.entidad == 'fibra':
            queryset = queryset.select_related('ruta').prefetch_related(Prefetch(
                'terminaciones', queryset=TerminacionFibra.objects.select_related('puerto_odf__odf_obj__rack_obj__sala__hub_site')
            ))
            if query:
                queryset = queryset.filter(Q(codigo_fibra__icontains=query) | Q(fibra_numero__icontains=query) | Q(ruta__nombre__icontains=query))
        elif control.entidad == 'odf':
            queryset = queryset.select_related('rack_obj__sala__hub_site')
            if query:
                queryset = queryset.filter(odf__icontains=query)
        elif control.entidad == 'puerto':
            queryset = queryset.select_related('odf_obj__rack_obj__sala__hub_site').prefetch_related('terminaciones_fibra')
            if query:
                queryset = queryset.filter(Q(puerto_odf__icontains=query) | Q(odf_obj__odf__icontains=query))
        elif query:
            queryset = queryset.filter(nombre__icontains=query)
        page = Paginator(queryset.order_by('pk'), 25).get_page(request.GET.get('page'))
        registros = [_registro(obj, control) for obj in page]
        params = {'site': site, 'control': control.codigo, 'q': query}
        if page.has_previous():
            previous = _url({**params, 'page': page.previous_page_number()}) + '#registros-calidad'
        if page.has_next():
            following = _url({**params, 'page': page.next_page_number()}) + '#registros-calidad'
    return render(request, 'mapa_inventario/calidad_informacion.html', {
        'sites': sites, 'site': site, 'site_desconocido': bool(site and site not in sites),
        'q': query, 'tarjetas': tarjetas, 'seleccionado': selected,
        **_resumen_visual(tarjetas),
        'actualizado': timezone.now(), 'url_resumen': _url({'site': site}),
        'registros': registros, 'page_obj': page, 'anterior': previous, 'siguiente': following,
    })
