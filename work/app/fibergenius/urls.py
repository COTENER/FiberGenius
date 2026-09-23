"""
URL configuration for FiberGenius.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path,include
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from .health import health

@never_cache
def protected_admin_login(request, extra_context=None):
    """Admin delegates authentication to the existing throttled login."""
    if request.user.is_authenticated:
        return redirect('admin:index' if request.user.is_staff else 'mapa_inventario')
    return redirect_to_login(
        request.GET.get('next') or reverse('admin:index'),
        login_url=reverse('login'),
    )


admin.site.login = protected_admin_login

urlpatterns = [
    path('health/', health, name='health'),
    path('admin/', admin.site.urls),
    path('', include('mapas.urls')),  # Incluye las URLs de la app 'mapas'

]
