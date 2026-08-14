from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import EmptyPage, Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from ..models import AuditoriaPuertoODF


@login_required
@permission_required('mapas.view_auditoriapuertoodf', raise_exception=True)
@require_GET
def api_historial_puertos(request):
    """Historial operativo compacto, global o filtrado por un puerto."""
    queryset = AuditoriaPuertoODF.objects.select_related(
        'usuario', 'fibra__ruta', 'lote_importacion',
    )
    puerto_id = str(request.GET.get('puerto_id', '')).strip()
    if puerto_id:
        if not puerto_id.isdigit():
            return JsonResponse(
                {'status': 'error', 'message': 'El puerto solicitado no es válido.'},
                status=400,
            )
        queryset = queryset.filter(
            Q(puerto_anterior_id=int(puerto_id))
            | Q(puerto_nuevo_id=int(puerto_id))
        )

    accion = str(request.GET.get('accion', '')).strip().upper()
    if accion:
        queryset = queryset.filter(accion=accion)
    origen = str(request.GET.get('origen', '')).strip().upper()
    if origen:
        queryset = queryset.filter(origen=origen)
    consulta = str(request.GET.get('q', '')).strip()
    if consulta:
        queryset = queryset.filter(
            Q(referencia_puerto_anterior__icontains=consulta)
            | Q(referencia_puerto_nuevo__icontains=consulta)
            | Q(referencia_fibra__icontains=consulta)
            | Q(usuario__username__icontains=consulta)
        )

    try:
        pagina = max(1, int(request.GET.get('page', 1)))
        tamano = min(100, max(10, int(request.GET.get('page_size', 50))))
    except (TypeError, ValueError):
        pagina, tamano = 1, 50
    paginador = Paginator(queryset, tamano)
    try:
        objeto_pagina = paginador.page(pagina)
    except EmptyPage:
        objeto_pagina = paginador.page(paginador.num_pages)

    datos = []
    for evento in objeto_pagina.object_list:
        datos.append({
            'id': evento.pk,
            'fecha': evento.creado_en.isoformat(),
            'usuario': evento.usuario.get_username() if evento.usuario else 'Sistema',
            'origen': evento.origen,
            'origen_nombre': evento.get_origen_display(),
            'accion': evento.accion,
            'accion_nombre': evento.get_accion_display(),
            'fibra': evento.referencia_fibra,
            'extremo': evento.extremo,
            'puerto_anterior': evento.referencia_puerto_anterior,
            'puerto_nuevo': evento.referencia_puerto_nuevo,
            'estado_anterior': evento.estado_anterior,
            'estado_nuevo': evento.estado_nuevo,
            'sincronizo_fibra': evento.sincronizo_fibra,
            'lote': str(evento.lote_importacion.codigo) if evento.lote_importacion else '',
        })
    return JsonResponse({
        'status': 'success',
        'data': datos,
        'pagination': {
            'page': objeto_pagina.number,
            'page_size': tamano,
            'pages': paginador.num_pages,
            'total': paginador.count,
        },
    })
