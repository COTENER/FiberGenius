import logging

from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
import json
from ..models import PerfilUmbral, Ruta

logger = logging.getLogger('mapas')


@login_required
@permission_required('mapas.view_perfilumbral', raise_exception=True)
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
@permission_required('mapas.view_perfilumbral', raise_exception=True)
@require_http_methods(['GET', 'POST'])
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

            permiso_por_accion = {
                'create': 'mapas.add_perfilumbral',
                'update': 'mapas.change_perfilumbral',
                'delete': 'mapas.delete_perfilumbral',
                'assign': 'mapas.change_ruta',
            }
            permiso = permiso_por_accion.get(action)
            if not permiso or not request.user.has_perm(permiso):
                raise PermissionDenied
            
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
                
        except PermissionDenied:
            raise
        except PerfilUmbral.DoesNotExist:
            return JsonResponse(
                {'status': 'error', 'message': 'Perfil de umbral no encontrado.'},
                status=404,
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            return JsonResponse(
                {'status': 'error', 'message': 'Los datos del perfil no son válidos.'},
                status=400,
            )
        except Exception:
            logger.exception("Error inesperado al gestionar perfiles de umbral")
            return JsonResponse(
                {'status': 'error', 'message': 'No se pudo completar la operación.'},
                status=500,
            )
            
    return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)
