from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
import json
from ..models import PerfilUmbral, Ruta

@login_required
def configuracion_umbrales(request):
    """
    Renderiza la vista principal para configurar perfiles de umbrales.
    """
    perfiles = PerfilUmbral.objects.all().order_by('-fecha_actualizacion')
    rutas = Ruta.objects.all().order_by('nombre')
    
    context = {
        'perfiles': perfiles,
        'rutas': rutas,
    }
    return render(request, 'configuracion/umbrales_index.html', context)

@login_required
def api_perfiles_umbral(request):
    """
    API para crear, editar o eliminar perfiles de umbrales.
    """
    if request.method == 'GET':
        perfiles = PerfilUmbral.objects.all().values()
        return JsonResponse({'status': 'success', 'data': list(perfiles)})
        
    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
            action = data.get('action')
            
            if action == 'create' or action == 'update':
                perfil_id = data.get('id')
                defaults = {
                    'nombre_perfil': data.get('nombre_perfil'),
                    'splice_loss': float(data.get('splice_loss', 0.1)),
                    'connector_loss': float(data.get('connector_loss', 0.5)),
                    'reflectance': float(data.get('reflectance', -45.0)),
                    'attenuation': float(data.get('attenuation', 0.5)),
                    'end_of_fiber': float(data.get('end_of_fiber', 3.0)),
                    'atenuacion_minor_min': float(data.get('atenuacion_minor_min', 1.0)),
                    'atenuacion_major_min': float(data.get('atenuacion_major_min', 3.0)),
                    'atenuacion_critical_min': float(data.get('atenuacion_critical_min', 5.0)),
                    'margen_corte_fibra_db': float(data.get('margen_corte_fibra_db', 2.0)),
                }
                
                if perfil_id:
                    PerfilUmbral.objects.filter(id=perfil_id).update(**defaults)
                    mensaje = "Perfil actualizado correctamente."
                else:
                    PerfilUmbral.objects.create(**defaults)
                    mensaje = "Perfil creado correctamente."
                    
                return JsonResponse({'status': 'success', 'message': mensaje})
                
            elif action == 'delete':
                perfil_id = data.get('id')
                PerfilUmbral.objects.filter(id=perfil_id).delete()
                return JsonResponse({'status': 'success', 'message': 'Perfil eliminado correctamente.'})
                
            elif action == 'assign':
                # Asignar perfil a rutas
                perfil_id = data.get('perfil_id')
                ruta_ids = data.get('ruta_ids', [])
                if perfil_id:
                    perfil = PerfilUmbral.objects.get(id=perfil_id)
                    Ruta.objects.filter(id__in=ruta_ids).update(perfil_umbral=perfil)
                    return JsonResponse({'status': 'success', 'message': f'Perfil asignado a {len(ruta_ids)} rutas.'})
                
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
            
    return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)
