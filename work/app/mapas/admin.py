from django.contrib import admin
from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
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

class EdicionSeguraAdmin(admin.ModelAdmin):
    """Snapshot firmado y escritura limitada para formularios de inventario."""
    def get_form(self, request, obj=None, **kwargs):
        if kwargs.get('fields') is not None:
            kwargs['fields'] = [f for f in kwargs['fields'] if f != 'revision_inventario']
        base = super().get_form(request, obj, **kwargs)
        if obj is None:
            return base
        model = self.model
        def valores(instance):
            return {f.attname: f.value_to_string(instance) for f in model._meta.concrete_fields}
        class FormularioSeguro(base):
            revision_inventario = forms.CharField(widget=forms.HiddenInput)
            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                if not self.is_bound:
                    self.initial['revision_inventario'] = signing.dumps(valores(self.instance), salt='fg-admin-edit')
            def clean(self):
                cleaned = super().clean()
                try:
                    original = signing.loads(cleaned.get('revision_inventario', ''), salt='fg-admin-edit')
                    actual = model.objects.select_for_update().get(pk=self.instance.pk)
                except (signing.BadSignature, model.DoesNotExist):
                    raise ValidationError('La referencia de edición no es válida. Recargue el registro.')
                if valores(actual) != original:
                    raise ValidationError('El registro cambió desde que abrió el formulario. Recargue antes de guardar.')
                return cleaned
        return FormularioSeguro

    def save_model(self, request, obj, form, change):
        if not change:
            return super().save_model(request, obj, form, change)
        campos = [f.name for f in obj._meta.concrete_fields if f.name in form.changed_data]
        if campos:
            obj.save(update_fields=campos)


@admin.register(OTU)
class OTUAdmin(EdicionSeguraAdmin):
    list_display = ('nombre', 'modelo', 'latitud', 'longitud')
    search_fields = ('nombre', 'modelo')
    list_filter = ('modelo',)


@admin.register(Ruta)
class RutaAdmin(EdicionSeguraAdmin):
    list_display = ('nombre', 'otu', 'distancia_m', 'olt', 'pon')
    search_fields = ('nombre', 'olt', 'pon')
    list_filter = ('otu',)
    list_select_related = ('otu',)


@admin.register(PuertoOTU)
class PuertoOTUAdmin(EdicionSeguraAdmin):
    list_display = ('otu', 'numero', 'estado', 'ruta_asociada')
    search_fields = ('otu__nombre',)
    list_filter = ('estado',)
    list_select_related = ('otu', 'ruta_asociada')


@admin.register(CoordenadaRuta)
class CoordenadaRutaAdmin(EdicionSeguraAdmin):
    list_display = (
        'ruta', 'orden', 'latitud', 'longitud',
        'tipo_trazado', 'inicio_segmento',
    )
    list_filter = ('tipo_trazado', 'inicio_segmento')
    search_fields = ('ruta__nombre',)
    list_select_related = ('ruta',)


@admin.register(Reserva)
class ReservaAdmin(EdicionSeguraAdmin):
    list_display = ('nombre', 'ruta', 'tipo', 'reserva_m')
    search_fields = ('nombre', 'ruta__nombre')
    list_filter = ('tipo',)
    list_select_related = ('ruta',)


@admin.register(IDRuta)
class IDRutaAdmin(EdicionSeguraAdmin):
    list_display = ('ruta', 'ruta_obj', 'id_onmsi')
    search_fields = ('ruta', 'ruta_obj__nombre')


@admin.register(HubSite)
class HubSiteAdmin(EdicionSeguraAdmin):
    list_display = ('nombre', 'latitud', 'longitud')
    search_fields = ('nombre',)


@admin.register(SalaTecnica)
class SalaTecnicaAdmin(EdicionSeguraAdmin):
    list_display = ('nombre', 'hub_site', 'estado')
    search_fields = ('nombre', 'hub_site__nombre')
    list_filter = ('estado',)


@admin.register(RackFisico)
class RackFisicoAdmin(EdicionSeguraAdmin):
    list_display = ('nombre', 'sala', 'estado')
    search_fields = ('nombre', 'sala__nombre', 'sala__hub_site__nombre')
    list_filter = ('estado',)


@admin.register(InventarioODF)
class InventarioODFAdmin(EdicionSeguraAdmin):
    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)
        class FormularioODF(base):
            def clean(self):
                cleaned = super().clean()
                if obj is None and (cleaned.get('capacidad_puertos') or 0) <= 0:
                    raise ValidationError('La capacidad debe ser mayor que cero.')
                return cleaned
        return FormularioODF

    def get_readonly_fields(self, request, obj=None):
        # Capacidad/ubicación existentes se gestionan por la operación ODF confirmada.
        return self.readonly_fields + (('capacidad_puertos', 'rack_obj') if obj else ())

    def save_model(self, request, obj, form, change):
        from .services.inventario import ajustar_puertos_a_capacidad
        super().save_model(request, obj, form, change)
        if not change:
            ajustar_puertos_a_capacidad(obj, obj.capacidad_puertos, usuario=request.user)

    list_display = (
        'odf', 'rack_obj', 'capacidad_puertos', 'puertos_ocupados',
        'puertos_libres', 'puertos_reservados',
    )
    search_fields = ('odf', 'rack_obj__nombre', 'rack_obj__sala__hub_site__nombre')
    readonly_fields = (
        'puertos_ocupados', 'puertos_libres', 'puertos_reservados',
    )


@admin.register(DetallePuertoODF)
class DetallePuertoODFAdmin(EdicionSeguraAdmin):
    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields + (('odf_obj', 'puerto_odf') if obj else ())

    list_display = ('odf_obj', 'puerto_odf', 'bandeja', 'estado_puerto', 'destino')
    search_fields = (
        'odf_obj__odf', 'puerto_odf', 'destino',
        'terminaciones_fibra__fibra__fibra_numero',
        'terminaciones_fibra__fibra__ruta__nombre',
    )
    list_filter = ('estado_puerto',)
    readonly_fields = ('estado_puerto',)


@admin.register(InventarioTramo)
class InventarioTramoAdmin(EdicionSeguraAdmin):
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
class NodoRedAdmin(EdicionSeguraAdmin):
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
class InventarioFibraAdmin(EdicionSeguraAdmin):
    list_display = (
        'codigo_fibra', 'ruta', 'fibra_numero', 'estado', 'origen_estado',
        'condicion_fisica', 'nombre_fibra',
    )
    search_fields = (
        'codigo_fibra', 'ruta__nombre', 'fibra_numero', 'nombre_fibra',
        'observaciones',
    )
    list_filter = ('estado', 'origen_estado', 'condicion_fisica')
    list_select_related = ('ruta',)

    def get_readonly_fields(self, request, obj=None):
        return (
            ('codigo_fibra', 'ruta', 'origen_estado')
            if obj is not None
            else ('origen_estado',)
        )

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
            obj.estado = 'SIN_INFORMACION'
            obj.origen_estado = 'NO_INFORMADO'
        super().save_model(request, obj, form, change)
        if estado_cambio:
            if estado_solicitado == 'SIN_INFORMACION':
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
