"""
Paquete de vistas para la app mapas.
Re-exporta todas las vistas para mantener compatibilidad con urls.py.
"""
from .mapa import (
    obtener_equipos_desde_db,
    mapa_alarmas,
)

# Dashboard
from .dashboard import (
    dashboard,
    contar_rutas_db,
)

# Ranking
from .ranking import (
    ranking,
    export_top_rutas_csv,
    export_top_opticos_csv,
)

# Usuarios y Grupos
from .usuarios import (
    lista_usuarios,
    exportar_usuarios_csv,
    crear_usuario,
    editar_usuario,
    eliminar_usuario,
    lista_grupos,
    crear_grupo,
    editar_grupo,
    eliminar_grupo,
    obtener_permisos_agrupados,
    cambiar_mi_password,
    custom_login_view,
    custom_logout,
)

# Importación CSV
from .importacion import (
    configuracion,
    cargar_csv,
    descargar_plantilla_terminaciones_fibra,
    eliminar_ruta_config,
)
from .importacion_operaciones_puertos import (
    descargar_plantilla_operaciones_puertos,
    descargar_resultado_operaciones_puertos,
    importar_operaciones_puertos,
)
from .auditoria_puertos import api_historial_puertos

# APIs ONMSI
from .api import (
    ejecutar_script_actualizacion,
    ejecutar_alarmas,
    actualizar_medicion_individual,
    descargar_sor,
    get_traza_data,
)

# Inventario
from .inventario import (
    mapa_inventario,
    dashboard_inventario,
    inventario_externo,
    inventario_interno,
    planta_interna_view,
    planta_externa_view,
    get_datos_inventario,
    get_detalle_fibras,
    get_detalle_reservas,
    add_reserva_manual,
    import_reservas_archivo,
    create_detalle_fibra,
    import_fibras_csv,
    update_detalle_fibra,
    get_odfs,
    get_detalle_puertos,
    update_detalle_puerto,
    gestionar_conexion_puerto,
    vaciar_inventario_odf,
    create_ruta_manual,
    update_ruta_manual,
    delete_ruta_manual,
    create_odf_manual,
    update_odf_manual,
    delete_odf_manual,
    get_puertos_odf_api,
    create_puerto_manual,
    import_puertos_archivo,
)

from .operacion_inventario import (
    api_odfs_paginados,
    api_mapa_site_navigation,
    api_puertos_paginados,
    api_troncales_paginadas,
    api_tramos_paginados,
    api_fibras_paginadas,
    api_elementos_paginados,
    api_panel_troncal,
    create_tramo_manual,
    exportar_inventario,
    busqueda_global,
    asset_360,
)

# Sites / Hubs
from .sites import (
    gestion_sites,
    crear_site_manual,
    importar_sites_csv,
)

# Webhook y APIs de Alarmas VeEX
from .webhook import (
    webhook_alarma,
    get_alarmas_activas,
)

# Visor de Trazas
from .visor_traza import (
    visor_traza,
)

# Trazas On-Demand
from .on_demand import (
    visor_ondemand,
    iniciar_traza_ondemand,
    get_on_demand_status,
    descargar_traza_ondemand,
)

# Configuracion de Umbrales
from .umbrales import (
    configuracion_umbrales,
    api_perfiles_umbral,
)

# Motor Proactivo
from .proactivo import (
    visor_proactivo,
    api_establecer_referencia,
    visor_establecer_referencia,
    promover_referencia,
    get_referencia_data,
    api_asignar_perfil_ruta,
    api_get_ondemand_data,
    api_calcular_coordenadas,
)
