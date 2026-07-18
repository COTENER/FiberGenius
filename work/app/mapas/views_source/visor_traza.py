from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from mapas.models import AlarmaVeex

@login_required
def visor_traza(request, alarm_id):
    """
    Renderiza la interfaz gráfica (SaaS) para visualizar la traza OTDR.
    """
    # Buscamos primero por ID exacto de BD si viene en la URL para evitar choques
    incident_id = request.GET.get('incident_id')
    log_id = request.GET.get('log_id')
    
    def process_event_type(obj):
        evt = (obj.alarm_type or '').lower().replace(' ', '')
        if 'loss' in evt or 'atenuaci' in evt:
            obj.tipo_traducido = 'Atenuación'
            obj.color_tipo = '#f59e0b' # var(--warning) naranja
            obj.bg_class = 'bg-warning'
        elif 'reflect' in evt or 'reflexi' in evt:
            obj.tipo_traducido = 'Reflexión'
            obj.color_tipo = '#3b82f6' # azul
            obj.bg_class = 'bg-info'
        elif 'break' in evt or 'corte' in evt:
            obj.tipo_traducido = 'Corte de Fibra'
            obj.color_tipo = '#ef4444' # var(--danger) rojo
            obj.bg_class = 'bg-danger'
        else:
            obj.tipo_traducido = obj.alarm_type
            obj.color_tipo = 'var(--text-primary)'
            obj.bg_class = ''
            
    alarma = None
    padre = None
    es_historico = False

    if log_id:
        from mapas.models import EventLog
        log_historico = EventLog.objects.filter(id=log_id).first()
        if log_historico:
            alarma = log_historico
            padre = log_historico.incident
            es_historico = True
    elif incident_id:
        alarma = AlarmaVeex.objects.filter(id=incident_id).first()
        padre = alarma

    # Si no vinieron IDs exactos en la URL, buscamos por el ID de VeEX
    if not alarma:
        alarma = AlarmaVeex.objects.filter(veex_alarm_id=alarm_id).first()
        padre = alarma

        if not alarma:
            from mapas.models import EventLog
            log_historico = EventLog.objects.filter(veex_alarm_id=alarm_id).first()
            if log_historico:
                alarma = log_historico
                padre = log_historico.incident
                es_historico = True
            else:
                alarma = get_object_or_404(AlarmaVeex, veex_alarm_id=alarm_id)
                padre = alarma
            
    # Obtener el historial completo del incidente
    historial = list(padre.event_logs.all().order_by('-fecha_registro')) if padre else []
    
    if alarma:
        process_event_type(alarma)
        
    for log in historial:
        process_event_type(log)
    
    context = {
        'alarma': alarma,
        'padre': padre,
        'veex_alarm_id': alarm_id,
        'es_historico': es_historico,
        'historial': historial
    }
    
    return render(request, 'visor_traza.html', context)
