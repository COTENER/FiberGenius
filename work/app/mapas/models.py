import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, F, Q
from django.db.models.functions import Coalesce, Lower


# =============================================================================
# MODELOS ORM (Managed) — Equipos y Rutas de Fibra Óptica
# =============================================================================

class OTU(models.Model):
    """
    Representa un equipo OTU físico.
    Datos consolidados de OTU-ONMSIv1.csv.
    """
    nombre = models.CharField(max_length=100, unique=True, db_index=True, verbose_name="Nombre OTU")
    modelo = models.CharField(max_length=50, blank=True, null=True)
    latitud = models.DecimalField(max_digits=10, decimal_places=7, null=True)
    longitud = models.DecimalField(max_digits=10, decimal_places=7, null=True)
    lote_importacion = models.ForeignKey(
        'LoteImportacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='equipos_otu',
    )

    def __str__(self):
        return self.nombre

    class Meta:
        db_table = 'inv_equipos_otu'
        verbose_name = "Equipo OTU"
        verbose_name_plural = "Equipos OTU"


class PerfilUmbral(models.Model):
    """
    Define los umbrales de sensibilidad de alarmas para las pruebas OTDR de VeEX.
    """
    nombre_perfil = models.CharField(max_length=150, unique=True, verbose_name="Nombre del Perfil")
    splice_loss = models.FloatField(default=0.1, verbose_name="Pérdida por Empalme (dB)")
    connector_loss = models.FloatField(default=0.5, verbose_name="Pérdida por Conector (dB)")
    reflectance = models.FloatField(default=-45.0, verbose_name="Reflectancia (dB)")
    attenuation = models.FloatField(default=0.5, verbose_name="Atenuación (dB/km)")
    end_of_fiber = models.FloatField(default=3.0, verbose_name="Fin de Fibra (dB)")
    
    # Motor Proactivo: Tolerancias de Atenuación
    atenuacion_minor_min = models.FloatField(default=1.0, verbose_name="Atenuación Minor (Min dB)")
    atenuacion_major_min = models.FloatField(default=3.0, verbose_name="Atenuación Major (Min dB)")
    atenuacion_critical_min = models.FloatField(default=5.0, verbose_name="Atenuación Critical (Min dB)")
    
    # Motor Proactivo: Corte de Fibra (Inteligente respecto al piso de ruido)
    margen_corte_fibra_db = models.FloatField(default=2.0, verbose_name="Margen sobre piso de ruido para Corte (dB)")
    
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.nombre_perfil

    class Meta:
        db_table = 'inv_perfil_umbrales'
        verbose_name = "Perfil de Umbrales"
        verbose_name_plural = "Perfiles de Umbrales"


class Ruta(models.Model):
    """
    Representa una ruta de fibra óptica.
    Combina información de ruta_otu.csv y los nombres de archivo de la carpeta 'Consolidado'.
    """
    nombre = models.CharField(max_length=150, unique=True, db_index=True, verbose_name="Nombre de Ruta")
    otu = models.ForeignKey(OTU, on_delete=models.SET_NULL, null=True, blank=True, related_name='rutas', verbose_name="OTU Asociado")
    distancia_m = models.FloatField(null=True, blank=True, verbose_name="Distancia (m)")
    distancia_declarada_m = models.FloatField(
        null=True,
        blank=True,
        verbose_name='Distancia oficial declarada (m)',
    )
    capacidad_declarada_hilos = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Capacidad oficial declarada',
    )
    hilos_ocupados_declarados = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Hilos ocupados declarados',
    )
    hilos_reservados_declarados = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Hilos reservados declarados',
    )
    hilos_libres_declarados = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Hilos libres declarados',
    )
    reservas_declaradas_m = models.FloatField(
        null=True,
        blank=True,
        verbose_name='Reserva oficial declarada (m)',
    )
    fuente_declaracion = models.CharField(
        max_length=255,
        blank=True,
        default='',
        verbose_name='Fuente de la declaración',
    )
    fecha_declaracion = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Fecha de la declaración',
    )
    declarado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='rutas_declaradas',
        verbose_name='Declarado por',
    )
    olt = models.CharField(max_length=100, blank=True, null=True, verbose_name="OLT")
    slot_olt = models.IntegerField(null=True, blank=True)
    puerto_olt = models.IntegerField(null=True, blank=True)
    pon = models.CharField(max_length=50, blank=True, null=True, verbose_name="PON")
    enlace = models.URLField(max_length=255, blank=True, null=True, verbose_name="Enlace PON View")
    perfil_umbral = models.ForeignKey(PerfilUmbral, on_delete=models.SET_NULL, null=True, blank=True, related_name='rutas', verbose_name="Perfil de Umbrales")
    lote_importacion = models.ForeignKey(
        'LoteImportacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='rutas',
    )

    def __str__(self):
        return self.nombre

    def clean(self):
        super().clean()
        declarados = (
            self.hilos_ocupados_declarados,
            self.hilos_reservados_declarados,
            self.hilos_libres_declarados,
        )
        if (
            self.capacidad_declarada_hilos is not None
            and sum(valor or 0 for valor in declarados)
            > self.capacidad_declarada_hilos
        ):
            raise ValidationError({
                'capacidad_declarada_hilos': (
                    'La suma de hilos ocupados, reservados y libres '
                    'declarados no puede superar la capacidad declarada.'
                ),
            })
        if not self.pk:
            return
        puerto = PuertoOTU.objects.filter(ruta_asociada_id=self.pk).select_related('otu').first()
        if puerto and self.otu_id and puerto.otu_id != self.otu_id:
            raise ValidationError({
                'otu': 'El OTU de la ruta debe coincidir con el OTU del puerto asociado.'
            })

    def save(self, *args, **kwargs):
        self.nombre = (self.nombre or '').strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    class Meta:
        db_table = 'inv_rutas_fibra'
        verbose_name = "Ruta de Fibra"
        verbose_name_plural = "Rutas de Fibra"
        ordering = ['nombre']
        constraints = [
            models.UniqueConstraint(
                Lower('nombre'),
                name='uq_ruta_nombre_ci',
            ),
            models.CheckConstraint(
                condition=(
                    Q(distancia_declarada_m__isnull=True)
                    | Q(distancia_declarada_m__gte=0)
                ),
                name='ck_ruta_dist_declarada_no_neg',
            ),
            models.CheckConstraint(
                condition=(
                    Q(capacidad_declarada_hilos__isnull=True)
                    | Q(capacidad_declarada_hilos__gt=0)
                ),
                name='ck_ruta_cap_declarada_positiva',
            ),
            models.CheckConstraint(
                condition=(
                    Q(reservas_declaradas_m__isnull=True)
                    | Q(reservas_declaradas_m__gte=0)
                ),
                name='ck_ruta_reserva_declarada_no_neg',
            ),
            models.CheckConstraint(
                condition=(
                    Q(capacidad_declarada_hilos__isnull=True)
                    | Q(
                        capacidad_declarada_hilos__gte=(
                            Coalesce('hilos_ocupados_declarados', 0)
                            + Coalesce('hilos_reservados_declarados', 0)
                            + Coalesce('hilos_libres_declarados', 0)
                        )
                    )
                ),
                name='ck_ruta_contadores_en_capacidad',
            ),
        ]
        permissions = [
            ("can_view_reports", "Puede ver Reportes (Dashboard/Ranking)"),
        ]


class PuertoOTU(models.Model):
    """
    Representa un puerto específico de un equipo OTU.
    Datos de las filas de OTU-ONMSIv1.csv.
    """
    ESTADO_CHOICES = [
        ('Monitoreado', 'Monitoreado'),
        ('Libre', 'Libre'),
        ('Ocupado', 'Ocupado'),
        ('Desconocido', 'Desconocido'),
    ]
    otu = models.ForeignKey(OTU, on_delete=models.CASCADE, related_name='puertos', verbose_name="Equipo OTU")
    numero = models.IntegerField(verbose_name="Número de Puerto")
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='Desconocido')
    ruta_asociada = models.OneToOneField(Ruta, on_delete=models.SET_NULL, null=True, blank=True, related_name='puerto', verbose_name="Ruta Asociada")
    lote_importacion = models.ForeignKey(
        'LoteImportacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='puertos_otu',
    )

    def __str__(self):
        return f"{self.otu.nombre} - Puerto {self.numero}"

    def clean(self):
        super().clean()
        if self.ruta_asociada_id and self.ruta_asociada.otu_id not in (None, self.otu_id):
            raise ValidationError({
                'ruta_asociada': 'La ruta pertenece a un OTU diferente al del puerto.'
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        if self.ruta_asociada_id and self.ruta_asociada.otu_id is None:
            Ruta.objects.filter(pk=self.ruta_asociada_id).update(otu_id=self.otu_id)

    class Meta:
        db_table = 'inv_puertos_otu'
        verbose_name = "Puerto de OTU"
        verbose_name_plural = "Puertos de OTU"
        unique_together = ('otu', 'numero')
        ordering = ['otu', 'numero']


class CoordenadaRuta(models.Model):
    """
    Representa un punto geográfico (lat, lon) que forma parte del trazado de una Ruta.
    Datos de los multiples archivos CSV en la carpeta 'Consolidado'.
    """
    ruta = models.ForeignKey(Ruta, on_delete=models.CASCADE, related_name='coordenadas', verbose_name="Ruta")
    latitud = models.DecimalField(max_digits=10, decimal_places=7)
    longitud = models.DecimalField(max_digits=10, decimal_places=7)
    orden = models.PositiveIntegerField(verbose_name="Orden del trazado")
    tipo_trazado = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        verbose_name="Tipo de trazado geográfico",
    )
    inicio_segmento = models.BooleanField(
        default=False,
        verbose_name="Inicio de segmento geográfico",
        help_text="Indica que este punto inicia un nuevo segmento visual del trazado.",
    )
    # Campos de compatibilidad durante la transición. La segmentación geográfica
    # nueva no depende de InventarioTramo, pero se conservan temporalmente para
    # poder desplegar y revertir la fase 1 sin perder relaciones históricas.
    tramo_secuencia = models.PositiveIntegerField(null=True, blank=True, verbose_name="Secuencia del Tramo", help_text="Identificador de sub-tramo dentro de la ruta.")
    tramo = models.ForeignKey(
        'InventarioTramo',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='coordenadas',
        verbose_name="Tramo relacionado",
    )
    lote_importacion = models.ForeignKey(
        'LoteImportacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='coordenadas_ruta',
    )

    def __str__(self):
        return f"Punto {self.orden} de {self.ruta.nombre}"

    def clean(self):
        super().clean()
        if self.tramo_id and self.tramo.ruta_id != self.ruta_id:
            raise ValidationError({'tramo': 'El tramo debe pertenecer a la misma ruta.'})

    def save(self, *args, **kwargs):
        if self.tramo_id:
            self.ruta_id = self.tramo.ruta_id
            self.tramo_secuencia = self.tramo.tramo_secuencia
        self.full_clean()
        return super().save(*args, **kwargs)

    class Meta:
        db_table = 'inv_rutas_coordenadas'
        verbose_name = "Coordenada de Ruta"
        verbose_name_plural = "Coordenadas de Ruta"
        ordering = ['ruta', 'orden']
        unique_together = ('ruta', 'orden')
        constraints = [
            models.CheckConstraint(
                condition=Q(latitud__gte=-90) & Q(latitud__lte=90),
                name='ck_coordenada_ruta_latitud',
            ),
            models.CheckConstraint(
                condition=Q(longitud__gte=-180) & Q(longitud__lte=180),
                name='ck_coordenada_ruta_longitud',
            ),
        ]


class Reserva(models.Model):
    """
    Representa un punto de reserva o landmark en el mapa.
    Datos de reservas.csv.
    """
    ruta = models.ForeignKey(Ruta, on_delete=models.CASCADE, related_name='reservas', verbose_name="Ruta Asociada")
    nombre = models.CharField(max_length=180, verbose_name="Nombre Landmark")
    reserva_m = models.FloatField(null=True, blank=True, verbose_name="Reserva (m)")
    latitud = models.DecimalField(max_digits=10, decimal_places=7)
    longitud = models.DecimalField(max_digits=10, decimal_places=7)
    tipo = models.CharField(max_length=50, blank=True, null=True, verbose_name="Tipo (Site, Mufa, etc.)")
    tipo_original_cliente = models.CharField(max_length=100, blank=True)
    codigo = models.CharField(max_length=150, null=True, blank=True)
    orden_en_ruta = models.PositiveIntegerField(null=True, blank=True)
    progresiva_m = models.FloatField(null=True, blank=True)
    estado = models.CharField(max_length=30, default='POR_CONFIRMAR')
    atributos = models.JSONField(default=dict, blank=True)
    tramo = models.ForeignKey(
        'InventarioTramo',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reservas',
        verbose_name='Tramo relacionado',
    )
    lote_importacion = models.ForeignKey(
        'LoteImportacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reservas',
    )

    def __str__(self):
        return self.nombre

    def clean(self):
        super().clean()
        if self.tramo_id and self.tramo.ruta_id != self.ruta_id:
            raise ValidationError({'tramo': 'El tramo debe pertenecer a la misma ruta.'})

    def save(self, *args, **kwargs):
        if self.tramo_id:
            self.ruta_id = self.tramo.ruta_id
        self.nombre = (self.nombre or '').strip()
        self.codigo = (self.codigo or '').strip() or None
        self.full_clean()
        return super().save(*args, **kwargs)

    class Meta:
        db_table = 'inv_reservas_landmarks'
        verbose_name = "Reserva/Landmark"
        verbose_name_plural = "Reservas/Landmarks"
        constraints = [
            models.UniqueConstraint(
                fields=['ruta', 'nombre', 'latitud', 'longitud'],
                name='uq_reserva_ruta_nombre_coordenada',
            ),
            models.UniqueConstraint(
                fields=['codigo'],
                condition=Q(codigo__isnull=False) & ~Q(codigo=''),
                name='uq_reserva_codigo',
            ),
            models.CheckConstraint(
                condition=Q(reserva_m__isnull=True) | Q(reserva_m__gte=0),
                name='ck_reserva_m_no_negativa',
            ),
            models.CheckConstraint(
                condition=Q(latitud__gte=-90) & Q(latitud__lte=90),
                name='ck_reserva_latitud',
            ),
            models.CheckConstraint(
                condition=Q(longitud__gte=-180) & Q(longitud__lte=180),
                name='ck_reserva_longitud',
            ),
            models.CheckConstraint(
                condition=Q(progresiva_m__isnull=True) | Q(progresiva_m__gte=0),
                name='ck_reserva_progresiva',
            ),
        ]


class IDRuta(models.Model):
    """
    Tabla simple para mapear Rutas con sus IDs de ONMSI.
    """
    ruta = models.CharField(max_length=150, verbose_name="Ruta", db_column='ruta_id')
    id_onmsi = models.IntegerField(verbose_name="ID ONMSI", db_index=True)
    ruta_obj = models.ForeignKey(
        Ruta,
        on_delete=models.CASCADE,
        related_name='identificadores_onmsi',
        verbose_name='Ruta relacionada',
    )
    lote_importacion = models.ForeignKey(
        'LoteImportacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='identificadores_ruta',
    )

    def __str__(self):
        return f"{self.ruta} - ID: {self.id_onmsi}"

    def clean(self):
        if self.ruta_obj_id:
            self.ruta = self.ruta_obj.nombre
        super().clean()

    def save(self, *args, **kwargs):
        if self.ruta_obj_id:
            self.ruta = self.ruta_obj.nombre
        self.full_clean()
        return super().save(*args, **kwargs)

    class Meta:
        db_table = 'inv_rutas_ids'
        verbose_name = "ID de Ruta"
        verbose_name_plural = "IDs de Rutas"
        constraints = [
            models.UniqueConstraint(fields=['id_onmsi'], name='uq_idruta_onmsi'),
            models.UniqueConstraint(
                fields=['ruta_obj'],
                condition=Q(ruta_obj__isnull=False),
                name='uq_idruta_ruta_obj',
            ),
        ]


class NodoRed(models.Model):
    """Identidad estable de un punto de la topología técnica de la red."""

    TIPOS = [
        ('SITE', 'Site'),
        ('ODF', 'ODF'),
        ('MUFA', 'Mufa'),
        ('CAMARA', 'Cámara'),
        ('POSTE', 'Poste'),
        ('CAJA_EMPALME', 'Caja de empalme'),
        ('PUNTO', 'Punto intermedio'),
        ('OTRO', 'Otro'),
    ]

    tipo = models.CharField(max_length=30, choices=TIPOS)
    codigo = models.CharField(max_length=150)
    nombre = models.CharField(max_length=180, blank=True)
    odf_obj = models.OneToOneField(
        'InventarioODF',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='nodo_red',
        verbose_name='ODF canónico',
    )
    hub_site_obj = models.OneToOneField(
        'HubSite',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='nodo_red',
        verbose_name='Site canónico',
    )
    lote_importacion = models.ForeignKey(
        'LoteImportacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='nodos_red',
    )

    def __str__(self):
        return self.nombre or f'{self.get_tipo_display()} {self.codigo}'

    def clean(self):
        super().clean()
        if self.odf_obj_id and self.hub_site_obj_id:
            raise ValidationError(
                'Un nodo no puede representar simultáneamente un ODF y un Site.'
            )
        if self.odf_obj_id and self.tipo != 'ODF':
            raise ValidationError({
                'tipo': 'Un nodo relacionado con un ODF debe ser de tipo ODF.'
            })
        if self.hub_site_obj_id and self.tipo != 'SITE':
            raise ValidationError({
                'tipo': 'Un nodo relacionado con un Site debe ser de tipo SITE.'
            })
        if not self.pk:
            return
        anterior = type(self).objects.filter(pk=self.pk).only(
            'tipo',
            'codigo',
        ).first()
        if not anterior:
            return
        identidad_cambio = (
            anterior.tipo != self.tipo
            or anterior.codigo.casefold() != self.codigo.casefold()
        )
        if identidad_cambio and (
            self.tramos_como_origen.exists()
            or self.tramos_como_destino.exists()
            or self.fibras_como_origen.exists()
            or self.fibras_como_destino.exists()
        ):
            raise ValidationError({
                'codigo': (
                    'No se puede cambiar el tipo o código de un nodo que '
                    'ya está referenciado por un tramo.'
                ),
            })

    def save(self, *args, **kwargs):
        self.tipo = (self.tipo or '').strip().upper()
        self.codigo = (self.codigo or '').strip().upper()
        self.nombre = (self.nombre or '').strip()
        if not self.odf_obj_id and not self.hub_site_obj_id:
            if self.tipo == 'ODF' and self.codigo:
                self.odf_obj = InventarioODF.objects.filter(
                    odf__iexact=self.codigo,
                ).first()
            elif self.tipo == 'SITE' and self.codigo:
                self.hub_site_obj = HubSite.objects.filter(
                    nombre__iexact=self.codigo,
                ).first()
        if self.odf_obj_id:
            self.tipo = 'ODF'
            self.codigo = self.odf_obj.odf.strip().upper()
            self.nombre = self.odf_obj.odf.strip()
        elif self.hub_site_obj_id:
            self.tipo = 'SITE'
            self.codigo = self.hub_site_obj.nombre.strip().upper()
            self.nombre = self.hub_site_obj.nombre.strip()
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = set(update_fields) | {
                'tipo', 'codigo', 'nombre', 'odf_obj', 'hub_site_obj',
            }
        self.full_clean()
        return super().save(*args, **kwargs)

    class Meta:
        db_table = 'inv_nodos_red'
        verbose_name = 'Nodo de red'
        verbose_name_plural = 'Nodos de red'
        ordering = ['tipo', 'codigo']
        constraints = [
            models.UniqueConstraint(
                F('tipo'),
                Lower('codigo'),
                name='uq_nodo_tipo_codigo_ci',
            ),
            models.CheckConstraint(
                condition=~Q(codigo=''),
                name='ck_nodo_codigo_no_vacio',
            ),
            models.CheckConstraint(
                condition=Q(tipo__in=(
                    'SITE',
                    'ODF',
                    'MUFA',
                    'CAMARA',
                    'POSTE',
                    'CAJA_EMPALME',
                    'PUNTO',
                    'OTRO',
                )),
                name='ck_nodo_tipo_valido',
            ),
            models.CheckConstraint(
                condition=Q(odf_obj__isnull=True) | Q(tipo='ODF'),
                name='ck_nodo_odf_tipo',
            ),
            models.CheckConstraint(
                condition=Q(hub_site_obj__isnull=True) | Q(tipo='SITE'),
                name='ck_nodo_site_tipo',
            ),
            models.CheckConstraint(
                condition=Q(odf_obj__isnull=True) | Q(hub_site_obj__isnull=True),
                name='ck_nodo_canonico_unico',
            ),
        ]


class InventarioTramo(models.Model):
    """
    Representa las propiedades técnicas y logísticas de un segmento o tramo específico
    de una ruta de fibra óptica.
    Datos poblados desde el CSV de 'Inventario Técnico'.
    """
    ESTADOS_INVENTARIO = [
        ('INCOMPLETO', 'Incompleto'),
        ('VALIDADO', 'Validado'),
    ]

    ruta = models.ForeignKey(Ruta, on_delete=models.CASCADE, related_name='tramos_inventario', verbose_name="Ruta Asociada")
    tramo_secuencia = models.PositiveIntegerField(verbose_name="Secuencia del Tramo")
    codigo_tramo = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name='Código del tramo',
    )
    origen_nodo = models.ForeignKey(
        NodoRed,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='tramos_como_origen',
        verbose_name='Nodo de origen',
    )
    destino_nodo = models.ForeignKey(
        NodoRed,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='tramos_como_destino',
        verbose_name='Nodo de destino',
    )
    tipo_trazado = models.CharField(max_length=50, blank=True, null=True, verbose_name="Tipo de Trazado (Ej: AEREO, SOTERRADO)")
    estado = models.CharField(max_length=50, blank=True, null=True, verbose_name="Estado")
    distancia_m = models.FloatField(blank=True, null=True, verbose_name="Distancia del Tramo (m)")
    mufas = models.IntegerField(blank=True, null=True, default=0, verbose_name="Número de Mufas")
    splitters = models.IntegerField(blank=True, null=True, default=0, verbose_name="Número de Splitters")
    reservas_m = models.FloatField(blank=True, null=True, default=0.0, verbose_name="Metraje de Reservas (m)")
    capacidad = models.CharField(max_length=100, blank=True, null=True, verbose_name="Capacidad (Ej: 144 Hilos)")
    capacidad_hilos = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Capacidad de hilos',
    )
    tipo_fibra = models.CharField(max_length=100, blank=True, null=True, verbose_name="Tipo de Fibra")
    origen = models.CharField(max_length=150, blank=True, null=True, verbose_name="Origen (Sobrescribe OLT/OTU)")
    destino = models.CharField(max_length=150, blank=True, null=True, verbose_name="Destino (Sobrescribe OLT/OTU)")
    hub_site = models.CharField(max_length=150, blank=True, null=True, verbose_name="Hub/Site")
    marca_modelo = models.CharField(max_length=150, blank=True, null=True, verbose_name="Marca / Modelo")
    serial = models.CharField(max_length=100, blank=True, null=True, verbose_name="Serial")
    odf_nombre = models.CharField(max_length=150, blank=True, null=True, verbose_name="Nombre ODF")
    hilos_ocupados = models.IntegerField(blank=True, null=True, default=0, verbose_name="Hilos Ocupados")
    hilos_libres = models.IntegerField(blank=True, null=True, default=0, verbose_name="Hilos Libres")
    hilos_reservados = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Hilos reservados',
    )
    observaciones = models.TextField(blank=True, default='')
    estado_inventario = models.CharField(
        max_length=20,
        choices=ESTADOS_INVENTARIO,
        default='INCOMPLETO',
        db_index=True,
        verbose_name='Estado de completitud',
    )
    lote_importacion = models.ForeignKey(
        'LoteImportacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tramos_inventario',
    )
    
    def __str__(self):
        return f"{self.ruta.nombre} - Tramo {self.tramo_secuencia} ({self.tipo_trazado})"

    def clean(self):
        super().clean()
        if self.estado_inventario == 'VALIDADO':
            obligatorios = {
                'codigo_tramo': self.codigo_tramo,
                'origen_nodo': self.origen_nodo_id,
                'destino_nodo': self.destino_nodo_id,
                'distancia_m': self.distancia_m,
                'capacidad_hilos': self.capacidad_hilos,
            }
            faltantes = [
                campo
                for campo, valor in obligatorios.items()
                if valor in (None, '')
            ]
            if faltantes:
                raise ValidationError({
                    campo: 'Este campo es obligatorio para validar el tramo.'
                    for campo in faltantes
                })

        contadores = (
            self.hilos_ocupados,
            self.hilos_reservados,
            self.hilos_libres,
        )
        if (
            self.estado_inventario == 'VALIDADO'
            and self.capacidad_hilos is not None
            and sum(valor or 0 for valor in contadores)
            > self.capacidad_hilos
        ):
            raise ValidationError({
                'capacidad_hilos': (
                    'La suma de hilos ocupados, reservados y libres '
                    'no puede superar la capacidad del tramo.'
                ),
            })

        if not self.pk:
            return

        anterior = type(self).objects.filter(pk=self.pk).only(
            'ruta_id',
        ).first()
        if (
            anterior
            and anterior.ruta_id != self.ruta_id
            and self.fibras_tramo.exists()
        ):
            raise ValidationError({
                'ruta': (
                    'No se puede mover el tramo a otra ruta porque ya tiene '
                    'posiciones de fibra registradas.'
                ),
            })

        if self.capacidad_hilos is None:
            return
        excedidas = []
        for numero_hilo in self.fibras_tramo.values_list(
            'numero_hilo',
            flat=True,
        ):
            coincidencia = re.fullmatch(
                r'F([1-9]\d*)',
                str(numero_hilo or '').strip().upper(),
            )
            if (
                coincidencia
                and int(coincidencia.group(1)) > self.capacidad_hilos
            ):
                excedidas.append(str(numero_hilo))
        if excedidas:
            raise ValidationError({
                'capacidad_hilos': (
                    f'La capacidad {self.capacidad_hilos} dejaría fuera '
                    f'posiciones existentes: {", ".join(excedidas[:10])}.'
                ),
            })

    def save(self, *args, **kwargs):
        if self.capacidad_hilos is not None:
            self.capacidad = f'{self.capacidad_hilos} Hilos'
        if self.origen_nodo_id:
            self.origen = self.origen_nodo.codigo
        if self.destino_nodo_id:
            self.destino = self.destino_nodo.codigo
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields = set(update_fields)
            if 'capacidad_hilos' in update_fields:
                update_fields.add('capacidad')
            if 'origen_nodo' in update_fields or 'origen_nodo_id' in update_fields:
                update_fields.add('origen')
            if 'destino_nodo' in update_fields or 'destino_nodo_id' in update_fields:
                update_fields.add('destino')
            kwargs['update_fields'] = update_fields
        self.full_clean()
        return super().save(*args, **kwargs)

    class Meta:
        db_table = 'inv_tramos'
        verbose_name = "Tramo de Inventario"
        verbose_name_plural = "Tramos de Inventario"
        unique_together = ('ruta', 'tramo_secuencia')
        ordering = ['ruta', 'tramo_secuencia']
        constraints = [
            models.CheckConstraint(
                condition=Q(distancia_m__isnull=True) | Q(distancia_m__gte=0),
                name='ck_tramo_distancia_no_negativa',
            ),
            models.CheckConstraint(
                condition=Q(mufas__isnull=True) | Q(mufas__gte=0),
                name='ck_tramo_mufas_no_negativas',
            ),
            models.CheckConstraint(
                condition=Q(splitters__isnull=True) | Q(splitters__gte=0),
                name='ck_tramo_splitters_no_negativos',
            ),
            models.CheckConstraint(
                condition=Q(reservas_m__isnull=True) | Q(reservas_m__gte=0),
                name='ck_tramo_reserva_no_negativa',
            ),
            models.CheckConstraint(
                condition=Q(hilos_ocupados__isnull=True) | Q(hilos_ocupados__gte=0),
                name='ck_tramo_hilos_ocupados',
            ),
            models.CheckConstraint(
                condition=Q(hilos_libres__isnull=True) | Q(hilos_libres__gte=0),
                name='ck_tramo_hilos_libres',
            ),
            models.UniqueConstraint(
                F('ruta'),
                Lower('codigo_tramo'),
                condition=Q(codigo_tramo__isnull=False) & ~Q(codigo_tramo=''),
                name='uq_tramo_codigo_ci',
            ),
            models.CheckConstraint(
                condition=Q(capacidad_hilos__isnull=True) | Q(capacidad_hilos__gt=0),
                name='ck_tramo_capacidad_positiva',
            ),
            models.CheckConstraint(
                condition=(
                    Q(origen_nodo__isnull=True)
                    | Q(destino_nodo__isnull=True)
                    | ~Q(origen_nodo=F('destino_nodo'))
                ),
                name='ck_tramo_nodos_distintos',
            ),
            models.CheckConstraint(
                condition=Q(hilos_reservados__isnull=True) | Q(hilos_reservados__gte=0),
                name='ck_tramo_hilos_reservados',
            ),
            models.CheckConstraint(
                condition=Q(estado_inventario__in=(
                    'INCOMPLETO',
                    'VALIDADO',
                )),
                name='ck_tramo_estado_inventario',
            ),
            models.CheckConstraint(
                condition=(
                    Q(capacidad_hilos__isnull=True)
                    | Q(
                        capacidad_hilos__gte=(
                            Coalesce('hilos_ocupados', 0)
                            + Coalesce('hilos_reservados', 0)
                            + Coalesce('hilos_libres', 0)
                        )
                    )
                ),
                name='ck_tramo_contadores_en_capacidad',
            ),
        ]


class InventarioFibra(models.Model):
    """
    Representa el estado y detalle de cada hilo/fibra individual dentro de una ruta troncal.
    Datos poblados desde el Archivo C.
    """
    ORIGENES_ESTADO = [
        ('INFORMADO', 'Informado'),
        ('INFERIDO_TRAMOS', 'Inferido desde tramos'),
        ('NO_INFORMADO', 'No informado'),
    ]
    CONDICIONES_FISICAS = [
        ('OPERATIVA', 'Operativa'),
        ('CON_FALLA', 'Con falla'),
        ('SIN_VERIFICAR', 'Sin verificar'),
    ]

    ruta = models.ForeignKey(
        Ruta,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='fibras_inventario',
        verbose_name="Ruta Asociada",
        help_text=(
            "Puede quedar pendiente mientras solo se conoce la terminación "
            "física ODF/puerto del hilo."
        ),
    )
    fibra_numero = models.CharField(max_length=50, verbose_name="Número de Fibra/Hilo")
    estado = models.CharField(
        max_length=50,
        choices=[
            ('Libre', 'Libre'),
            ('Ocupado', 'Ocupado'),
            ('Reservado', 'Reservado'),
            ('Desconocido', 'Desconocido'),
        ],
        default='Desconocido',
        verbose_name="Estado",
    )
    origen_estado = models.CharField(
        max_length=20,
        choices=ORIGENES_ESTADO,
        default='NO_INFORMADO',
        db_index=True,
        verbose_name='Origen del estado',
    )
    condicion_fisica = models.CharField(
        max_length=20,
        choices=CONDICIONES_FISICAS,
        default='SIN_VERIFICAR',
        db_index=True,
        verbose_name='Condición física',
    )
    nombre_fibra = models.CharField(max_length=150, blank=True, null=True, verbose_name="Nombre/Descripción (Si está ocupada/reservada)")
    observaciones = models.TextField(blank=True, default='', verbose_name='Observaciones')
    origen_odf = models.CharField(max_length=150, blank=True, null=True, verbose_name="Origen ODF")
    destino = models.CharField(max_length=150, blank=True, null=True, verbose_name="Destino")
    origen_nodo = models.ForeignKey(
        NodoRed,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='fibras_como_origen',
        verbose_name='Nodo inicial del recorrido',
    )
    destino_nodo = models.ForeignKey(
        NodoRed,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='fibras_como_destino',
        verbose_name='Nodo final del recorrido',
    )
    tipo_conector = models.CharField(max_length=100, blank=True, null=True, verbose_name="Tipo de Conector")
    lote_importacion = models.ForeignKey(
        'LoteImportacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='fibras_legacy',
    )

    def __str__(self):
        return (
            f"{self.nombre_troncal} - Fibra {self.fibra_numero} "
            f"({self.estado})"
        )

    @property
    def nombre_troncal(self):
        return self.ruta.nombre if self.ruta_id else "Troncal pendiente"

    @property
    def es_provisional(self):
        return self.ruta_id is None

    def clean(self):
        super().clean()
        if (
            self.origen_estado == 'NO_INFORMADO'
            and self.estado != 'Desconocido'
        ):
            raise ValidationError({
                'origen_estado': (
                    'Una fibra sin origen de estado solo puede tener estado '
                    'Desconocido.'
                ),
            })
        if (
            self.origen_nodo_id is not None
            and self.origen_nodo_id == self.destino_nodo_id
        ):
            raise ValidationError({
                'destino_nodo': (
                    'El origen y el destino del recorrido deben ser distintos.'
                ),
            })

    def save(self, *args, **kwargs):
        self.fibra_numero = (self.fibra_numero or '').strip().upper()
        if self.origen_nodo_id:
            self.origen_odf = (
                self.origen_nodo.nombre or self.origen_nodo.codigo
            )
        if self.destino_nodo_id:
            self.destino = (
                self.destino_nodo.nombre or self.destino_nodo.codigo
            )
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields = set(update_fields)
            if 'origen_nodo' in update_fields or 'origen_nodo_id' in update_fields:
                update_fields.add('origen_odf')
            if 'destino_nodo' in update_fields or 'destino_nodo_id' in update_fields:
                update_fields.add('destino')
            kwargs['update_fields'] = update_fields
        self.full_clean()
        return super().save(*args, **kwargs)

    class Meta:
        db_table = 'inv_fibras_detalle'
        verbose_name = "Detalle de Fibra"
        verbose_name_plural = "Detalle de Fibras"
        ordering = ['ruta', 'fibra_numero']
        constraints = [
            models.UniqueConstraint(
                F('ruta'), Lower('fibra_numero'),
                condition=Q(ruta__isnull=False),
                name='uq_fibra_ruta_numero_ci',
            ),
            models.CheckConstraint(
                condition=Q(estado__in=(
                    'Libre',
                    'Ocupado',
                    'Reservado',
                    'Desconocido',
                )),
                name='ck_inventario_fibra_estado',
            ),
            models.CheckConstraint(
                condition=Q(origen_estado__in=(
                    'INFORMADO',
                    'INFERIDO_TRAMOS',
                    'NO_INFORMADO',
                )),
                name='ck_fibra_origen_estado_valido',
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(origen_estado='NO_INFORMADO')
                    | Q(estado='Desconocido')
                ),
                name='ck_fibra_estado_origen_coherente',
            ),
            models.CheckConstraint(
                condition=Q(condicion_fisica__in=(
                    'OPERATIVA',
                    'CON_FALLA',
                    'SIN_VERIFICAR',
                )),
                name='ck_fibra_condicion_fisica_valida',
            ),
            models.CheckConstraint(
                condition=(
                    Q(origen_nodo__isnull=True)
                    | Q(destino_nodo__isnull=True)
                    | ~Q(origen_nodo=F('destino_nodo'))
                ),
                name='ck_fibra_recorrido_nodos_dist',
            ),
        ]


class FibraTramo(models.Model):
    """Posición física de un hilo dentro de un tramo técnico."""

    ESTADOS = [
        ('Libre', 'Libre'),
        ('Ocupado', 'Ocupado'),
        ('Reservado', 'Reservado'),
        ('Desconocido', 'Desconocido'),
    ]

    tramo = models.ForeignKey(
        InventarioTramo,
        on_delete=models.CASCADE,
        related_name='fibras_tramo',
    )
    numero_hilo = models.CharField(max_length=50)
    estado = models.CharField(
        max_length=20,
        choices=ESTADOS,
        default='Desconocido',
    )
    fibra = models.ForeignKey(
        InventarioFibra,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asignaciones_tramo',
    )
    observaciones = models.TextField(blank=True, default='')
    lote_importacion = models.ForeignKey(
        'LoteImportacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='fibras_tramo',
    )

    def __str__(self):
        return f'{self.tramo} - Hilo {self.numero_hilo} ({self.estado})'

    def clean(self):
        super().clean()
        numero_hilo = (self.numero_hilo or '').strip().upper()
        coincidencia = re.fullmatch(r'F([1-9]\d*)', numero_hilo)
        if coincidencia is None:
            raise ValidationError({
                'numero_hilo': 'Use el formato F1, F2, F3...'
            })
        if (
            self.tramo_id
            and self.tramo.capacidad_hilos is not None
            and int(coincidencia.group(1)) > self.tramo.capacidad_hilos
        ):
            raise ValidationError({
                'numero_hilo': (
                    f'{numero_hilo} excede la capacidad '
                    f'{self.tramo.capacidad_hilos} del tramo.'
                )
            })
        if (
            self.tramo_id
            and self.fibra_id
            and self.tramo.ruta_id != self.fibra.ruta_id
        ):
            raise ValidationError({
                'fibra': 'La fibra y el tramo deben pertenecer a la misma ruta.'
            })

    def save(self, *args, **kwargs):
        self.numero_hilo = (self.numero_hilo or '').strip().upper()
        self.observaciones = (self.observaciones or '').strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    class Meta:
        db_table = 'inv_fibras_tramos'
        verbose_name = 'Fibra por tramo'
        verbose_name_plural = 'Fibras por tramo'
        ordering = ['tramo', 'numero_hilo']
        constraints = [
            models.UniqueConstraint(
                F('tramo'),
                Lower('numero_hilo'),
                name='uq_fibra_tramo_numero_ci',
            ),
            models.UniqueConstraint(
                fields=['tramo', 'fibra'],
                condition=Q(fibra__isnull=False),
                name='uq_fibra_tramo_fibra',
            ),
            models.CheckConstraint(
                condition=~Q(numero_hilo=''),
                name='ck_fibra_tramo_numero',
            ),
            models.CheckConstraint(
                condition=Q(estado__in=(
                    'Libre',
                    'Ocupado',
                    'Reservado',
                    'Desconocido',
                )),
                name='ck_fibra_tramo_estado_valido',
            ),
            models.CheckConstraint(
                condition=Q(numero_hilo__regex=r'^F[1-9][0-9]*$'),
                name='ck_fibra_tramo_formato',
            ),
        ]
        indexes = [
            models.Index(
                fields=['tramo', 'estado'],
                name='ix_fibra_tramo_estado',
            ),
        ]


ESTADOS_PUERTO_ODF = ('Libre', 'Ocupado', 'Reservado')
ESTADOS_PUERTO_ODF_CHOICES = tuple((estado, estado) for estado in ESTADOS_PUERTO_ODF)


def normalizar_estado_puerto_odf(valor, default='Libre'):
    """Normaliza los estados admitidos sin cambiar el formato del Archivo E."""
    equivalencias = {
        'libre': 'Libre',
        'disponible': 'Libre',
        'ocupado': 'Ocupado',
        'ocupada': 'Ocupado',
        'en uso': 'Ocupado',
        'reservado': 'Reservado',
        'reservada': 'Reservado',
    }
    return equivalencias.get(str(valor or '').strip().casefold(), default)


def normalizar_estado_puerto_odf_con_destino(valor, destino, default='Libre'):
    """Reconoce reservas descritas en el destino aunque el estado legado diga ocupado."""
    destino_normalizado = str(destino or '').strip().casefold()
    if 'reservad' in destino_normalizado:
        return 'Reservado'
    return normalizar_estado_puerto_odf(valor, default=default)


class InventarioODF(models.Model):
    """
    Representa la información general de un ODF (Optical Distribution Frame) en un Hub o Site.
    Datos poblados desde el Archivo D.
    """
    hub_site = models.CharField(max_length=150, blank=True, null=True, verbose_name="Hub/Site")
    sala = models.CharField(max_length=100, blank=True, null=True, verbose_name="Sala")
    rack = models.CharField(max_length=100, blank=True, null=True, verbose_name="Rack")
    odf = models.CharField(max_length=150, db_index=True, verbose_name="Nombre ODF")
    capacidad_puertos = models.IntegerField(blank=True, null=True, default=0, verbose_name="Capacidad")
    puertos_ocupados = models.IntegerField(blank=True, null=True, default=0, verbose_name="Ocupados")
    puertos_libres = models.IntegerField(blank=True, null=True, default=0, verbose_name="Libres")
    puertos_reservados = models.IntegerField(blank=True, null=True, default=0, verbose_name="Reservados")
    tipo_conector = models.CharField(max_length=100, blank=True, null=True, verbose_name="Tipo Conector")
    estado = models.CharField(max_length=50, blank=True, null=True, verbose_name="Estado")
    observaciones = models.TextField(blank=True, null=True, verbose_name="Observaciones")
    rack_obj = models.ForeignKey(
        'RackFisico',
        on_delete=models.PROTECT,
        related_name='odfs',
        verbose_name='Rack relacionado',
    )
    lote_importacion = models.ForeignKey(
        'LoteImportacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='odfs',
    )
    creado_en = models.DateTimeField(auto_now_add=True, null=True)
    actualizado_en = models.DateTimeField(auto_now=True, null=True)

    def __str__(self):
        return self.odf

    def save(self, *args, **kwargs):
        nombre_anterior = None
        if self.pk:
            nombre_anterior = type(self).objects.filter(pk=self.pk).values_list('odf', flat=True).first()
        if self.rack_obj_id:
            self.rack = self.rack_obj.nombre
            self.sala = self.rack_obj.sala.nombre
            self.hub_site = self.rack_obj.sala.hub_site.nombre
        self.odf = (self.odf or '').strip()
        self.full_clean()
        super().save(*args, **kwargs)
        NodoRed.objects.filter(odf_obj=self).update(
            tipo='ODF',
            codigo=self.odf.upper(),
            nombre=self.odf,
        )
        if nombre_anterior and nombre_anterior != self.odf:
            self.puertos_detalle.update(odf=self.odf)

    def actualizar_contadores(self):
        resumen = self.puertos_detalle.aggregate(
            total=Count('id'),
            ocupados=Count('id', filter=Q(estado_puerto='Ocupado')),
            reservados=Count('id', filter=Q(estado_puerto='Reservado')),
        )
        ocupados = resumen['ocupados']
        reservados = resumen['reservados']
        capacidad = max(self.capacidad_puertos or 0, resumen['total'])
        type(self).objects.filter(pk=self.pk).update(
            capacidad_puertos=capacidad,
            puertos_ocupados=ocupados,
            puertos_libres=max(capacidad - ocupados - reservados, 0),
            puertos_reservados=reservados,
        )

    def clean(self):
        super().clean()
        if self.pk and self.capacidad_puertos:
            total_puertos = self.puertos_detalle.count()
            if total_puertos > self.capacidad_puertos:
                raise ValidationError({
                    'capacidad_puertos': (
                        f'La capacidad no puede ser menor que los {total_puertos} '
                        'puertos ya registrados.'
                    )
                })

    class Meta:
        db_table = 'inv_odfs'
        verbose_name = "Inventario ODF"
        verbose_name_plural = "Inventario ODFs"
        ordering = ['odf']
        constraints = [
            models.UniqueConstraint(
                Lower('odf'),
                name='uq_odf_nombre_global_ci',
            ),
            models.CheckConstraint(
                condition=Q(capacidad_puertos__isnull=True) | Q(capacidad_puertos__gte=0),
                name='ck_odf_capacidad_no_negativa',
            ),
            models.CheckConstraint(
                condition=Q(puertos_ocupados__isnull=True) | Q(puertos_ocupados__gte=0),
                name='ck_odf_ocupados_no_negativos',
            ),
            models.CheckConstraint(
                condition=Q(puertos_libres__isnull=True) | Q(puertos_libres__gte=0),
                name='ck_odf_libres_no_negativos',
            ),
            models.CheckConstraint(
                condition=Q(puertos_reservados__isnull=True) | Q(puertos_reservados__gte=0),
                name='ck_odf_reservados_no_negativos',
            ),
        ]


class DetallePuertoODF(models.Model):
    """
    Representa el estado y detalle de cada puerto individual dentro de un ODF.
    Datos poblados desde el Archivo E.
    """
    odf_obj = models.ForeignKey(InventarioODF, on_delete=models.CASCADE, related_name='puertos_detalle', verbose_name="ODF Asociado")
    odf = models.CharField(max_length=150, verbose_name="Nombre ODF (Texto)")
    bandeja = models.CharField(max_length=50, blank=True, null=True, verbose_name="Bandeja")
    puerto_odf = models.CharField(max_length=50, verbose_name="Puerto ODF")
    estado_puerto = models.CharField(max_length=50, choices=ESTADOS_PUERTO_ODF_CHOICES, default='Libre', verbose_name="Estado")
    tipo_conector = models.CharField(max_length=100, blank=True, null=True, verbose_name="Tipo Conector")
    patchcord = models.CharField(max_length=10, blank=True, null=True, verbose_name="Patchcord (Sí/No)")
    destino = models.CharField(max_length=150, blank=True, null=True, verbose_name="Destino")
    observaciones = models.TextField(blank=True, null=True, verbose_name="Observaciones")
    lote_importacion = models.ForeignKey(
        'LoteImportacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='puertos_odf',
    )

    def __str__(self):
        nombre_odf = self.odf_obj.odf if self.odf_obj_id else self.odf
        return f"{nombre_odf} - Puerto {self.puerto_odf} ({self.estado_puerto})"

    def clean(self):
        super().clean()
        if not self.odf_obj_id:
            raise ValidationError({'odf_obj': 'El puerto debe pertenecer a un ODF existente.'})
        if not self.pk and self.estado_puerto == 'Ocupado':
            raise ValidationError({
                'estado_puerto': (
                    'Un puerto nuevo debe crearse Libre o Reservado y luego '
                    'conectarse a una fibra.'
                ),
            })
        if self.pk:
            estado_anterior = type(self).objects.filter(pk=self.pk).values_list(
                'estado_puerto', flat=True
            ).first()
            if estado_anterior and estado_anterior != self.estado_puerto:
                raise ValidationError({
                    'estado_puerto': (
                        'Use la gestión de conexión para conectar, desconectar, '
                        'reservar o cancelar una reserva.'
                    ),
                })

    def save(self, *args, **kwargs):
        self.odf = self.odf_obj.odf
        self.puerto_odf = (self.puerto_odf or '').strip().upper()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        odf = self.odf_obj
        result = super().delete(*args, **kwargs)
        if odf:
            odf.actualizar_contadores()
        return result

    class Meta:
        db_table = 'inv_odfs_puertos'
        verbose_name = "Detalle de Puerto ODF"
        verbose_name_plural = "Detalle de Puertos ODF"
        ordering = ['odf', 'puerto_odf']
        constraints = [
            models.UniqueConstraint(
                F('odf_obj'), Lower('puerto_odf'),
                name='uq_puerto_odf_numero_ci',
            ),
        ]


class HubSite(models.Model):
    """
    Representa un Site o Hub principal.
    Permite gestionar de manera unificada los nombres de los sitios en la plataforma.
    """
    nombre = models.CharField(max_length=150, unique=True, db_index=True, verbose_name="Nombre del Hub/Site")
    latitud = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True, verbose_name="Latitud")
    longitud = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True, verbose_name="Longitud")
    direccion = models.CharField(max_length=255, null=True, blank=True, verbose_name="Dirección")
    lote_importacion = models.ForeignKey(
        'LoteImportacion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='hub_sites',
    )

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        self.nombre = (self.nombre or '').strip()
        resultado = super().save(*args, **kwargs)
        NodoRed.objects.filter(hub_site_obj=self).update(
            tipo='SITE',
            codigo=self.nombre.upper(),
            nombre=self.nombre,
        )
        return resultado

    class Meta:
        db_table = 'inv_hub_sites'
        verbose_name = "Hub Site"
        verbose_name_plural = "Hub Sites"
        ordering = ['nombre']
        constraints = [
            models.UniqueConstraint(
                Lower('nombre'),
                name='uq_hub_site_nombre_ci',
            ),
        ]


class LoteImportacion(models.Model):
    """Audita el origen, resultado y reversibilidad de cada carga masiva."""

    ORIGENES_REGISTRO = [
        ('GUI', 'Importacion ejecutada desde la GUI'),
        ('BASELINE_RECONSTRUIDO', 'Baseline reconstruido para homologacion'),
    ]

    ESTADOS = [
        ('PENDIENTE', 'Pendiente'),
        ('PROCESANDO', 'Procesando'),
        ('COMPLETADO', 'Completado'),
        ('COMPLETADO_CON_ERRORES', 'Completado con errores'),
        ('FALLIDO', 'Fallido'),
        ('REVERTIDO', 'Revertido'),
    ]

    codigo = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    tipo = models.CharField(max_length=80)
    archivo_origen = models.CharField(max_length=255)
    hash_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    estado = models.CharField(max_length=30, choices=ESTADOS, default='PENDIENTE', db_index=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lotes_importacion_fibergenius',
    )
    total_filas = models.PositiveIntegerField(default=0)
    filas_creadas = models.PositiveIntegerField(default=0)
    filas_actualizadas = models.PositiveIntegerField(default=0)
    filas_rechazadas = models.PositiveIntegerField(default=0)
    detalle_errores = models.JSONField(default=list, blank=True)
    origen_registro = models.CharField(
        max_length=30,
        choices=ORIGENES_REGISTRO,
        default='GUI',
        db_index=True,
    )
    fecha_origen = models.DateTimeField(null=True, blank=True)
    metadatos_origen = models.JSONField(default=dict, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    finalizado_en = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.tipo} - {self.codigo}"

    class Meta:
        db_table = 'sys_lotes_importacion'
        ordering = ['-creado_en']


class SalaTecnica(models.Model):
    hub_site = models.ForeignKey(HubSite, on_delete=models.PROTECT, related_name='salas')
    nombre = models.CharField(max_length=100)
    codigo = models.CharField(max_length=100, blank=True)
    estado = models.CharField(max_length=30, default='ACTIVO')
    lote_importacion = models.ForeignKey(
        LoteImportacion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='salas',
    )

    def __str__(self):
        return f"{self.hub_site.nombre} / {self.nombre}"

    class Meta:
        db_table = 'inv_salas_tecnicas'
        ordering = ['hub_site__nombre', 'nombre']
        constraints = [
            models.UniqueConstraint(fields=['hub_site', 'nombre'], name='uq_sala_hub_nombre'),
        ]


class RackFisico(models.Model):
    sala = models.ForeignKey(SalaTecnica, on_delete=models.PROTECT, related_name='racks')
    nombre = models.CharField(max_length=100)
    codigo = models.CharField(max_length=100, blank=True)
    unidades_rack = models.PositiveSmallIntegerField(null=True, blank=True)
    estado = models.CharField(max_length=30, default='ACTIVO')
    lote_importacion = models.ForeignKey(
        LoteImportacion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='racks',
    )

    def __str__(self):
        return f"{self.sala} / {self.nombre}"

    class Meta:
        db_table = 'inv_racks_fisicos'
        ordering = ['sala__hub_site__nombre', 'sala__nombre', 'nombre']
        constraints = [
            models.UniqueConstraint(fields=['sala', 'nombre'], name='uq_rack_sala_nombre'),
        ]


class SiteAlias(models.Model):
    """Conserva nombres KML/KMZ sin reemplazar el nombre oficial del Excel."""

    hub_site = models.ForeignKey(HubSite, on_delete=models.CASCADE, related_name='aliases')
    alias = models.CharField(max_length=150, db_index=True)
    fuente = models.CharField(max_length=150, default='KML/KMZ')
    latitud = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitud = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    distancia_coincidencia_m = models.FloatField(null=True, blank=True)
    validado = models.BooleanField(default=False, db_index=True)
    lote_importacion = models.ForeignKey(
        LoteImportacion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='aliases_site',
    )

    class Meta:
        db_table = 'inv_sites_alias'
        constraints = [
            models.UniqueConstraint(
                fields=['hub_site', 'alias', 'fuente'],
                name='uq_site_alias_fuente',
            ),
            models.CheckConstraint(
                condition=Q(distancia_coincidencia_m__isnull=True) | Q(distancia_coincidencia_m__gte=0),
                name='ck_alias_distancia_no_negativa',
            ),
        ]


class TerminacionFibra(models.Model):
    EXTREMOS = [('A', 'Extremo A'), ('B', 'Extremo B')]

    fibra = models.ForeignKey(InventarioFibra, on_delete=models.CASCADE, related_name='terminaciones')
    extremo = models.CharField(max_length=1, choices=EXTREMOS)
    puerto_odf = models.ForeignKey(DetallePuertoODF, on_delete=models.PROTECT, related_name='terminaciones_fibra')
    tipo_conector = models.CharField(max_length=100, blank=True)
    lote_importacion = models.ForeignKey(
        LoteImportacion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='terminaciones_fibra',
    )

    def clean(self):
        super().clean()
        # El ODF pertenece a esta terminación individual. Una ruta puede
        # distribuir sus fibras entre varios ODF y no impone un ODF único.

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    class Meta:
        db_table = 'inv_fibras_terminaciones'
        constraints = [
            models.UniqueConstraint(fields=['fibra', 'extremo'], name='uq_terminacion_fibra_extremo'),
            models.UniqueConstraint(fields=['puerto_odf'], name='uq_terminacion_puerto_odf'),
            models.CheckConstraint(
                condition=Q(extremo__in=('A', 'B')),
                name='ck_terminacion_extremo_valido',
            ),
        ]


class AuditoriaPuertoODF(models.Model):
    """Evento inmutable de una operación sobre un puerto ODF."""

    ACCIONES = [
        ('CONECTAR', 'Conectar'),
        ('MOVER', 'Mover terminación'),
        ('DESCONECTAR', 'Desconectar'),
        ('RESERVAR', 'Reservar'),
        ('CANCELAR_RESERVA', 'Cancelar reserva'),
    ]
    ORIGENES = [
        ('GUI', 'Interfaz web'),
        ('EXCEL', 'Actualización masiva por Excel'),
        ('SISTEMA', 'Proceso interno'),
    ]

    accion = models.CharField(max_length=30, choices=ACCIONES, db_index=True)
    origen = models.CharField(max_length=15, choices=ORIGENES, default='SISTEMA', db_index=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='auditorias_puertos_odf',
    )
    fibra = models.ForeignKey(
        InventarioFibra,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='auditorias_puertos_odf',
    )
    extremo = models.CharField(max_length=1, blank=True)
    puerto_anterior = models.ForeignKey(
        DetallePuertoODF,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='auditorias_como_puerto_anterior',
    )
    puerto_nuevo = models.ForeignKey(
        DetallePuertoODF,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='auditorias_como_puerto_nuevo',
    )
    estado_anterior = models.CharField(max_length=50, blank=True)
    estado_nuevo = models.CharField(max_length=50, blank=True)
    referencia_puerto_anterior = models.CharField(max_length=500, blank=True)
    referencia_puerto_nuevo = models.CharField(max_length=500, blank=True)
    referencia_fibra = models.CharField(max_length=300, blank=True)
    sincronizo_fibra = models.BooleanField(default=False)
    lote_importacion = models.ForeignKey(
        LoteImportacion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='auditorias_puertos_odf',
    )
    metadatos = models.JSONField(default=dict, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True, db_index=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Los eventos de auditoría no pueden modificarse.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Los eventos de auditoría no pueden eliminarse.')

    def __str__(self):
        return f'{self.get_accion_display()} - {self.referencia_puerto_nuevo or self.referencia_puerto_anterior}'

    class Meta:
        db_table = 'aud_puertos_odf'
        ordering = ['-creado_en', '-pk']
        indexes = [
            models.Index(fields=['puerto_anterior', '-creado_en'], name='idx_aud_puerto_ant_fecha'),
            models.Index(fields=['puerto_nuevo', '-creado_en'], name='idx_aud_puerto_nvo_fecha'),
            models.Index(fields=['fibra', '-creado_en'], name='idx_aud_fibra_fecha'),
        ]


class AuditoriaFibra(models.Model):
    """Evento inmutable para cambios del estado global o la troncal."""

    ACCIONES = [
        ('CAMBIAR_ESTADO', 'Cambiar estado'),
        ('INFERIR_ESTADO', 'Inferir estado desde tramos'),
        ('RESTABLECER_ESTADO', 'Restablecer estado'),
        ('ASIGNAR_RUTA', 'Asignar troncal'),
    ]
    ORIGENES = [
        ('GUI', 'Interfaz web'),
        ('EXCEL', 'Importación Excel/CSV'),
        ('API', 'API'),
        ('ADMIN', 'Administrador'),
        ('SISTEMA', 'Proceso interno'),
    ]

    accion = models.CharField(max_length=30, choices=ACCIONES, db_index=True)
    origen = models.CharField(
        max_length=15,
        choices=ORIGENES,
        default='SISTEMA',
        db_index=True,
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='auditorias_fibras',
    )
    fibra = models.ForeignKey(
        InventarioFibra,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='auditorias',
    )
    referencia_fibra = models.CharField(max_length=300, blank=True)
    valor_anterior = models.CharField(max_length=300, blank=True)
    valor_nuevo = models.CharField(max_length=300, blank=True)
    lote_importacion = models.ForeignKey(
        LoteImportacion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='auditorias_fibras',
    )
    metadatos = models.JSONField(default=dict, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True, db_index=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Los eventos de auditoría no pueden modificarse.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Los eventos de auditoría no pueden eliminarse.')

    def __str__(self):
        return f'{self.get_accion_display()} - {self.referencia_fibra}'

    class Meta:
        db_table = 'aud_fibras'
        ordering = ['-creado_en', '-pk']
        indexes = [
            models.Index(fields=['fibra', '-creado_en'], name='idx_aud_fibra_evento'),
            models.Index(fields=['accion', '-creado_en'], name='idx_aud_fibra_accion'),
        ]


class EmpalmeFibra(models.Model):
    elemento = models.ForeignKey(Reserva, on_delete=models.PROTECT, related_name='empalmes')
    fibra_entrada = models.ForeignKey(InventarioFibra, on_delete=models.PROTECT, related_name='empalmes_entrada')
    fibra_salida = models.ForeignKey(InventarioFibra, on_delete=models.PROTECT, related_name='empalmes_salida')
    bandeja = models.CharField(max_length=50, blank=True)
    posicion = models.CharField(max_length=50, blank=True)
    perdida_db = models.FloatField(null=True, blank=True)
    estado = models.CharField(max_length=30, default='POR_CONFIRMAR')
    lote_importacion = models.ForeignKey(
        LoteImportacion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='empalmes_fibra',
    )

    def clean(self):
        super().clean()
        if self.fibra_entrada_id and self.fibra_salida_id and self.fibra_entrada_id >= self.fibra_salida_id:
            raise ValidationError('Ordene las fibras por ID y no empalme una fibra consigo misma.')
        if (
            self.fibra_entrada_id
            and self.fibra_salida_id
            and self.fibra_entrada.ruta_id != self.fibra_salida.ruta_id
        ):
            raise ValidationError('Las dos fibras del empalme deben pertenecer a la misma troncal.')
        if (
            self.elemento_id
            and self.fibra_entrada_id
            and self.elemento.ruta_id != self.fibra_entrada.ruta_id
        ):
            raise ValidationError({'elemento': 'El elemento y las fibras deben pertenecer a la misma troncal.'})
        tipo_elemento = (self.elemento.tipo or '').strip().casefold() if self.elemento_id else ''
        if self.elemento_id and tipo_elemento not in {
            'mufa', 'camara', 'cámara', 'caja_empalme', 'caja empalme', 'otro'
        }:
            raise ValidationError({'elemento': 'El elemento seleccionado no admite empalmes.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    class Meta:
        db_table = 'inv_fibras_empalmes'
        constraints = [
            models.UniqueConstraint(
                fields=['elemento', 'fibra_entrada', 'fibra_salida'],
                name='uq_empalme_elemento_fibras',
            ),
            models.UniqueConstraint(
                fields=['elemento', 'bandeja', 'posicion'],
                condition=~Q(bandeja='') & ~Q(posicion=''),
                name='uq_empalme_elemento_posicion',
            ),
            models.CheckConstraint(
                condition=Q(fibra_entrada__lt=F('fibra_salida')),
                name='ck_empalme_fibras_distintas',
            ),
            models.CheckConstraint(
                condition=Q(perdida_db__isnull=True) | Q(perdida_db__gte=0),
                name='ck_empalme_perdida_no_negativa',
            ),
        ]


# =============================================================================
# MODELOS UNMANAGED — Tablas SQL existentes (no gestionadas por migraciones)
# =============================================================================



class EventoOTDR(models.Model):
    """
    Mapea la tabla 'otdr_eventos_geo' con eventos OTDR georreferenciados.
    Poblada por el management command 'georeferenciar_eventos'.
    """
    id = models.BigAutoField(primary_key=True)
    node = models.CharField(max_length=255, db_column='Node')
    event_id = models.CharField(max_length=255)
    event_type = models.CharField(max_length=255, blank=True, null=True)
    optical_distance_m = models.FloatField(blank=True, null=True)
    geo_distance_m_from_calibration = models.FloatField(blank=True, null=True)
    segment_index = models.IntegerField(blank=True, null=True)
    segment_fraction = models.FloatField(blank=True, null=True)
    latitude = models.FloatField(blank=True, null=True)
    longitude = models.FloatField(blank=True, null=True)
    loss_db = models.FloatField(blank=True, null=True)
    reflectance_db = models.FloatField(blank=True, null=True)
    event_test_status = models.CharField(max_length=255, blank=True, null=True)
    section_loss_db = models.DecimalField(max_digits=8, decimal_places=3, blank=True, null=True)
    cumulative_loss_db = models.DecimalField(max_digits=8, decimal_places=3, blank=True, null=True)
    cumulative_loss_db_calculated = models.DecimalField(max_digits=8, decimal_places=3, blank=True, null=True)
    real_cumulative_loss_db = models.DecimalField(max_digits=8, decimal_places=3, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.node} - {self.event_type} ({self.event_id})"

    class Meta:
        managed = False
        db_table = 'mon_otdr_eventos_geo'
        verbose_name = "Evento OTDR Georreferenciado"
        verbose_name_plural = "Eventos OTDR Georreferenciados"


class Medicion(models.Model):
    """
    Mapea la tabla 'mediciones' con datos de mediciones OTDR por enlace.
    Poblada por el management command 'actualizar_mediciones'.
    """
    id = models.AutoField(primary_key=True)
    node = models.CharField(max_length=100, db_column='Node')
    internal_key = models.BigIntegerField()
    measurement_uid = models.CharField(max_length=100)
    link_internal_key = models.BigIntegerField()
    otu_internal_key = models.BigIntegerField(blank=True, null=True)
    acquisition_date = models.DateTimeField(blank=True, null=True)
    result = models.CharField(max_length=20, blank=True, null=True)
    fiber_length = models.FloatField(blank=True, null=True)
    wavelength = models.IntegerField(blank=True, null=True)
    link_loss_alarm = models.BooleanField(blank=True, null=True)
    link_loss_value = models.FloatField(blank=True, null=True)
    linear_att_alarm = models.BooleanField(blank=True, null=True)
    linear_att_value = models.FloatField(blank=True, null=True)
    orl_alarm = models.BooleanField(blank=True, null=True)
    orl_value = models.FloatField(blank=True, null=True)
    events_alarm = models.BooleanField(blank=True, null=True)
    splitter_detected = models.BooleanField(blank=True, null=True)
    fiber_end_detected = models.BooleanField(blank=True, null=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Medición {self.node} - {self.measurement_uid}"

    class Meta:
        managed = True
        db_table = 'mon_otdr_mediciones'
        verbose_name = "Medición OTDR"
        verbose_name_plural = "Mediciones OTDR"
        indexes = [
            models.Index(fields=['link_internal_key'], name='ix_medicion_link_key'),
            models.Index(fields=['node', '-acquisition_date'], name='ix_medicion_node_fecha'),
        ]


class PruebaOTDR(models.Model):
    """
    Mapea la tabla 'otdr_pruebas' con datos de pruebas OTDR.
    Poblada por el management command 'actualizar_eventos'.
    """
    id = models.AutoField(primary_key=True)
    fecha_adquisicion = models.DateTimeField(blank=True, null=True)
    longitud_fibra_m = models.FloatField(blank=True, null=True)
    perdida_total_db = models.FloatField(blank=True, null=True)
    orl_db = models.FloatField(blank=True, null=True)
    num_eventos = models.IntegerField(blank=True, null=True)
    longitud_onda_nm = models.IntegerField(blank=True, null=True)
    max_empalme_db = models.FloatField(blank=True, null=True)
    max_reflectancia_db = models.FloatField(blank=True, null=True)
    estado_prueba = models.CharField(max_length=10, blank=True, null=True)
    cable_id = models.CharField(max_length=100, blank=True, null=True)
    fibra_id = models.CharField(max_length=100, blank=True, null=True)
    direccion = models.CharField(max_length=100, blank=True, null=True)
    localizacion_a = models.CharField(max_length=100, blank=True, null=True)
    localizacion_b = models.CharField(max_length=100, blank=True, null=True)
    internal_key = models.CharField(max_length=100, blank=True, null=True)
    fecha_procesamiento = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Prueba {self.cable_id} - {self.fecha_adquisicion}"

    class Meta:
        managed = True
        db_table = 'mon_otdr_pruebas'
        verbose_name = "Prueba OTDR"
        verbose_name_plural = "Pruebas OTDR"
        indexes = [
            models.Index(fields=['internal_key'], name='ix_prueba_internal_key'),
            models.Index(fields=['fecha_adquisicion'], name='ix_prueba_fecha'),
        ]


class EventoOTDRDetalle(models.Model):
    """
    Mapea la tabla 'otdr_eventos' con detalles de eventos por prueba.
    Poblada por el management command 'actualizar_eventos'.
    """
    id = models.AutoField(primary_key=True)
    prueba = models.ForeignKey(PruebaOTDR, on_delete=models.PROTECT, db_column='prueba_id', blank=True, null=True)
    event_id = models.IntegerField(blank=True, null=True)
    id_slm = models.IntegerField(blank=True, null=True)
    event_type = models.CharField(max_length=50, blank=True, null=True)
    distance_m = models.FloatField(blank=True, null=True)
    section_length_m = models.FloatField(blank=True, null=True)
    loss_db = models.FloatField(blank=True, null=True)
    section_loss_db = models.FloatField(blank=True, null=True)
    cumulative_loss_db = models.FloatField(blank=True, null=True)
    reflectance_db = models.FloatField(blank=True, null=True)
    slope_dbkm = models.FloatField(blank=True, null=True)
    event_test_status = models.CharField(max_length=10, blank=True, null=True)
    loss_alarm_failed = models.BooleanField(blank=True, null=True)
    refl_alarm_failed = models.BooleanField(blank=True, null=True)
    group_number = models.IntegerField(blank=True, null=True)
    refl_saturated = models.BooleanField(blank=True, null=True)
    refl_peak_distance_m = models.FloatField(blank=True, null=True)
    node = models.CharField(max_length=255, db_column='Node', blank=True, null=True)

    def __str__(self):
        return f"Evento {self.event_id} - {self.event_type}"

    class Meta:
        managed = True
        db_table = 'mon_otdr_eventos'
        verbose_name = "Evento OTDR Detalle"
        verbose_name_plural = "Eventos OTDR Detalle"
        indexes = [
            models.Index(fields=['node'], name='ix_evento_det_node'),
            models.Index(fields=['prueba', 'event_id'], name='ix_evento_det_prueba'),
        ]

# =============================================================================
# MODELOS USUARIO & DASHBOARD (Managed)
# =============================================================================

from django.contrib.auth.models import User
from django.utils.timezone import now

class AlarmaVeex(models.Model):
    """
    Guarda las alarmas recibidas en tiempo real desde los equipos VeEX a través de SNMP Traps.
    """
    status = models.CharField(max_length=50, verbose_name="Estado de Alarma")
    alarm_type = models.CharField(max_length=100, verbose_name="Tipo de Alarma")
    alarm_level = models.CharField(max_length=50, verbose_name="Severidad")
    timestamp = models.CharField(max_length=50, verbose_name="Timestamp del Equipo")
    device_serial = models.CharField(max_length=100, verbose_name="Serial del OTU")
    device_ip = models.GenericIPAddressField(verbose_name="IP del Equipo")
    port = models.CharField(max_length=50, verbose_name="Puerto OTU")
    distance = models.FloatField(verbose_name="Distancia Métrica (m)", null=True, blank=True)
    route_name = models.CharField(max_length=150, verbose_name="Nombre de Ruta")
    latitude = models.DecimalField(max_digits=14, decimal_places=10, verbose_name="Latitud de Falla")
    longitude = models.DecimalField(max_digits=14, decimal_places=10, verbose_name="Longitud de Falla")
    test_message = models.TextField(blank=True, null=True, verbose_name="Mensaje de Prueba")
    fecha_registro = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Registro")
    fecha_resolucion = models.CharField(max_length=50, blank=True, null=True, verbose_name="Timestamp de Resolución")
    veex_alarm_id = models.IntegerField(null=True, blank=True, verbose_name="ID API VeEX")
    archivo_sor = models.FileField(upload_to='trazas_padre/', null=True, blank=True, verbose_name="Archivo .sor Local")
    ruta_obj = models.ForeignKey(
        Ruta,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='alarmas_veex',
        verbose_name='Ruta relacionada',
    )
    timestamp_evento = models.DateTimeField(null=True, blank=True, db_index=True)

    def __str__(self):
        return f"{self.route_name} - {self.alarm_type} ({self.alarm_level}) - {self.status}"

    class Meta:
        db_table = 'mon_alarmas_veex'
        verbose_name = "Alarma VeEX"
        verbose_name_plural = "Alarmas VeEX"
        ordering = ['-fecha_registro']
        indexes = [
            models.Index(fields=['status', '-fecha_registro'], name='ix_alarma_status_fecha'),
            models.Index(fields=['alarm_level', '-fecha_registro'], name='ix_alarma_nivel_fecha'),
            models.Index(fields=['alarm_type', '-fecha_registro'], name='ix_alarma_tipo_fecha'),
            models.Index(fields=['device_serial', 'port'], name='ix_alarma_equipo_puerto'),
            models.Index(fields=['route_name'], name='ix_alarma_ruta_texto'),
            models.Index(
                fields=['route_name', 'port', 'status'],
                name='ix_alarma_ruta_puerto_estado',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['veex_alarm_id'],
                condition=Q(veex_alarm_id__isnull=False),
                name='uq_alarma_veex_id',
            ),
            models.CheckConstraint(
                condition=Q(distance__isnull=True) | Q(distance__gte=0),
                name='ck_alarma_distancia_no_negativa',
            ),
        ]


class EventLog(models.Model):
    """
    Guarda de forma inmutable cada trap individual recibido para auditoría.
    """
    incident = models.ForeignKey(AlarmaVeex, on_delete=models.PROTECT, related_name='event_logs', verbose_name="Incidente Padre")
    status = models.CharField(max_length=50, verbose_name="Estado de Alarma")
    alarm_type = models.CharField(max_length=100, verbose_name="Tipo de Alarma")
    alarm_level = models.CharField(max_length=50, verbose_name="Severidad")
    timestamp = models.CharField(max_length=50, verbose_name="Timestamp del Equipo")
    device_serial = models.CharField(max_length=100, verbose_name="Serial del OTU")
    device_ip = models.GenericIPAddressField(verbose_name="IP del Equipo")
    port = models.CharField(max_length=50, verbose_name="Puerto OTU")
    distance = models.FloatField(verbose_name="Distancia Métrica (m)", null=True, blank=True)
    route_name = models.CharField(max_length=150, verbose_name="Nombre de Ruta")
    latitude = models.DecimalField(max_digits=14, decimal_places=10, verbose_name="Latitud de Falla")
    longitude = models.DecimalField(max_digits=14, decimal_places=10, verbose_name="Longitud de Falla")
    test_message = models.TextField(blank=True, null=True, verbose_name="Mensaje de Prueba")
    fecha_registro = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Registro")
    fecha_resolucion = models.CharField(max_length=50, blank=True, null=True, verbose_name="Timestamp de Resolución")
    veex_alarm_id = models.IntegerField(null=True, blank=True, verbose_name="ID API VeEX")
    archivo_sor = models.FileField(upload_to='trazas_logs/', null=True, blank=True, verbose_name="Archivo .sor Local")
    ruta_obj = models.ForeignKey(
        Ruta,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logs_alarmas',
        verbose_name='Ruta relacionada',
    )
    timestamp_evento = models.DateTimeField(null=True, blank=True, db_index=True)

    def __str__(self):
        return f"Log {self.id} (Incidente {self.incident_id}) - {self.status}"

    class Meta:
        db_table = 'mon_alarmas_logs'
        verbose_name = "Log de Evento"
        verbose_name_plural = "Logs de Eventos"
        ordering = ['-fecha_registro']
        indexes = [
            models.Index(fields=['status', '-fecha_registro'], name='ix_log_status_fecha'),
            models.Index(fields=['route_name', '-fecha_registro'], name='ix_log_ruta_fecha'),
        ]


class UserActivity(models.Model):
    """
    Rastrea la última actividad de un usuario para determinar si está online.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='activity')
    last_activity = models.DateTimeField(default=now)

    def __str__(self):
        return f"Actividad de {self.user.username}"

    class Meta:
        db_table = 'sys_user_activity'
        verbose_name = "Actividad de Usuario"
        verbose_name_plural = "Actividades de Usuario"


class LoginThrottle(models.Model):
    """Contador persistente y opaco para limitar intentos de autenticación."""

    key_hash = models.CharField(max_length=64, unique=True)
    attempts = models.PositiveIntegerField(default=0)
    window_started = models.DateTimeField()
    locked_until = models.DateTimeField(null=True, blank=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'sys_login_throttle'
        verbose_name = "Bloqueo temporal de acceso"
        verbose_name_plural = "Bloqueos temporales de acceso"


class TrazaOnDemand(models.Model):
    """
    Modelo para guardar información y resultados de trazas bajo demanda generadas en tiempo real.
    """
    route_name = models.CharField(max_length=150, verbose_name="Nombre de Ruta")
    fecha_solicitud = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Solicitud")
    status = models.CharField(max_length=50, default='Pending', verbose_name="Estado de Solicitud") # Pending, Running, Completed, Failed
    veex_on_demand_id = models.CharField(max_length=100, null=True, blank=True, verbose_name="ID de Tarea VeEX")
    archivo_sor = models.FileField(upload_to='trazas_ondemand/', null=True, blank=True, verbose_name="Archivo .sor Descargado")
    distance_km = models.FloatField(null=True, blank=True, verbose_name="Distancia Reportada (Km)")
    error_message = models.TextField(null=True, blank=True, verbose_name="Mensaje de Error")
    ruta_obj = models.ForeignKey(
        Ruta,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='trazas_on_demand',
        verbose_name='Ruta relacionada',
    )

    def __str__(self):
        return f"Traza {self.id} - {self.route_name} ({self.status})"

    class Meta:
        db_table = 'mon_trazas_ondemand'
        verbose_name = "Traza Bajo Demanda"
        verbose_name_plural = "Trazas Bajo Demanda"
        ordering = ['-fecha_solicitud']


class TrazaReferencia(models.Model):
    """
    Guarda la traza base o ideal de una ruta (Baseline) para comparaciones futuras por el motor matemático.
    """
    ruta = models.ForeignKey('Ruta', on_delete=models.CASCADE, related_name='trazas_referencia', verbose_name="Ruta Asociada")
    nombre = models.CharField(max_length=150, verbose_name="Nombre de Referencia")
    archivo_sor = models.FileField(upload_to='trazas_referencia/', verbose_name="Archivo .sor de Referencia")
    distancia_km = models.FloatField(null=True, blank=True, verbose_name="Distancia Total (Km)")
    fecha_creacion = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Creación")
    activa = models.BooleanField(default=True, verbose_name="Referencia Principal")

    def __str__(self):
        return f"{self.nombre} - {self.ruta.nombre}"

    class Meta:
        db_table = 'mon_trazas_referencia'
        verbose_name = "Traza de Referencia"
        verbose_name_plural = "Trazas de Referencia"
        constraints = [
            models.UniqueConstraint(
                fields=['ruta'],
                condition=Q(activa=True),
                name='uq_traza_referencia_activa_ruta',
            ),
        ]


class DiagnosticoProactivo(models.Model):
    """
    Guarda las alarmas generadas exclusivamente por nuestro motor matemático en Python (API), separadas de VeSion.
    """
    ruta = models.ForeignKey('Ruta', on_delete=models.CASCADE, related_name='diagnosticos_proactivos', verbose_name="Ruta Asociada")
    tipo_falla = models.CharField(max_length=100, verbose_name="Tipo de Falla (Atenuación / Corte)")
    severidad = models.CharField(max_length=50, verbose_name="Severidad") # Minor, Major, Critical
    distancia_falla_m = models.FloatField(verbose_name="Distancia de la Falla (metros)")
    desviacion_db = models.FloatField(null=True, blank=True, verbose_name="Desviación (dB)")
    
    archivo_sor_actual = models.FileField(upload_to='trazas_proactivas/', verbose_name="Archivo .sor Analizado")
    traza_referencia = models.ForeignKey(TrazaReferencia, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Referencia Utilizada")
    
    fecha_diagnostico = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Diagnóstico")
    
    estado = models.CharField(max_length=50, default='Abierta', verbose_name="Estado de Alarma") # Abierta, Cerrada

    def __str__(self):
        return f"{self.tipo_falla} ({self.severidad}) en {self.ruta.nombre} a {self.distancia_falla_m}m"

    class Meta:
        db_table = 'mon_diagnostico_proactivo'
        verbose_name = "Diagnóstico Proactivo"
        verbose_name_plural = "Diagnósticos Proactivos"
        ordering = ['-fecha_diagnostico']
