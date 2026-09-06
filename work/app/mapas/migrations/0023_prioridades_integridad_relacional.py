# Generada para Fiber Genius RC2 DB pruebas v0.3.

import django.db.models.deletion
import django.db.models.functions.text
from django.db import migrations, models


def preparar_datos_existentes(apps, schema_editor):
    """Resuelve FKs legacy y aborta antes de crear restricciones si hay colisiones."""

    Ruta = apps.get_model('mapas', 'Ruta')
    IDRuta = apps.get_model('mapas', 'IDRuta')
    ODF = apps.get_model('mapas', 'InventarioODF')
    Puerto = apps.get_model('mapas', 'DetallePuertoODF')
    Fibra = apps.get_model('mapas', 'InventarioFibra')
    Reserva = apps.get_model('mapas', 'Reserva')

    rutas = {
        (nombre or '').strip().casefold(): pk
        for pk, nombre in Ruta.objects.values_list('pk', 'nombre')
    }
    ids_sin_ruta = []
    for identificador in IDRuta.objects.filter(ruta_obj__isnull=True):
        ruta_id = rutas.get((identificador.ruta or '').strip().casefold())
        if not ruta_id:
            ids_sin_ruta.append(f'{identificador.pk}:{identificador.ruta}')
        else:
            identificador.ruta_obj_id = ruta_id
            identificador.save(update_fields=['ruta_obj'])
    if ids_sin_ruta:
        raise RuntimeError(
            'No se puede hacer obligatoria IDRuta.ruta_obj. Referencias sin resolver: '
            + ', '.join(ids_sin_ruta[:20])
        )

    def comprobar(modelo, campos, etiqueta):
        vistos = {}
        duplicados = []
        for fila in modelo.objects.values('pk', *campos):
            clave = tuple(
                (fila[campo] or '').strip().casefold()
                if isinstance(fila[campo], str) else fila[campo]
                for campo in campos
            )
            if clave in vistos:
                duplicados.append((vistos[clave], fila['pk'], clave))
            else:
                vistos[clave] = fila['pk']
        if duplicados:
            raise RuntimeError(
                f'{etiqueta}: hay identificadores equivalentes que deben conciliarse '
                f'antes de migrar: {duplicados[:10]}'
            )

    comprobar(ODF, ['odf'], 'ODF')
    comprobar(Puerto, ['odf_obj_id', 'puerto_odf'], 'Puertos ODF')
    comprobar(Fibra, ['ruta_id', 'fibra_numero'], 'Fibras')
    comprobar(
        Reserva,
        ['ruta_id', 'nombre', 'latitud', 'longitud'],
        'Reservas/Landmarks',
    )

    for odf in ODF.objects.all().only('pk', 'odf'):
        odf.odf = (odf.odf or '').strip()
        odf.save(update_fields=['odf'])
    for puerto in Puerto.objects.all().only('pk', 'puerto_odf'):
        puerto.puerto_odf = (puerto.puerto_odf or '').strip().upper()
        puerto.save(update_fields=['puerto_odf'])
    for fibra in Fibra.objects.all().only('pk', 'fibra_numero'):
        fibra.fibra_numero = (fibra.fibra_numero or '').strip().upper()
        fibra.save(update_fields=['fibra_numero'])


class Migration(migrations.Migration):

    dependencies = [
        ('mapas', '0022_eliminar_inventario_fisico_duplicado'),
    ]

    operations = [
        migrations.RunPython(preparar_datos_existentes, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='detallepuertoodf',
            name='uq_puerto_odf_numero',
        ),
        migrations.RemoveConstraint(
            model_name='inventarioodf',
            name='uq_odf_rack_nombre',
        ),
        migrations.AlterUniqueTogether(
            name='inventariofibra',
            unique_together=set(),
        ),
        migrations.AddField(
            model_name='coordenadaruta',
            name='lote_importacion',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='coordenadas_ruta', to='mapas.loteimportacion'),
        ),
        migrations.AddField(
            model_name='detallepuertoodf',
            name='lote_importacion',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='puertos_odf', to='mapas.loteimportacion'),
        ),
        migrations.AddField(
            model_name='hubsite',
            name='lote_importacion',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hub_sites', to='mapas.loteimportacion'),
        ),
        migrations.AddField(
            model_name='idruta',
            name='lote_importacion',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='identificadores_ruta', to='mapas.loteimportacion'),
        ),
        migrations.AddField(
            model_name='otu',
            name='lote_importacion',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='equipos_otu', to='mapas.loteimportacion'),
        ),
        migrations.AddField(
            model_name='puertootu',
            name='lote_importacion',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='puertos_otu', to='mapas.loteimportacion'),
        ),
        migrations.AddField(
            model_name='reserva',
            name='atributos',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='reserva',
            name='codigo',
            field=models.CharField(blank=True, max_length=150, null=True),
        ),
        migrations.AddField(
            model_name='reserva',
            name='estado',
            field=models.CharField(default='POR_CONFIRMAR', max_length=30),
        ),
        migrations.AddField(
            model_name='reserva',
            name='orden_en_ruta',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='reserva',
            name='progresiva_m',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='reserva',
            name='tipo_original_cliente',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='ruta',
            name='lote_importacion',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='rutas', to='mapas.loteimportacion'),
        ),
        migrations.AddField(
            model_name='ruta',
            name='odf_destino',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='rutas_como_destino', to='mapas.inventarioodf', verbose_name='ODF extremo B'),
        ),
        migrations.AddField(
            model_name='ruta',
            name='odf_origen',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='rutas_como_origen', to='mapas.inventarioodf', verbose_name='ODF extremo A'),
        ),
        migrations.AddField(
            model_name='empalmefibra',
            name='reserva_destino',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='mapas.reserva'),
        ),
        migrations.AlterField(
            model_name='coordenadaruta',
            name='tramo',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='coordenadas', to='mapas.inventariotramo', verbose_name='Tramo relacionado'),
        ),
        migrations.AlterField(
            model_name='eventootdrdetalle',
            name='prueba',
            field=models.ForeignKey(blank=True, db_column='prueba_id', null=True, on_delete=django.db.models.deletion.PROTECT, to='mapas.pruebaotdr'),
        ),
        migrations.AlterField(
            model_name='idruta',
            name='ruta_obj',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='identificadores_onmsi', to='mapas.ruta', verbose_name='Ruta relacionada'),
        ),
        migrations.AlterField(
            model_name='reserva',
            name='nombre',
            field=models.CharField(max_length=180, verbose_name='Nombre Landmark'),
        ),
        migrations.AddConstraint(
            model_name='detallepuertoodf',
            constraint=models.UniqueConstraint(models.F('odf_obj'), django.db.models.functions.text.Lower('puerto_odf'), name='uq_puerto_odf_numero_ci'),
        ),
        migrations.AddConstraint(
            model_name='inventariofibra',
            constraint=models.UniqueConstraint(models.F('ruta'), django.db.models.functions.text.Lower('fibra_numero'), name='uq_fibra_ruta_numero_ci'),
        ),
        migrations.AddConstraint(
            model_name='inventarioodf',
            constraint=models.UniqueConstraint(django.db.models.functions.text.Lower('odf'), name='uq_odf_nombre_global_ci'),
        ),
        migrations.AddConstraint(
            model_name='reserva',
            constraint=models.UniqueConstraint(fields=('ruta', 'nombre', 'latitud', 'longitud'), name='uq_reserva_ruta_nombre_coordenada'),
        ),
        migrations.AddConstraint(
            model_name='reserva',
            constraint=models.UniqueConstraint(condition=models.Q(codigo__isnull=False) & ~models.Q(codigo=''), fields=('codigo',), name='uq_reserva_codigo'),
        ),
        migrations.AddConstraint(
            model_name='reserva',
            constraint=models.CheckConstraint(condition=models.Q(progresiva_m__isnull=True) | models.Q(progresiva_m__gte=0), name='ck_reserva_progresiva'),
        ),
    ]
