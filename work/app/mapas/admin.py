from django.contrib import admin
from .models import (
    OTU, Ruta, PuertoOTU, CoordenadaRuta, Reserva, IDRuta,
    EventoOTDR, Medicion, PruebaOTDR, EventoOTDRDetalle,
    HubSite, SalaTecnica, RackFisico, InventarioODF, DetallePuertoODF,
    NodoRed, InventarioTramo, InventarioFibra, FibraTramo,
    TerminacionFibra, EmpalmeFibra, AuditoriaPuertoODF, AuditoriaFibra,
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
    list_display = (
        'ruta', 'orden', 'latitud', 'longitud',
        'tipo_trazado', 'inicio_segmento',
    )
    list_filter = ('tipo_trazado', 'inicio_segmento')
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
    list_display = (
        'odf', 'rack_obj', 'capacidad_puertos', 'puertos_ocupados',
        'puertos_libres', 'puertos_reservados',
    )
    search_fields = ('odf', 'rack_obj__nombre', 'rack_obj__sala__hub_site__nombre')


@admin.register(DetallePuertoODF)
class DetallePuertoODFAdmin(admin.ModelAdmin):
    list_display = ('odf_obj', 'puerto_odf', 'bandeja', 'estado_puerto', 'destino')
    search_fields = (
        'odf_obj__odf', 'puerto_odf', 'destino',
        'terminaciones_fibra__fibra__fibra_numero',
        'terminaciones_fibra__fibra__ruta__nombre',
    )
    list_filter = ('estado_puerto',)


@admin.register(InventarioTramo)
class InventarioTramoAdmin(admin.ModelAdmin):
    list_display = (
        'ruta', 'codigo_tramo', 'tramo_secuencia', 'origen_nodo',
        'destino_nodo', 'capacidad_hilos', 'tipo_trazado', 'distancia_m',
        'estado',
    )
    search_fields = (
        'ruta__nombre', 'codigo_tramo', 'origen_nodo__codigo',
        'origen_nodo__nombre', 'destino_nodo__codigo',
        'destino_nodo__nombre', 'origen', 'destino',
    )
    list_filter = ('tipo_trazado', 'estado')
    list_select_related = ('ruta', 'origen_nodo', 'destino_nodo')

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        return (
            'ruta',
            'tramo_secuencia',
            'codigo_tramo',
            'origen_nodo',
            'destino_nodo',
        )


@admin.register(NodoRed)
class NodoRedAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'tipo', 'nombre', 'lote_importacion')
    search_fields = ('codigo', 'nombre')
    list_filter = ('tipo',)
    list_select_related = ('lote_importacion',)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        if (
            obj.tramos_como_origen.exists()
            or obj.tramos_como_destino.exists()
        ):
            return ('tipo', 'codigo')
        return ()


@admin.register(InventarioFibra)
class InventarioFibraAdmin(admin.ModelAdmin):
    list_display = (
        'ruta', 'fibra_numero', 'estado', 'origen_estado',
        'condicion_fisica', 'nombre_fibra',
    )
    search_fields = (
        'ruta__nombre', 'fibra_numero', 'nombre_fibra', 'destino',
        'observaciones',
    )
    list_filter = ('estado', 'origen_estado', 'condicion_fisica')
    list_select_related = ('ruta',)

    def get_readonly_fields(self, request, obj=None):
        return ('ruta', 'origen_estado') if obj is not None else ('origen_estado',)

    def save_model(self, request, obj, form, change):
        from .services.fibras import (
            asignar_ruta_fibra,
            establecer_estado_fibra_informado,
            restablecer_estado_fibra,
        )

        estado_solicitado = obj.estado
        ruta_solicitada = obj.ruta if not change else None
        estado_cambio = not change or 'estado' in form.changed_data
        if change:
            anterior = InventarioFibra.objects.get(pk=obj.pk)
            obj.estado = anterior.estado
            obj.origen_estado = anterior.origen_estado
        else:
            obj.ruta = None
            obj.estado = 'Desconocido'
            obj.origen_estado = 'NO_INFORMADO'
        super().save_model(request, obj, form, change)
        if estado_cambio:
            if estado_solicitado == 'Desconocido':
                restablecer_estado_fibra(
                    fibra=obj,
                    usuario=request.user,
                    origen='ADMIN',
                )
            else:
                establecer_estado_fibra_informado(
                    fibra=obj,
                    estado=estado_solicitado,
                    usuario=request.user,
                    origen='ADMIN',
                )
        if ruta_solicitada:
            asignar_ruta_fibra(
                fibra=obj,
                ruta=ruta_solicitada,
                usuario=request.user,
                origen='ADMIN',
            )
        obj.refresh_from_db()


@admin.register(FibraTramo)
class FibraTramoAdmin(admin.ModelAdmin):
    list_display = ('tramo', 'numero_hilo', 'estado', 'fibra', 'lote_importacion')
    search_fields = (
        'tramo__ruta__nombre', 'tramo__codigo_tramo',
        'numero_hilo', 'fibra__fibra_numero',
    )
    list_filter = ('estado',)
    list_select_related = ('tramo', 'tramo__ruta', 'fibra', 'lote_importacion')
    readonly_fields = tuple(field.name for field in FibraTramo._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return bool(obj is None and super().has_change_permission(request, obj))

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TerminacionFibra)
class TerminacionFibraAdmin(admin.ModelAdmin):
    list_display = ('fibra', 'extremo', 'puerto_odf', 'tipo_conector')
    list_select_related = ('fibra', 'fibra__ruta', 'puerto_odf', 'puerto_odf__odf_obj')
    readonly_fields = ('fibra', 'extremo', 'puerto_odf', 'tipo_conector', 'lote_importacion')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return bool(obj is None and super().has_change_permission(request, obj))

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(EmpalmeFibra)
admin.site.register(LoteImportacion)
admin.site.register(SiteAlias)


@admin.register(AuditoriaPuertoODF)
class AuditoriaPuertoODFAdmin(admin.ModelAdmin):
    list_display = (
        'creado_en', 'usuario', 'origen', 'accion', 'referencia_fibra',
        'referencia_puerto_anterior', 'referencia_puerto_nuevo',
        'estado_anterior', 'estado_nuevo',
    )
    list_filter = ('origen', 'accion', 'sincronizo_fibra')
    search_fields = (
        'usuario__username', 'referencia_fibra',
        'referencia_puerto_anterior', 'referencia_puerto_nuevo',
    )
    readonly_fields = [field.name for field in AuditoriaPuertoODF._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditoriaFibra)
class AuditoriaFibraAdmin(admin.ModelAdmin):
    list_display = (
        'creado_en', 'usuario', 'origen', 'accion', 'referencia_fibra',
        'valor_anterior', 'valor_nuevo',
    )
    list_filter = ('origen', 'accion')
    search_fields = ('usuario__username', 'referencia_fibra')
    readonly_fields = (
        'accion', 'origen', 'usuario', 'fibra', 'referencia_fibra',
        'valor_anterior', 'valor_nuevo', 'lote_importacion', 'metadatos',
        'creado_en',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return bool(obj is None and super().has_change_permission(request, obj))

    def has_delete_permission(self, request, obj=None):
        return False


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
