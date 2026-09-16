from django import forms
from django.contrib.auth.models import User, Group, Permission
from django.contrib.auth.forms import UserCreationForm, UserChangeForm, AuthenticationForm
from django.db.models import Q

# 1. FORMULARIO PARA CREAR USUARIO
class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True, help_text="Requerido. Se necesita un email válido.")
    first_name = forms.CharField(max_length=30, required=False, label="Nombre")
    last_name = forms.CharField(max_length=30, required=False, label="Apellido")
    
    # --- AQUÍ AGREGAMOS EL SELECTOR DE GRUPO (RadioSelect de único elemento) ---
    grupo_rol = forms.ModelChoiceField(
        queryset=Group.objects.all(),
        widget=forms.RadioSelect, 
        required=False,
        label="Asignar Perfil / Rol",
        empty_label="Sin Perfil Asignado"
    )

    is_staff = forms.BooleanField(required=False, label="¿Es staff?", help_text="Acceso al panel de administración.")
    is_superuser = forms.BooleanField(required=False, label="Superusuario", help_text="Permisos totales.")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = UserCreationForm.Meta.fields + ('email', 'first_name', 'last_name', 'is_staff', 'is_superuser')

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not actor or not actor.is_superuser:
            self.fields.pop('is_staff', None)
            self.fields.pop('is_superuser', None)

    def save(self, commit=True):
        user = super().save(commit=False)
        if commit:
            user.save()
            grupo = self.cleaned_data.get('grupo_rol')
            if grupo:
                user.groups.set([grupo])
            else:
                user.groups.clear()
        return user

# 2. FORMULARIO PARA EDITAR USUARIO
class CustomUserChangeForm(UserChangeForm):
    password = None # Ocultamos el password en edición básica

    grupo_rol = forms.ModelChoiceField(
        queryset=Group.objects.all(),
        widget=forms.RadioSelect,
        required=False,
        label="Asignar Perfil / Rol",
        empty_label="Sin Perfil Asignado"
    )

    class Meta:
        model = User
        fields = [
            'username',
            'email',
            'first_name',
            'last_name',
            'is_active',
            'is_staff',
            'is_superuser'
        ]

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not actor or not actor.is_superuser:
            self.fields.pop('is_staff', None)
            self.fields.pop('is_superuser', None)
        if self.instance and self.instance.pk:
            self.fields['grupo_rol'].initial = self.instance.groups.first()

    def save(self, commit=True):
        user = super().save(commit=False)
        if commit:
            user.save()
            grupo = self.cleaned_data.get('grupo_rol')
            if grupo:
                user.groups.set([grupo])
            else:
                user.groups.clear()
        return user

# 3. FORMULARIO DE LOGIN (No cambia)
class CustomAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.error_messages['invalid_login'] = "Usuario o contraseña incorrectos."
        self.error_messages['inactive'] = "Esta cuenta está inactiva."

# 4. FORMULARIO PARA CREAR/EDITAR GRUPOS (El que te di antes)
class PermisosPersonalizadosField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        # Solo retornamos la acción traducida, sin el nombre del modelo
        # El nombre del modelo lo manejaremos en el template
        codigo = obj.codename.split('_')[0] # 'add', 'change', 'delete', 'view'
        
        acciones = {
            'add': 'Crear',
            'change': 'Editar',
            'delete': 'Eliminar',
            'view': 'Ver',
            'can': 'Acción Especial' # Para tus permisos custom
        }
        
        # Casos especiales
        if obj.codename == 'can_import_csv': return "Importar CSV"
        if obj.codename == 'can_measure_route': return "Medir Ruta"
        if obj.codename == 'can_view_reports': return "⭐ Funcionalidad: Ver Reportes"
        
        return acciones.get(codigo, obj.name)


# --- 2. FORMULARIO DE GRUPO ACTUALIZADO ---
class GrupoForm(forms.ModelForm):
    permissions = PermisosPersonalizadosField( # <--- USAMOS TU CLASE PERSONALIZADA AQUÍ
        queryset=Permission.objects.filter(
            Q(content_type__app_label='mapas') | 
            Q(content_type__app_label='auth', content_type__model__in=['user', 'group'])
        ).order_by('content_type__model', 'codename'),
        
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Permisos / Accesos"
    )

    class Meta:
        model = Group
        fields = ['name', 'permissions']
        labels = {
            'name': 'Nombre del Grupo (Rol)',
        }
