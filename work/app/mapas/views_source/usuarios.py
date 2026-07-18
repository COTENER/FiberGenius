"""
Vistas de gestión de usuarios, grupos, roles y permisos.
"""
import logging
from collections import defaultdict

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.models import User, Group, Permission
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.contrib import messages
from django.db.models import Q

from ..forms import CustomUserCreationForm, CustomUserChangeForm, GrupoForm

logger = logging.getLogger('mapas')


from datetime import timedelta
from django.utils.timezone import now

@login_required
@permission_required('auth.view_user', raise_exception=True)
def lista_usuarios(request):
    usuarios = User.objects.prefetch_related('groups', 'activity').order_by('username')
    
    # Define umbral de inactividad (ej: 15 minutos)
    umbral_online = now() - timedelta(minutes=15)
    
    for u in usuarios:
        if hasattr(u, 'activity') and u.activity.last_activity:
            u.is_online = u.activity.last_activity >= umbral_online
            u.last_activity_time = u.activity.last_activity
        else:
            u.is_online = False
            u.last_activity_time = None
            
    usuarios_online = sum(1 for u in usuarios if u.is_online)

    context = {
        'usuarios': usuarios,
        'usuarios_online': usuarios_online,
    }
    return render(request, 'usuarios/lista_usuarios.html', context)


# VISTA PARA CREAR USUARIOS ---
@login_required
@permission_required('auth.add_user', raise_exception=True)
def crear_usuario(request):
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Usuario creado correctamente.')
            return redirect('lista_usuarios')
    else:
        form = CustomUserCreationForm()

    context = {
        'form_profile': form,
        'titulo': 'Crear Nuevo Usuario'
    }
    return render(request, 'usuarios/formulario_usuario.html', context)


# --- VISTA PARA EDITAR USUARIOS ---
@login_required
@permission_required('auth.change_user', raise_exception=True)
def editar_usuario(request, pk):
    usuario = get_object_or_404(User, pk=pk)
    # Un no-superusuario no puede editar a un superusuario
    if usuario.is_superuser and not request.user.is_superuser:
        messages.error(request, 'No tienes permiso para editar a un superusuario.')
        return redirect('lista_usuarios')
    # Manejo del formulario de cambio de datos del usuario
    if request.method == 'POST' and 'update_profile' in request.POST:
        form_profile = CustomUserChangeForm(request.POST, instance=usuario)
        if form_profile.is_valid():
            form_profile.save()
            messages.success(request, f'Perfil de "{usuario.username}" actualizado correctamente.')
            return redirect('editar_usuario', pk=usuario.pk)
    else:
        form_profile = CustomUserChangeForm(instance=usuario)

    # Manejo del formulario de cambio de contraseña
    if request.method == 'POST' and 'change_password' in request.POST:
        form_password = SetPasswordForm(usuario, request.POST)
        if form_password.is_valid():
            user_saved = form_password.save()
            update_session_auth_hash(request, user_saved)
            messages.success(request, f'Contraseña de "{usuario.username}" cambiada correctamente.')
            return redirect('editar_usuario', pk=usuario.pk)
    else:
        form_password = SetPasswordForm(usuario)

    context = {
        'form_profile': form_profile,
        'form_password': form_password,
        'usuario_editado': usuario,
        'titulo': f'Editando a {usuario.username}'
    }
    return render(request, 'usuarios/formulario_usuario.html', context)


@login_required
@permission_required('auth.delete_user', raise_exception=True)
def eliminar_usuario(request, pk):
    usuario_a_eliminar = get_object_or_404(User, pk=pk)

    if request.user.pk == usuario_a_eliminar.pk:
        messages.error(request, 'No puedes eliminar tu propia cuenta de usuario.')
        return redirect('lista_usuarios')

    username = usuario_a_eliminar.username
    usuario_a_eliminar.delete()

    messages.success(request, f'El usuario "{username}" ha sido eliminado correctamente.')
    return redirect('lista_usuarios')


# LISTAR GRUPOS
@login_required
@permission_required('auth.view_group', raise_exception=True)
def lista_grupos(request):
    grupos = Group.objects.prefetch_related('permissions').all()
    context = {'grupos': grupos}
    return render(request, 'usuarios/lista_grupos.html', context)


def obtener_permisos_agrupados():
    permisos = Permission.objects.filter(
        Q(content_type__app_label='mapas') |
        Q(content_type__app_label='auth', content_type__model__in=['user', 'group'])
    ).select_related('content_type').order_by('content_type__model', 'codename')

    agrupados = {
        "Operaciones": {
            "Mapa VeEX": [],
            "Dashboard": [],
            "Ranking": [],
        },
        "Inventario": {
            "Mapa de Inventario": [],
            "Dashboard Inventario": [],
            "Inventario Externo": [],
            "Inventario Interno": [],
            "Planta Interna": [],
            "Planta Externa": [],
        },
        "Administración": {
            "Importación": [],
            "Umbrales OTDR": [],
            "Sites / Hubs": [],
            "Usuarios": [],
            "Grupos / Roles": [],
        }
    }

    for p in permisos:
        modelo_key = p.content_type.model
        code = p.codename

        if code == 'can_view_reports':
            agrupados["Operaciones"]["Dashboard"].append(p)
            agrupados["Operaciones"]["Ranking"].append(p)
            agrupados["Inventario"]["Dashboard Inventario"].append(p)
        elif modelo_key in ['evento', 'eventootdr', 'medicion', 'otu', 'puertootu']:
            agrupados["Operaciones"]["Mapa VeEX"].append(p)
        elif modelo_key == 'ruta':
            agrupados["Inventario"]["Inventario Externo"].append(p)
            agrupados["Inventario"]["Planta Externa"].append(p)
            agrupados["Inventario"]["Mapa de Inventario"].append(p)
            if 'add_' in code or 'change_' in code:
                agrupados["Administración"]["Importación"].append(p)
        elif modelo_key == 'idruta':
            agrupados["Inventario"]["Inventario Externo"].append(p)
        elif modelo_key in ['inventariotramo', 'inventariofibra']:
            agrupados["Inventario"]["Inventario Externo"].append(p)
            agrupados["Inventario"]["Planta Externa"].append(p)
        elif modelo_key in ['inventarioodf', 'inventariopuertoodf']:
            agrupados["Inventario"]["Inventario Interno"].append(p)
            agrupados["Inventario"]["Planta Interna"].append(p)
        elif modelo_key in ['site', 'hub', 'nodos']:
            agrupados["Administración"]["Sites / Hubs"].append(p)
        elif modelo_key in ['umbral', 'perfilumbral']:
            agrupados["Administración"]["Umbrales OTDR"].append(p)
        elif modelo_key == 'user':
            agrupados["Administración"]["Usuarios"].append(p)
        elif modelo_key == 'group':
            agrupados["Administración"]["Grupos / Roles"].append(p)
        elif modelo_key == 'coordenadaruta':
            agrupados["Inventario"]["Mapa de Inventario"].append(p)
            agrupados["Inventario"]["Planta Externa"].append(p)
        elif modelo_key == 'reserva':
            agrupados["Inventario"]["Planta Externa"].append(p)
        else:
            agrupados["Administración"]["Importación"].append(p)

    # Retornamos todo, incluyendo módulos vacíos para que la UI muestre el cascarón completo.
    return agrupados


# CREAR GRUPO
@login_required
@permission_required('auth.add_group', raise_exception=True)
def crear_grupo(request):
    if request.method == 'POST':
        form = GrupoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Grupo creado correctamente.')
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                from django.http import JsonResponse
                return JsonResponse({'success': True})
            return redirect('lista_grupos')
    else:
        form = GrupoForm()

    context = {
        'form': form,
        'permisos_agrupados': obtener_permisos_agrupados(),
        'permisos_seleccionados': [],
        'titulo': 'Crear Nuevo Grupo'
    }
    return render(request, 'usuarios/formulario_grupo.html', context)


# EDITAR GRUPO
@login_required
@permission_required('auth.change_group', raise_exception=True)
def editar_grupo(request, pk):
    grupo = get_object_or_404(Group, pk=pk)
    if request.method == 'POST':
        form = GrupoForm(request.POST, instance=grupo)
        if form.is_valid():
            form.save()
            messages.success(request, f'Grupo "{grupo.name}" actualizado.')
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                from django.http import JsonResponse
                return JsonResponse({'success': True})
            return redirect('lista_grupos')
    else:
        form = GrupoForm(instance=grupo)

    permisos_seleccionados = list(grupo.permissions.values_list('id', flat=True))

    context = {
        'form': form,
        'permisos_agrupados': obtener_permisos_agrupados(),
        'permisos_seleccionados': permisos_seleccionados,
        'titulo': f'Editando Grupo: {grupo.name}'
    }
    return render(request, 'usuarios/formulario_grupo.html', context)


# ELIMINAR GRUPO
@login_required
@permission_required('auth.delete_group', raise_exception=True)
def eliminar_grupo(request, pk):
    grupo = get_object_or_404(Group, pk=pk)
    nombre = grupo.name
    grupo.delete()
    messages.success(request, f'Grupo "{nombre}" eliminado.')
    return redirect('lista_grupos')


@login_required
def cambiar_mi_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, '¡Tu contraseña ha sido actualizada exitosamente!')
            return redirect('mapa')
    else:
        form = PasswordChangeForm(request.user)

    context = {
        'form': form,
        'titulo': 'Cambiar Mi Contraseña'
    }
    return render(request, 'usuarios/cambiar_mi_password.html', context)


def custom_logout(request):
    logout(request)
    return redirect('login')

import csv
from django.http import HttpResponse

@login_required
@permission_required('auth.view_user', raise_exception=True)
def exportar_usuarios_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="usuarios_export.csv"'

    writer = csv.writer(response)
    # Header del CSV
    writer.writerow([
        'Usuario', 'Email', 'Nombre', 'Apellido', 
        'Activo', 'Staff', 'Superusuario', 'Ultimo Ingreso', 
        'Ultima Actividad', 'Estado Online', 'Grupos'
    ])

    usuarios = User.objects.prefetch_related('groups', 'activity').order_by('username')
    umbral_online = now() - timedelta(minutes=15)

    for u in usuarios:
        is_online = False
        last_act = ""
        if hasattr(u, 'activity') and u.activity.last_activity:
            is_online = u.activity.last_activity >= umbral_online
            last_act = u.activity.last_activity.strftime("%Y-%m-%d %H:%M:%S")
            
        grupos_str = ", ".join([g.name for g in u.groups.all()])
        
        writer.writerow([
            u.username,
            u.email,
            u.first_name,
            u.last_name,
            "Si" if u.is_active else "No",
            "Si" if u.is_staff else "No",
            "Si" if u.is_superuser else "No",
            u.last_login.strftime("%Y-%m-%d %H:%M:%S") if u.last_login else "",
            last_act,
            "Online" if is_online else "Offline",
            grupos_str
        ])

    return response
