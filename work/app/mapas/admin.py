from django.contrib import admin
from .models import (
    OTU, Ruta, PuertoOTU, CoordenadaRuta, Reserva, IDRuta,
    EventoOTDR, Medicion, PruebaOTDR, EventoOTDRDetalle,
    HubSite, SalaTecnica, RackFisico, InventarioODF, DetallePuertoODF,
    InventarioTramo, InventarioFibra,
    TerminacionFibra, EmpalmeFibra,
    LoteImportacion, SiteAlias,
)


# =============================================================================
# Admin para Modelos Managed (ORM)
# =============================================================================

@admin.register(OTU)
class OTUAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'modelo', 'latitud', 'longitud')
    search_fields = ('nombre', 'modelo')
    list_filter = ('modelo',)


@admin.register(Ruta)
class RutaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'otu', 'distancia_m', 'olt', 'pon')
    search_fields = ('nombre', 'olt', 'pon')
    list_filter = ('otu',)
    list_select_related = ('otu',)


@admin.register(PuertoOTU)
class PuertoOTUAdmin(admin.ModelAdmin):
    list_display = ('otu', 'numero', 'estado', 'ruta_asociada')
    search_fields = ('otu__nombre',)
    list_filter = ('estado',)
    list_select_related = ('otu', 'ruta_asociada')


@admin.register(CoordenadaRuta)
class CoordenadaRutaAdmin(admin.ModelAdmin):
    list_display = ('ruta', 'orden', 'latitud', 'longitud')
    search_fields = ('ruta__nombre',)
    list_select_related = ('ruta',)


@admin.register(Reserva)
class ReservaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'ruta', 'tipo', 'reserva_m')
    search_fields = ('nombre', 'ruta__nombre')
    list_filter = ('tipo',)
    list_select_related = ('ruta',)


@admin.register(IDRuta)
class IDRutaAdmin(admin.ModelAdmin):
    list_display = ('ruta', 'ruta_obj', 'id_onmsi')
    search_fields = ('ruta', 'ruta_obj__nombre')


@admin.register(HubSite)
class HubSiteAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'latitud', 'longitud')
    search_fields = ('nombre',)


@admin.register(SalaTecnica)
class SalaTecnicaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'hub_site', 'estado')
    search_fields = ('nombre', 'hub_site__nombre')
    list_filter = ('estado',)


@admin.register(RackFisico)
class RackFisicoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'sala', 'estado')
    search_fields = ('nombre', 'sala__nombre', 'sala__hub_site__nombre')
    list_filter = ('estado',)


@admin.register(InventarioODF)
class InventarioODFAdmin(admin.ModelAdmin):
    list_display = ('odf', 'rack_obj', 'capacidad_puertos', 'puertos_ocupados', 'puertos_libres')
    search_fields = ('odf', 'rack_obj__nombre', 'rack_obj__sala__hub_site__nombre')


@admin.register(DetallePuertoODF)
class DetallePuertoODFAdmin(admin.ModelAdmin):
    list_display = ('odf_obj', 'puerto_odf', 'bandeja', 'estado_puerto', 'destino')
    search_fields = ('odf_obj__odf', 'puerto_odf', 'fibra', 'destino')
    list_filter = ('estado_puerto',)


@admin.register(InventarioTramo)
class InventarioTramoAdmin(admin.ModelAdmin):
    list_display = ('ruta', 'tramo_secuencia', 'tipo_trazado', 'distancia_m', 'estado')
    search_fields = ('ruta__nombre', 'origen', 'destino')
    list_filter = ('tipo_trazado', 'estado')
    list_select_related = ('ruta',)


@admin.register(InventarioFibra)
class InventarioFibraAdmin(admin.ModelAdmin):
    list_display = ('ruta', 'fibra_numero', 'estado', 'nombre_fibra', 'destino')
    search_fields = ('ruta__nombre', 'fibra_numero', 'nombre_fibra', 'destino')
    list_filter = ('estado',)
    list_select_related = ('ruta',)


admin.site.register(TerminacionFibra)
admin.site.register(EmpalmeFibra)
admin.site.register(LoteImportacion)
admin.site.register(SiteAlias)


# =============================================================================
# Admin de telemetría OTDR (solo lectura para operadores)
# =============================================================================

class ReadOnlyAdminMixin:
    """Mixin para hacer un ModelAdmin de solo lectura."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False





@admin.register(EventoOTDR)
class EventoOTDRAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('node', 'event_type', 'event_test_status', 'optical_distance_m', 'loss_db', 'latitude', 'longitude')
    search_fields = ('node', 'event_type')
    list_filter = ('event_type', 'event_test_status')
    list_per_page = 50


@admin.register(Medicion)
class MedicionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('node', 'result', 'fiber_length', 'acquisition_date', 'link_loss_alarm')
    search_fields = ('node',)
    list_filter = ('result', 'link_loss_alarm')
    list_per_page = 50


@admin.register(PruebaOTDR)
class PruebaOTDRAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('cable_id', 'fibra_id', 'estado_prueba', 'fecha_adquisicion', 'longitud_fibra_m')
    search_fields = ('cable_id', 'fibra_id')
    list_filter = ('estado_prueba',)
    list_per_page = 50


@admin.register(EventoOTDRDetalle)
class EventoOTDRDetalleAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('node', 'event_type', 'event_test_status', 'distance_m', 'loss_db')
    search_fields = ('node', 'event_type')
    list_filter = ('event_type', 'event_test_status')
    list_per_page = 50
