from django.urls import path
from . import views
from django.contrib.auth import views as auth_views  # Importa las vistas de autenticación
from django.shortcuts import redirect

urlpatterns = [
    path('inventario/', views.mapa_inventario, name='mapa_inventario'), 
    path('inventario/dashboard/', views.dashboard_inventario, name='dashboard_inventario'),
    path('inventario/externo/', views.inventario_externo, name='inventario_externo'),
    path('inventario/interno/', views.inventario_interno, name='inventario_interno'),
    path('inventario/planta-interna/', views.planta_interna_view, name='planta_interna'),
    path('inventario/planta-externa/', views.planta_externa_view, name='planta_externa'),
    path('api/inventario/consulta/odfs/', views.api_odfs_paginados, name='api_odfs_paginados'),
    path('api/inventario/consulta/puertos/', views.api_puertos_paginados, name='api_puertos_paginados'),
    path('api/inventario/mapa/sites/<int:pk>/', views.api_mapa_site_navigation, name='api_mapa_site_navigation'),
    path('api/inventario/consulta/troncales/', views.api_troncales_paginadas, name='api_troncales_paginadas'),
    path('api/inventario/consulta/tramos/', views.api_tramos_paginados, name='api_tramos_paginados'),
    path('api/inventario/consulta/fibras/', views.api_fibras_paginadas, name='api_fibras_paginadas'),
    path('api/inventario/consulta/elementos/', views.api_elementos_paginados, name='api_elementos_paginados'),
    path('api/inventario/exportar/<str:recurso>/<str:formato>/', views.exportar_inventario, name='exportar_inventario'),
    path('api/inventario/buscar/', views.busqueda_global, name='busqueda_global'),
    path('api/inventario/360/<str:tipo>/<int:pk>/', views.asset_360, name='asset_360'),
    path('api/inventario/datos/', views.get_datos_inventario, name='api_datos_inventario'),
    path('api/inventario/fibras/create/', views.create_detalle_fibra, name='api_create_fibra'),
    path('api/inventario/fibras/import/', views.import_fibras_csv, name='api_import_fibras'),
    path('api/inventario/fibras/update/', views.update_detalle_fibra, name='api_update_fibra'),
    path('api/inventario/fibras/<str:ruta_nombre>/', views.get_detalle_fibras, name='api_detalle_fibras'),
    path('api/inventario/reservas/', views.get_detalle_reservas, name='api_detalle_reservas'),
    path('api/inventario/reservas/add/', views.add_reserva_manual, name='api_add_reserva'),
    path('api/inventario/reservas/import/', views.import_reservas_archivo, name='api_import_reservas'),
    path('api/inventario/odfs/', views.get_odfs, name='api_get_odfs'),
    path('api/inventario/odfs/puertos/create/', views.create_puerto_manual, name='api_create_puerto'),
    path('api/inventario/odfs/puertos/import/', views.import_puertos_archivo, name='api_import_puertos'),
    path('api/inventario/odfs/puertos/update/', views.update_detalle_puerto, name='api_update_puerto'),
    path('api/inventario/odfs/puertos/<str:odf_nombre>/', views.get_detalle_puertos, name='api_detalle_puertos'),
    path('api/inventario/puertos-odf-mapa/', views.get_puertos_odf_api, name='api_puertos_odf_mapa'),
    path('api/inventario/rutas/create/', views.create_ruta_manual, name='api_create_ruta'),
    path('api/inventario/rutas/update/', views.update_ruta_manual, name='api_update_ruta'),
    path('api/inventario/rutas/delete/', views.delete_ruta_manual, name='api_delete_ruta'),
    path('api/inventario/tramos/create/', views.create_tramo_manual, name='api_create_tramo'),
    path('api/inventario/odfs/create/', views.create_odf_manual, name='api_create_odf'),
    path('api/inventario/odfs/update/', views.update_odf_manual, name='api_update_odf'),
    path('api/inventario/odfs/delete/', views.delete_odf_manual, name='api_delete_odf'),
    path('api/inventario/odfs/vaciar/', views.vaciar_inventario_odf, name='vaciar_inventario_odf'),
    path('dashboard/', views.dashboard, name='dashboard'),  # Ruta para el dashboard
    path('ranking/', views.ranking, name='ranking'),
    path('export-csv/', views.export_top_rutas_csv, name='export_csv'),
    path('export-opticos-csv/', views.export_top_opticos_csv, name='export_opticos_csv'),
    path('login/', views.custom_login_view, name='login'),  # Vista de login personalizada
    path('logout/', views.custom_logout, name='logout'),  # Ruta para cerrar sesión
    path('Configuracion/', views.configuracion, name='configuracion'),
    path('usuarios/', views.lista_usuarios, name='lista_usuarios'),
    path('usuarios/exportar-csv/', views.exportar_usuarios_csv, name='exportar_usuarios_csv'),
    path('usuarios/crear/', views.crear_usuario, name='crear_usuario'),
    path('usuarios/editar/<int:pk>/', views.editar_usuario, name='editar_usuario'),
    path('usuarios/eliminar/<int:pk>/', views.eliminar_usuario, name='eliminar_usuario'),
    path('grupos/', views.lista_grupos, name='lista_grupos'),
    path('grupos/crear/', views.crear_grupo, name='crear_grupo'),
    path('grupos/editar/<int:pk>/', views.editar_grupo, name='editar_grupo'),
    path('grupos/eliminar/<int:pk>/', views.eliminar_grupo, name='eliminar_grupo'),
    path('cargar-csv/<str:tipo_csv>/', views.cargar_csv, name='cargar_csv'),
    path('Configuracion/eliminar-ruta/', views.eliminar_ruta_config, name='eliminar_ruta_config'),
    path('mi-perfil/cambiar-password/', views.cambiar_mi_password, name='cambiar_mi_password'),
    
    # Sites / Hubs
    path('administracion/sites/', views.gestion_sites, name='gestion_sites'),
    path('administracion/sites/crear/', views.crear_site_manual, name='crear_site_manual'),
    path('administracion/sites/importar/', views.importar_sites_csv, name='importar_sites_csv'),

    # Alarmas VeEX y Mapa en Vivo
    path('api/alarmas/webhook/', views.webhook_alarma, name='webhook_alarma'),
    path('api/alarmas/activas/', views.get_alarmas_activas, name='get_alarmas_activas'),
    path('mapa-alarmas/', views.mapa_alarmas, name='mapa_alarmas'),
    path('api/descargar-sor/<int:alarm_id>/', views.descargar_sor, name='descargar_sor'),
    path('api/traza-datos/<int:alarm_id>/', views.get_traza_data, name='get_traza_data'),
    path('mapa/visor-traza/<int:alarm_id>/', views.visor_traza, name='visor_traza'),
    
    # Trazas On-Demand
    path('mapa/traza-en-vivo/', views.visor_ondemand, name='visor_ondemand'),
    path('api/traza-en-vivo/iniciar/', views.iniciar_traza_ondemand, name='iniciar_traza_ondemand'),
    path('api/traza-en-vivo/status/<int:traza_id>/', views.get_on_demand_status, name='get_on_demand_status'),
    
    # Umbrales
    path('configuracion/umbrales/', views.configuracion_umbrales, name='configuracion_umbrales'),
    path('api/umbrales/', views.api_perfiles_umbral, name='api_perfiles_umbral'),
    
    # Motor Proactivo
    path('mapa/diagnostico-proactivo/', views.visor_proactivo, name='visor_proactivo'),
    path('mapa/establecer-referencia/', views.visor_establecer_referencia, name='visor_establecer_referencia'),
    path('api/proactivo/establecer-referencia/', views.api_establecer_referencia, name='api_establecer_referencia'),
    path('api/proactivo/promover-referencia/', views.promover_referencia, name='promover_referencia'),
    path('api/proactivo/referencia-datos/<int:ref_id>/', views.get_referencia_data, name='get_referencia_data'),
    path('api/proactivo/ondemand-datos/<int:traza_id>/', views.api_get_ondemand_data, name='api_get_ondemand_data'),
    path('api/proactivo/asignar-perfil/', views.api_asignar_perfil_ruta, name='api_asignar_perfil_ruta'),
    path('api/proactivo/coordenadas/<int:ruta_id>/', views.api_calcular_coordenadas, name='api_calcular_coordenadas'),
]
