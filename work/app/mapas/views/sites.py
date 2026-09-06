import io
import logging
from decimal import Decimal, InvalidOperation

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from ..models import HubSite

logger = logging.getLogger('mapas')


@login_required
@permission_required('mapas.view_hubsite', raise_exception=True)
def gestion_sites(request):
    """Muestra la vista principal de Sites/Hubs con la lista actual."""
    sites = HubSite.objects.all().order_by('nombre')
    return render(request, 'administracion/sites_index.html', {'sites': sites})

@login_required
@permission_required(('mapas.add_hubsite', 'mapas.change_hubsite'), raise_exception=True)
@require_POST
def crear_site_manual(request):
    """Crea un Site/Hub manualmente."""
    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        latitud = request.POST.get('latitud')
        longitud = request.POST.get('longitud')
        direccion = request.POST.get('direccion', '').strip()

        if not nombre:
            messages.error(request, "El nombre del Hub/Site es obligatorio.")
            return redirect('gestion_sites')

        try:
            with transaction.atomic():
                if HubSite.objects.filter(nombre__iexact=nombre).exists():
                    messages.error(request, "El Site ya existe. Use una actualización explícita; no se cambió ningún dato.")
                    return redirect('gestion_sites')
                site = HubSite(nombre=nombre,
                    latitud=Decimal(latitud) if latitud else None,
                    longitud=Decimal(longitud) if longitud else None,
                    direccion=direccion)
                if (site.latitud is not None and not Decimal('-90') <= site.latitud <= Decimal('90')
                        or site.longitud is not None and not Decimal('-180') <= site.longitud <= Decimal('180')):
                    raise ValidationError('Coordenadas fuera de rango.')
                site.full_clean()
                site.save()
            messages.success(request, f"Site '{nombre}' guardado exitosamente.")
        except (TypeError, ValueError, InvalidOperation, ValidationError, IntegrityError):
            messages.error(request, "Las coordenadas del Site no son válidas.")
        except Exception:
            logger.exception("Error inesperado al guardar el Site %r", nombre)
            messages.error(request, "No se pudo guardar el Site.")

    return redirect('gestion_sites')

@login_required
@permission_required(('mapas.add_hubsite', 'mapas.change_hubsite'), raise_exception=True)
@require_POST
def importar_sites_csv(request):
    """Adapta la pantalla de Sites al importador oficial y auditable."""
    if request.method == 'POST' and 'csv_file' in request.FILES:
        archivo = request.FILES['csv_file']
        
        if not archivo.name.casefold().endswith('.csv'):
            messages.error(request, "El archivo debe tener extensión .csv")
            return redirect('gestion_sites')

        try:
            from .importacion import (
                _procesar_sites_inventario,
                _validar_archivo_subido,
                ejecutar_importacion_sincrona_auditada,
            )

            _validar_archivo_subido(archivo)
            _, resultado = ejecutar_importacion_sincrona_auditada(
                archivo,
                'sites_inventario',
                request.user,
                _procesar_sites_inventario,
            )
            messages.success(
                request,
                (
                    f"Importación exitosa: {resultado['creadas']} Sites creados, "
                    f"{resultado['actualizadas']} actualizados."
                ),
            )
        except (UnicodeDecodeError, TypeError, ValueError) as exc:
            messages.error(request, str(exc) or "El CSV contiene datos no válidos.")
        except Exception:
            logger.exception("Error inesperado al importar Sites")
            messages.error(request, "No se pudo procesar el CSV de Sites.")
            
    return redirect('gestion_sites')
