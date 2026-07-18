import io
import pandas as pd
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.db import transaction
from ..models import HubSite

@login_required
def gestion_sites(request):
    """Muestra la vista principal de Sites/Hubs con la lista actual."""
    sites = HubSite.objects.all().order_by('nombre')
    return render(request, 'administracion/sites_index.html', {'sites': sites})

@login_required
@permission_required('mapas.add_hubsite', raise_exception=True)
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
            HubSite.objects.update_or_create(
                nombre=nombre,
                defaults={
                    'latitud': float(latitud) if latitud else None,
                    'longitud': float(longitud) if longitud else None,
                    'direccion': direccion
                }
            )
            messages.success(request, f"Site '{nombre}' guardado exitosamente.")
        except Exception as e:
            messages.error(request, f"Error al guardar el Site: {e}")

    return redirect('gestion_sites')

@login_required
@permission_required('mapas.add_hubsite', raise_exception=True)
def importar_sites_csv(request):
    """Importa Sites/Hubs desde un archivo CSV."""
    if request.method == 'POST' and 'csv_file' in request.FILES:
        archivo = request.FILES['csv_file']
        
        if not archivo.name.endswith('.csv'):
            messages.error(request, "El archivo debe tener extensión .csv")
            return redirect('gestion_sites')

        try:
            decoded_file = archivo.read().decode('utf-8')
            df = pd.read_csv(io.StringIO(decoded_file))
            
            # Limpieza básica
            df.columns = [c.strip().lower() for c in df.columns]
            
            if 'nombre' not in df.columns:
                messages.error(request, "El CSV debe contener una columna llamada 'nombre'.")
                return redirect('gestion_sites')

            creados = 0
            actualizados = 0
            
            with transaction.atomic():
                for _, row in df.iterrows():
                    nombre = str(row['nombre']).strip()
                    if pd.isna(row['nombre']) or not nombre:
                        continue
                        
                    lat = row.get('latitud')
                    lon = row.get('longitud')
                    dir_str = str(row.get('direccion', '')).strip()

                    obj, created = HubSite.objects.update_or_create(
                        nombre=nombre,
                        defaults={
                            'latitud': float(lat) if pd.notna(lat) else None,
                            'longitud': float(lon) if pd.notna(lon) else None,
                            'direccion': dir_str if dir_str != 'nan' else None
                        }
                    )
                    if created:
                        creados += 1
                    else:
                        actualizados += 1

            messages.success(request, f"Importación exitosa: {creados} Sites creados, {actualizados} actualizados.")
        except Exception as e:
            messages.error(request, f"Ocurrió un error al procesar el CSV: {e}")
            
    return redirect('gestion_sites')
