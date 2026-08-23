import hashlib
import hmac
import gzip
import io
import json
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlencode

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db import connection
from django.db.models.deletion import ProtectedError
from django.template.loader import render_to_string
from django.test import (
    RequestFactory,
    TestCase,
    TransactionTestCase,
    override_settings,
    tag,
)
from django.test.utils import CaptureQueriesContext
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from .models import (
    AlarmaVeex,
    CoordenadaRuta,
    DetallePuertoODF,
    EmpalmeFibra,
    EventLog,
    HubSite,
    IDRuta,
    InventarioFibra,
    InventarioODF,
    InventarioTramo,
    LoginThrottle,
    LoteImportacion,
    Medicion,
    NodoRed,
    OTU,
    PruebaOTDR,
    PuertoOTU,
    RackFisico,
    Ruta,
    Reserva,
    SalaTecnica,
    TerminacionFibra,
    TrazaReferencia,
    EventoOTDRDetalle,
)
from .management.commands.actualizar_eventos import guardar_eventos
from .management.commands.actualizar_mediciones import (
    guardar_en_db_sobrescribiendo_por_enlace,
    parsear_xml,
)
from .forms import CustomUserCreationForm
from .views.importacion import (
    _procesar_coordenadas_csv,
    _procesar_coordenadas_inventario_zip,
    _procesar_coordenadas_zip,
    _procesar_puertos_odf_inventario,
    _procesar_reservas,
    _procesar_ruta_otu,
    _procesar_tramos_inventario,
)
from .middleware import ActiveUserMiddleware


def crear_jerarquia_odf(nombre='ODF-01', capacidad=24):
    hub = HubSite.objects.create(nombre=f'HUB-{nombre}')
    sala = SalaTecnica.objects.create(hub_site=hub, nombre='SALA-01')
    rack = RackFisico.objects.create(sala=sala, nombre='RACK-01')
    odf = InventarioODF.objects.create(
        rack_obj=rack,
        odf=nombre,
        capacidad_puertos=capacidad,
        puertos_libres=capacidad,
    )
    return hub, sala, rack, odf


class IntegridadODFTests(TransactionTestCase):
    reset_sequences = True

    def test_odf_duplicado_en_mismo_rack_es_rechazado(self):
        _, _, rack, odf = crear_jerarquia_odf()
        with self.assertRaises(IntegrityError), transaction.atomic():
            InventarioODF.objects.bulk_create([InventarioODF(
                rack_obj=rack,
                odf=odf.odf,
            )])

    def test_nombre_odf_es_unico_global_sin_distinguir_mayusculas(self):
        crear_jerarquia_odf(nombre='ODF-GLOBAL')
        hub = HubSite.objects.create(nombre='HUB-OTRO')
        sala = SalaTecnica.objects.create(hub_site=hub, nombre='SALA-OTRA')
        rack = RackFisico.objects.create(sala=sala, nombre='RACK-OTRO')
        with self.assertRaises(IntegrityError), transaction.atomic():
            InventarioODF.objects.bulk_create([
                InventarioODF(rack_obj=rack, odf='odf-global')
            ])

    def test_puerto_sin_odf_es_rechazado_por_base_de_datos(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            DetallePuertoODF.objects.bulk_create(
                [DetallePuertoODF(puerto_odf='1')]
            )

    def test_puerto_duplicado_es_rechazado(self):
        _, _, _, odf = crear_jerarquia_odf()
        DetallePuertoODF.objects.create(odf_obj=odf, puerto_odf='1')
        with self.assertRaises(IntegrityError), transaction.atomic():
            DetallePuertoODF.objects.bulk_create(
                [DetallePuertoODF(odf_obj=odf, puerto_odf='1')]
            )

    def test_renombrar_odf_se_refleja_en_sus_puertos(self):
        _, _, _, odf = crear_jerarquia_odf(nombre='ODF-OLD')
        puerto = DetallePuertoODF.objects.create(odf_obj=odf, puerto_odf='1')
        odf.odf = 'ODF-NEW'
        odf.save()
        puerto.refresh_from_db()
        self.assertEqual(puerto.odf_obj.odf, 'ODF-NEW')

    def test_puerto_reservado_reduce_disponibilidad_y_se_importa_sin_columnas_nuevas(self):
        hub, _, _, odf = crear_jerarquia_odf(nombre='ODF-RESERVAS', capacidad=4)
        archivo = SimpleUploadedFile(
            'puertos.csv',
            (
                'hub_site,odf,puerto_odf,estado_puerto\n'
                f'{hub.nombre},{odf.odf},1,Reservado\n'
            ).encode(),
            content_type='text/csv',
        )

        resultado = _procesar_puertos_odf_inventario(archivo)

        puerto = DetallePuertoODF.objects.get(odf_obj=odf, puerto_odf='1')
        odf.refresh_from_db()
        self.assertEqual(resultado['creadas'], 1)
        self.assertEqual(puerto.estado_puerto, 'RESERVADO')
        self.assertEqual(odf.puertos_reservados, 1)
        self.assertEqual(odf.puertos_ocupados, 0)
        self.assertEqual(odf.puertos_libres, 3)

    def test_importacion_no_infiere_reserva_desde_el_destino(self):
        hub, _, _, odf = crear_jerarquia_odf(nombre='ODF-RESERVA-LEGADA', capacidad=4)
        archivo = SimpleUploadedFile(
            'puertos.csv',
            (
                'hub_site,odf,puerto_odf,estado_puerto,destino\n'
                f'{hub.nombre},{odf.odf},1,Ocupado,SW-01 | Reservado proyecto CCTV\n'
            ).encode(),
            content_type='text/csv',
        )

        with self.assertRaises(ValueError):
            _procesar_puertos_odf_inventario(archivo)

        odf.refresh_from_db()
        self.assertFalse(
            DetallePuertoODF.objects.filter(odf_obj=odf, puerto_odf='1').exists()
        )
        self.assertEqual(odf.puertos_reservados, 0)
        self.assertEqual(odf.puertos_ocupados, 0)
        self.assertEqual(odf.puertos_libres, 4)

class IntegridadRutaTests(TestCase):
    def test_impide_otu_contradictorio_entre_ruta_y_puerto(self):
        otu_a = OTU.objects.create(nombre='OTU-A')
        otu_b = OTU.objects.create(nombre='OTU-B')
        ruta = Ruta.objects.create(nombre='RUTA-A', otu=otu_a)
        puerto = PuertoOTU(otu=otu_b, numero=1, ruta_asociada=ruta)
        with self.assertRaises(ValidationError):
            puerto.full_clean()

    def test_impide_coordenada_asociada_a_tramo_de_otra_ruta(self):
        ruta_a = Ruta.objects.create(nombre='RUTA-A')
        ruta_b = Ruta.objects.create(nombre='RUTA-B')
        tramo_b = InventarioTramo.objects.create(ruta=ruta_b, tramo_secuencia=1)
        coordenada = CoordenadaRuta(
            ruta=ruta_a,
            tramo=tramo_b,
            tramo_secuencia=1,
            orden=1,
            latitud=-23,
            longitud=-69,
        )
        with self.assertRaises(ValidationError):
            coordenada.full_clean()

    def test_borrar_tramo_conserva_coordenadas_e_hitos(self):
        ruta = Ruta.objects.create(nombre='RUTA-CONSERVAR-GEO')
        tramo = InventarioTramo.objects.create(ruta=ruta, tramo_secuencia=1)
        coordenada = CoordenadaRuta.objects.create(
            ruta=ruta,
            tramo=tramo,
            orden=1,
            latitud='-23.0000000',
            longitud='-69.0000000',
        )
        tramo.delete()
        coordenada.refresh_from_db()
        self.assertIsNone(coordenada.tramo_id)
        self.assertEqual(coordenada.tramo_secuencia, 1)

    def test_borrado_permanente_de_ruta_elimina_id_onmsi_relacionado(self):
        ruta = Ruta.objects.create(nombre='RUTA-ID-CASCADE')
        IDRuta.objects.create(ruta_obj=ruta, id_onmsi=9001)
        ruta.delete()
        self.assertFalse(IDRuta.objects.filter(id_onmsi=9001).exists())


class InventarioFisicoTests(TransactionTestCase):
    def test_fibra_duplicada_en_la_misma_troncal_es_rechazada(self):
        ruta = Ruta.objects.create(nombre='TRONCAL-FIBRAS')
        InventarioFibra.objects.create(ruta=ruta, fibra_numero='1')
        with self.assertRaises(IntegrityError), transaction.atomic():
            InventarioFibra.objects.bulk_create([
                InventarioFibra(ruta=ruta, fibra_numero='1'),
            ])

    def test_reserva_reutiliza_la_tabla_de_planta_externa(self):
        ruta = Ruta.objects.create(nombre='TRONCAL-ELEMENTO')
        tramo = InventarioTramo.objects.create(ruta=ruta, tramo_secuencia=1)
        elemento = Reserva.objects.create(
            ruta=ruta,
            tramo=tramo,
            tipo='MUFA',
            nombre='MUFA-TRAMO-01',
            latitud=-23,
            longitud=-69,
        )
        self.assertEqual(elemento.ruta, ruta)

    def test_una_sola_referencia_activa_por_ruta(self):
        ruta = Ruta.objects.create(nombre='RUTA-REF')
        TrazaReferencia.objects.create(
            ruta=ruta,
            nombre='Referencia 1',
            archivo_sor='trazas_referencia/ref1.sor',
            activa=True,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            TrazaReferencia.objects.create(
                ruta=ruta,
                nombre='Referencia 2',
                archivo_sor='trazas_referencia/ref2.sor',
                activa=True,
            )

    def test_empalme_no_admite_la_misma_fibra(self):
        ruta = Ruta.objects.create(nombre='TRONCAL-EMP')
        fibra = InventarioFibra.objects.create(ruta=ruta, fibra_numero='1')
        elemento = Reserva.objects.create(
            ruta=ruta,
            tipo='MUFA',
            nombre='MUFA-01',
            latitud=-23,
            longitud=-69,
        )
        empalme = EmpalmeFibra(
            elemento=elemento,
            fibra_entrada=fibra,
            fibra_salida=fibra,
        )
        with self.assertRaises(ValidationError):
            empalme.full_clean()

    def test_empalme_rechaza_fibras_de_troncales_distintas(self):
        ruta_a = Ruta.objects.create(nombre='TRONCAL-EMP-A')
        ruta_b = Ruta.objects.create(nombre='TRONCAL-EMP-B')
        fibra_a = InventarioFibra.objects.create(ruta=ruta_a, fibra_numero='1')
        fibra_b = InventarioFibra.objects.create(ruta=ruta_b, fibra_numero='1')
        elemento = Reserva.objects.create(
            ruta=ruta_a,
            tipo='Mufa',
            nombre='MUFA-A',
            latitud=-23,
            longitud=-69,
        )
        with self.assertRaises(ValidationError):
            EmpalmeFibra(
                elemento=elemento,
                fibra_entrada=fibra_a,
                fibra_salida=fibra_b,
            ).full_clean()

    def test_terminacion_define_odf_por_fibra_y_ocupa_puerto(self):
        _, _, _, odf_a = crear_jerarquia_odf(nombre='ODF-EXT-A')
        _, _, _, odf_b = crear_jerarquia_odf(nombre='ODF-EXT-B')
        puerto_a = DetallePuertoODF.objects.create(
            odf_obj=odf_a,
            puerto_odf='1',
            estado_puerto='RESERVADO',
        )
        puerto_b = DetallePuertoODF.objects.create(
            odf_obj=odf_b,
            puerto_odf='1',
            estado_puerto='LIBRE',
        )
        ruta = Ruta.objects.create(nombre='TRONCAL-TERMINACION')
        fibra = InventarioFibra.objects.create(ruta=ruta, fibra_numero='F1')

        TerminacionFibra.objects.create(
            fibra=fibra,
            extremo='A',
            puerto_odf=puerto_a,
        )
        TerminacionFibra.objects.create(
            fibra=fibra,
            extremo='B',
            puerto_odf=puerto_b,
        )
        puerto_a.refresh_from_db()
        puerto_b.refresh_from_db()
        self.assertEqual(puerto_a.estado_puerto, 'OCUPADO')
        self.assertEqual(puerto_b.estado_puerto, 'OCUPADO')


class ImportacionV5Tests(TestCase):
    def test_ruta_acepta_marcadores_sin_dato_en_enlace(self):
        archivo = io.BytesIO(
            b'Ruta,OTU,Distancia (m),OLT,SLOT OLT,PUERTO OLT,PON,Enlace\n'
            b'RUTA-SIN-URL,,1000,,,,,SIN_DATO\n'
        )
        resultado = _procesar_ruta_otu(archivo)
        ruta = Ruta.objects.get(nombre='RUTA-SIN-URL')
        self.assertIsNone(ruta.enlace)
        self.assertEqual(resultado['creadas'], 1)

    def test_reservas_homonimas_con_coordenadas_distintas_no_colapsan(self):
        Ruta.objects.create(nombre='RUTA-P57')
        archivo = io.BytesIO(
            b'Enlace,Landmark name,Connection type,Reserva (m),Latitud,Longitud\n'
            b'RUTA-P57,P57,Poste,0,-24.2724030,-69.0920510\n'
            b'RUTA-P57,P57,Poste,0,-24.2716100,-69.0920390\n'
        )
        resultado = _procesar_reservas(archivo)
        self.assertEqual(Reserva.objects.filter(ruta__nombre='RUTA-P57').count(), 2)
        self.assertEqual(resultado['creadas'], 2)

    def test_zip_segmentado_reemplaza_geografia_sin_tocar_tramos_tecnicos(self):
        ruta = Ruta.objects.create(nombre='RUTA-ZIP')
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            estado='OPERATIVO',
            capacidad='144 Hilos',
            hub_site='SITE-A',
            distancia_m=987.5,
        )
        inventario_antes = list(
            ruta.tramos_inventario.order_by('pk').values()
        )
        contenido = (
            'Latitude,Longitude,tipo_trazado,new_seg\n'
            '-23.0000000,-69.0000000,AEREO,false\n'
            '-23.0010000,-69.0010000,AEREO,false\n'
            '-23.0020000,-69.0020000,SOTERRADO,true\n'
        ).encode()
        archivo = io.BytesIO()
        with zipfile.ZipFile(archivo, 'w') as paquete:
            paquete.writestr('RUTA-ZIP.csv', contenido)
        archivo.seek(0)

        _procesar_coordenadas_inventario_zip(archivo)
        self.assertEqual(
            list(ruta.tramos_inventario.order_by('pk').values()),
            inventario_antes,
        )
        coordenadas = list(ruta.coordenadas.order_by('orden'))
        self.assertEqual(
            [coordenada.tipo_trazado for coordenada in coordenadas],
            ['AEREO', 'AEREO', 'SOTERRADO'],
        )
        self.assertEqual(
            [coordenada.inicio_segmento for coordenada in coordenadas],
            [True, False, True],
        )
        self.assertEqual(
            [coordenada.tramo_secuencia for coordenada in coordenadas],
            [1, 1, 2],
        )
        self.assertTrue(all(
            coordenada.tramo_id is None for coordenada in coordenadas
        ))

    def test_csv_geografico_crea_ruta_sin_inventario_tramo(self):
        archivo = io.BytesIO(
            (
                'ruta,latitude,longitude,tipo_trazado,new_seg\n'
                'RUTA-NUEVA,-23.0000000,-69.0000000,AEREO,false\n'
                'RUTA-NUEVA,-23.0010000,-69.0010000,SOTERRADO,true\n'
            ).encode()
        )

        _procesar_coordenadas_csv(archivo)

        ruta = Ruta.objects.get(nombre='RUTA-NUEVA')
        self.assertEqual(ruta.coordenadas.count(), 2)
        self.assertFalse(ruta.tramos_inventario.exists())

    def test_recargar_csv_geografico_preserva_inventario_tecnico(self):
        ruta = Ruta.objects.create(nombre='RUTA-CSV-EXISTENTE')
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            estado='OPERATIVO',
            capacidad='64 Hilos',
            hub_site='SITE-ORIGEN',
            distancia_m=700,
        )
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=2,
            estado='MANTENIMIENTO',
            capacidad='48 Hilos',
            destino='SITE-DESTINO',
            distancia_m=300,
        )
        inventario_antes = list(
            ruta.tramos_inventario.order_by('pk').values()
        )
        contenido = (
            'ruta,latitude,longitude,tipo_trazado,new_seg\n'
            'RUTA-CSV-EXISTENTE,-23.0000000,-69.0000000,AEREO,false\n'
            'RUTA-CSV-EXISTENTE,-23.0010000,-69.0010000,AEREO,false\n'
            'RUTA-CSV-EXISTENTE,-23.0020000,-69.0020000,SOTERRADO,true\n'
            'RUTA-CSV-EXISTENTE,-23.0030000,-69.0030000,AEREO,true\n'
        ).encode()

        for _ in range(2):
            _procesar_coordenadas_csv(io.BytesIO(contenido))
            self.assertEqual(
                list(ruta.tramos_inventario.order_by('pk').values()),
                inventario_antes,
            )

        coordenadas = list(ruta.coordenadas.order_by('orden'))
        self.assertEqual(len(coordenadas), 4)
        self.assertEqual(
            [coordenada.inicio_segmento for coordenada in coordenadas],
            [True, False, True, True],
        )
        self.assertTrue(all(
            coordenada.tramo_id is None for coordenada in coordenadas
        ))

    def test_csv_geografico_invalido_conserva_geografia_e_inventario(self):
        ruta = Ruta.objects.create(nombre='RUTA-ROLLBACK')
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            capacidad='48 Hilos',
        )
        CoordenadaRuta.objects.create(
            ruta=ruta,
            orden=1,
            latitud='-23.0000000',
            longitud='-69.0000000',
            tipo_trazado='AEREO',
            inicio_segmento=True,
        )
        coordenadas_antes = list(
            ruta.coordenadas.order_by('pk').values()
        )
        inventario_antes = list(
            ruta.tramos_inventario.order_by('pk').values()
        )
        archivo = io.BytesIO(
            (
                'ruta,latitude,longitude,tipo_trazado,new_seg\n'
                'RUTA-ROLLBACK,-23.1000000,-69.1000000,AEREO,false\n'
                'RUTA-ROLLBACK,999,-69.2000000,AEREO,false\n'
            ).encode()
        )

        with self.assertRaises(ValueError):
            _procesar_coordenadas_csv(archivo)

        self.assertEqual(
            list(ruta.coordenadas.order_by('pk').values()),
            coordenadas_antes,
        )
        self.assertEqual(
            list(ruta.tramos_inventario.order_by('pk').values()),
            inventario_antes,
        )

    def test_zip_simple_no_modifica_inventario_tecnico(self):
        ruta = Ruta.objects.create(nombre='RUTA-ZIP-SIMPLE')
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            estado='OPERATIVO',
        )
        inventario_antes = list(
            ruta.tramos_inventario.order_by('pk').values()
        )
        archivo = io.BytesIO()
        with zipfile.ZipFile(archivo, 'w') as paquete:
            paquete.writestr(
                'RUTA-ZIP-SIMPLE.csv',
                (
                    'Latitude,Longitude\n'
                    '-23.0000000,-69.0000000\n'
                    '-23.0010000,-69.0010000\n'
                ),
            )
        archivo.seek(0)

        _procesar_coordenadas_zip(archivo)

        self.assertEqual(
            list(ruta.tramos_inventario.order_by('pk').values()),
            inventario_antes,
        )
        self.assertEqual(
            list(ruta.coordenadas.values_list(
                'tipo_trazado', 'inicio_segmento', 'tramo_id'
            )),
            [('DESCONOCIDO', True, None), ('DESCONOCIDO', False, None)],
        )

    def test_formato_legacy_no_se_aplica_ambiguamente_a_varios_tramos(self):
        ruta = Ruta.objects.create(nombre='RUTA-METRICAS')
        InventarioTramo.objects.create(ruta=ruta, tramo_secuencia=1, distancia_m=100)
        InventarioTramo.objects.create(ruta=ruta, tramo_secuencia=2, distancia_m=200)
        archivo = io.BytesIO(
            b'ruta,distancia,mufas,splitters,reservas_m,hilos_ocupados,hilos_libres\n'
            b'RUTA-METRICAS,300,2,1,50,3,9\n'
        )
        resultado = _procesar_tramos_inventario(archivo)
        ruta.refresh_from_db()
        tramos = list(ruta.tramos_inventario.order_by('tramo_secuencia'))
        self.assertIsNone(ruta.distancia_m)
        self.assertEqual([tramo.distancia_m for tramo in tramos], [100, 200])
        self.assertEqual([tramo.mufas for tramo in tramos], [0, 0])
        self.assertEqual(resultado['rechazadas'], 1)
        self.assertTrue(
            any(
                'no identifica' in advertencia
                for advertencia in resultado['advertencias']
            )
        )

    def test_carga_tecnica_crea_registro_inicial_si_la_ruta_no_tiene_tramos(self):
        ruta = Ruta.objects.create(nombre='RUTA-TECNICA-NUEVA')
        archivo = io.BytesIO(
            (
                'ruta,estado,capacidad,tipo_fibra,hilos_ocupados,hilos_libres\n'
                'RUTA-TECNICA-NUEVA,OPERATIVO,48,G.652D,12,36\n'
            ).encode()
        )

        resultado = _procesar_tramos_inventario(archivo)

        tramo = ruta.tramos_inventario.get(tramo_secuencia=1)
        self.assertEqual(resultado['actualizadas'], 1)
        self.assertEqual(tramo.estado, 'OPERATIVO')
        self.assertEqual(tramo.capacidad, '48 Hilos')
        self.assertEqual(tramo.hilos_ocupados, 12)
        self.assertEqual(tramo.hilos_libres, 36)

    def test_carga_tecnica_no_sobrescribe_distancia_geografica(self):
        ruta = Ruta.objects.create(
            nombre='RUTA-DISTANCIA-GEO',
            distancia_m=1250,
        )
        CoordenadaRuta.objects.bulk_create([
            CoordenadaRuta(
                ruta=ruta,
                orden=1,
                latitud='-23.0000000',
                longitud='-69.0000000',
                tipo_trazado='AEREO',
                inicio_segmento=True,
            ),
            CoordenadaRuta(
                ruta=ruta,
                orden=2,
                latitud='-23.0010000',
                longitud='-69.0010000',
                tipo_trazado='AEREO',
            ),
        ])
        archivo = io.BytesIO(
            (
                'ruta,distancia,capacidad\n'
                'RUTA-DISTANCIA-GEO,9999,48\n'
            ).encode()
        )

        _procesar_tramos_inventario(archivo)

        ruta.refresh_from_db()
        tramo = ruta.tramos_inventario.get(tramo_secuencia=1)
        self.assertEqual(ruta.distancia_m, 1250)
        self.assertEqual(tramo.distancia_m, 9999)

    def test_trazado_exige_al_menos_dos_coordenadas(self):
        archivo = io.BytesIO(
            (
                'ruta,latitude,longitude,tipo_trazado\n'
                'RUTA-UN-PUNTO,-23.0000000,-69.0000000,AEREO\n'
            ).encode()
        )

        with self.assertRaisesRegex(ValueError, 'al menos dos'):
            _procesar_coordenadas_csv(archivo)

        self.assertFalse(Ruta.objects.filter(
            nombre='RUTA-UN-PUNTO'
        ).exists())

    def test_carga_web_registra_lote_con_contadores_y_origen(self):
        usuario = User.objects.create_user('importador', password='clave')
        usuario.user_permissions.add(*Permission.objects.filter(
            codename__in=['add_ruta', 'change_ruta', 'add_otu']
        ))
        self.client.force_login(usuario)
        archivo = SimpleUploadedFile(
            'rutas.csv',
            (
                'Ruta,OTU,Distancia (m),OLT,SLOT OLT,PUERTO OLT,PON,Enlace\n'
                'RUTA-LOTE,,1000,,,,,NO_APLICA\n'
            ).encode(),
            content_type='text/csv',
        )
        respuesta = self.client.post(
            reverse('cargar_csv', args=['ruta_otu']),
            {'csv_file': archivo},
        )
        self.assertEqual(respuesta.status_code, 302)
        lote = LoteImportacion.objects.get()
        self.assertEqual(lote.estado, 'COMPLETADO')
        self.assertEqual(lote.total_filas, 1)
        self.assertEqual(lote.filas_creadas, 1)
        self.assertEqual(lote.filas_actualizadas, 0)
        self.assertEqual(lote.filas_rechazadas, 0)
        self.assertEqual(Ruta.objects.get(nombre='RUTA-LOTE').lote_importacion, lote)

    def test_carga_parcial_se_registra_como_completada_con_errores(self):
        usuario = User.objects.create_user('importador-parcial', password='clave')
        usuario.user_permissions.add(*Permission.objects.filter(
            codename__in=['add_ruta', 'change_ruta', 'add_otu']
        ))
        self.client.force_login(usuario)
        archivo = SimpleUploadedFile(
            'rutas.csv',
            (
                'Ruta,OTU,Distancia (m),OLT,SLOT OLT,PUERTO OLT,PON,Enlace\n'
                'RUTA-VALIDA,,1000,,,,,NO_APLICA\n'
                ',OTU-SIN-RUTA,1000,,,,,NO_APLICA\n'
            ).encode(),
            content_type='text/csv',
        )

        respuesta = self.client.post(
            reverse('cargar_csv', args=['ruta_otu']),
            {'csv_file': archivo},
        )

        self.assertEqual(respuesta.status_code, 302)
        lote = LoteImportacion.objects.get()
        self.assertEqual(lote.estado, 'COMPLETADO_CON_ERRORES')
        self.assertEqual(lote.total_filas, 2)
        self.assertEqual(lote.filas_creadas, 1)
        self.assertEqual(lote.filas_rechazadas, 1)


class AutorizacionTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user('operador', password='clave-segura')
        self.client.force_login(self.usuario)

    def test_usuario_sin_permiso_no_crea_ruta(self):
        response = self.client.post(
            reverse('api_create_ruta'),
            data=json.dumps({'nombre': 'RUTA-NO-AUTORIZADA'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Ruta.objects.filter(nombre='RUTA-NO-AUTORIZADA').exists())

    def test_usuario_sin_permiso_no_crea_odf(self):
        response = self.client.post(
            reverse('api_create_odf'),
            data=json.dumps({'hub_site': 'H', 'sala': 'S', 'rack': 'R', 'odf': 'O'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_vaciado_no_se_ejecuta_mediante_get(self):
        permiso = Permission.objects.get(codename='delete_inventarioodf')
        self.usuario.user_permissions.add(permiso)
        _, _, _, odf = crear_jerarquia_odf()
        response = self.client.get(reverse('vaciar_inventario_odf'))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(InventarioODF.objects.filter(pk=odf.pk).exists())

    def test_eliminacion_usuario_no_se_ejecuta_mediante_get(self):
        permiso = Permission.objects.get(codename='delete_user')
        self.usuario.user_permissions.add(permiso)
        objetivo = User.objects.create_user('objetivo', password='clave')
        response = self.client.get(reverse('eliminar_usuario', args=[objetivo.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(User.objects.filter(pk=objetivo.pk).exists())

    def test_actualizacion_invalida_de_puerto_se_revierte(self):
        permiso = Permission.objects.get(codename='change_detallepuertoodf')
        self.usuario.user_permissions.add(permiso)
        _, _, _, odf = crear_jerarquia_odf()
        DetallePuertoODF.objects.create(odf_obj=odf, puerto_odf='1')
        puerto = DetallePuertoODF.objects.create(odf_obj=odf, puerto_odf='2')
        response = self.client.post(
            reverse('api_update_puerto'),
            data=json.dumps({
                'id': puerto.pk,
                'puerto_odf': '1',
                'estado_puerto': 'Libre',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        puerto.refresh_from_db()
        self.assertEqual(puerto.puerto_odf, '2')

    def test_gestion_de_puerto_acepta_reserva(self):
        permiso = Permission.objects.get(codename='change_detallepuertoodf')
        self.usuario.user_permissions.add(permiso)
        _, _, _, odf = crear_jerarquia_odf(capacidad=2)
        puerto = DetallePuertoODF.objects.create(odf_obj=odf, puerto_odf='1')

        response = self.client.post(
            reverse('api_gestionar_conexion_puerto'),
            data=json.dumps({
                'accion': 'reservar',
                'puerto_id': puerto.pk,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        puerto.refresh_from_db()
        odf.refresh_from_db()
        self.assertEqual(puerto.estado_puerto, 'RESERVADO')
        self.assertEqual(odf.puertos_reservados, 1)
        self.assertEqual(odf.puertos_libres, 0)

    def test_alarmas_no_son_publicas(self):
        self.client.logout()
        response = self.client.get(reverse('get_alarmas_activas'))
        self.assertEqual(response.status_code, 302)

    def test_permiso_crear_usuario_no_permita_crear_superusuario(self):
        formulario = CustomUserCreationForm(
            {
                'username': 'nuevo-operador',
                'email': 'operador@example.test',
                'password1': 'Clave-segura-2026',
                'password2': 'Clave-segura-2026',
                'is_staff': 'on',
                'is_superuser': 'on',
            },
            actor=self.usuario,
        )
        self.assertTrue(formulario.is_valid(), formulario.errors)
        nuevo = formulario.save()
        self.assertFalse(nuevo.is_staff)
        self.assertFalse(nuevo.is_superuser)

    def test_login_no_redirige_a_dominio_externo(self):
        self.client.logout()
        response = self.client.post(
            reverse('login') + '?next=https://example.com/phishing',
            {'username': 'operador', 'password': 'clave-segura'},
        )
        self.assertRedirects(response, reverse('mapa_inventario'), fetch_redirect_response=False)


@override_settings(
    FIBERGENIUS_LOGIN_MAX_ATTEMPTS=2,
    FIBERGENIUS_LOGIN_ATTEMPT_WINDOW_SECONDS=300,
    FIBERGENIUS_LOGIN_LOCKOUT_SECONDS=120,
    FIBERGENIUS_IDLE_TIMEOUT_SECONDS=60,
    FIBERGENIUS_IDLE_TOUCH_SECONDS=1,
)
class ControlesAccesoTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            'usuario-seguro',
            password='Clave-segura-2026',
        )

    def test_bloquea_temporalmente_despues_del_limite_de_intentos(self):
        login_url = reverse('login')
        credenciales = {
            'username': self.usuario.username,
            'password': 'incorrecta',
        }

        primero = self.client.post(login_url, credenciales)
        segundo = self.client.post(login_url, credenciales)
        aun_bloqueado = self.client.post(login_url, {
            'username': self.usuario.username,
            'password': 'Clave-segura-2026',
        })

        self.assertEqual(primero.status_code, 200)
        self.assertEqual(segundo.status_code, 429)
        self.assertEqual(aun_bloqueado.status_code, 429)
        self.assertEqual(
            LoginThrottle.objects.filter(locked_until__isnull=False).count(),
            2,
        )
        self.assertContains(
            segundo,
            'Acceso bloqueado temporalmente',
            status_code=429,
        )

    def test_cierra_sesion_expirada_por_inactividad(self):
        self.client.force_login(self.usuario)
        session = self.client.session
        session['_fg_last_activity'] = int(time.time()) - 61
        session.save()

        response = self.client.get(reverse('mapa_inventario'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"{reverse('login')}?reason=inactive",
        )
        self.assertNotIn('_auth_user_id', self.client.session)


class WebhookTests(TestCase):
    def _payload(self):
        return {
            'route_name': 'RUTA-WEBHOOK',
            'status': 'Active',
            'alarm_type': 'Fiber Cut',
            'alarm_level': 'Critical',
            'latitude': '-23.1234567',
            'longitude': '-69.1234567',
            'port': '1',
            'timestamp': '2026-07-16T20:00:00-05:00',
        }

    def _firma(self, body, timestamp):
        mensaje = str(timestamp).encode('ascii') + b'.' + body
        return hmac.new(
            b'secreto-pruebas-webhook', mensaje, hashlib.sha256
        ).hexdigest()

    def test_rechaza_webhook_sin_firma(self):
        body = json.dumps(self._payload()).encode()
        response = self.client.post(
            reverse('webhook_alarma'),
            data=body,
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(AlarmaVeex.objects.count(), 0)

    def test_acepta_webhook_hmac_y_crea_auditoria(self):
        Ruta.objects.create(nombre='RUTA-WEBHOOK')
        body = json.dumps(self._payload(), separators=(',', ':')).encode()
        timestamp = int(time.time())
        response = self.client.post(
            reverse('webhook_alarma'),
            data=body,
            content_type='application/json',
            HTTP_X_FIBERGENIUS_TIMESTAMP=str(timestamp),
            HTTP_X_FIBERGENIUS_SIGNATURE='sha256=' + self._firma(body, timestamp),
        )
        self.assertEqual(response.status_code, 200, response.content)
        alarma = AlarmaVeex.objects.get()
        self.assertIsNotNone(alarma.ruta_obj)
        self.assertIsNotNone(alarma.timestamp_evento)
        self.assertEqual(EventLog.objects.filter(incident=alarma).count(), 1)
        with self.assertRaises(ProtectedError):
            alarma.delete()


class SincronizacionOTDRTests(TestCase):
    def test_parsea_y_actualiza_medicion_sin_sql_especifico_de_motor(self):
        xml = b'''<?xml version="1.0" encoding="UTF-8"?>
        <root>
          <measurementOnLink>
            <internalKey>7001</internalKey>
            <measurementUid>med-7001</measurementUid>
            <linkInternalKey>7001</linkInternalKey>
            <acquisitionDate>2026-07-16T20:00:00Z</acquisitionDate>
            <result>PASS</result>
            <measurementOnLinkOtdrResult>
              <fiberLengthM>1234.5</fiberLengthM>
              <wavelengthNm>1550</wavelengthNm>
              <linkLossAlarm>false</linkLossAlarm>
              <linkLossdB>2.3</linkLossdB>
            </measurementOnLinkOtdrResult>
          </measurementOnLink>
        </root>'''
        mediciones = parsear_xml(xml, 'RUTA-OTDR', 7001)
        self.assertEqual(len(mediciones), 1)
        guardar_en_db_sobrescribiendo_por_enlace(mediciones)
        mediciones[0]['fiber_length'] = 1300.0
        guardar_en_db_sobrescribiendo_por_enlace(mediciones)

        self.assertEqual(Medicion.objects.count(), 1)
        self.assertEqual(Medicion.objects.get().fiber_length, 1300.0)

    def test_guarda_prueba_y_reemplaza_eventos_relacionados(self):
        payload = {
            'tests': [{
                'status': 'PASS',
                'configuration': {'otdrSettings': {'fiber': {'cableId': 'CB-1', 'fiberId': 'F-1'}}},
                'results': {'data': {'otdrResults': {'measuredResults': [{
                    'acqDateTime': '2026-07-16T20:00:00+00:00',
                    'fiberLengthM': 500,
                    'numberOfEvents': 1,
                    'events': [{'id': 1, 'eventType': 'SPLICE', 'distanceM': 250}],
                }]}}},
            }],
        }
        self.assertEqual(guardar_eventos(payload, 'RUTA-EVENTOS', 7001), 1)
        payload['tests'][0]['results']['data']['otdrResults']['measuredResults'][0]['events'] = [
            {'id': 2, 'eventType': 'END', 'distanceM': 500}
        ]
        self.assertEqual(guardar_eventos(payload, 'RUTA-EVENTOS', 7001), 1)

        self.assertEqual(PruebaOTDR.objects.count(), 1)
        self.assertEqual(EventoOTDRDetalle.objects.count(), 1)
        self.assertEqual(EventoOTDRDetalle.objects.get().event_id, 2)

    def test_prueba_con_eventos_no_se_borra_silenciosamente(self):
        prueba = PruebaOTDR.objects.create(internal_key='protegida')
        EventoOTDRDetalle.objects.create(prueba=prueba, event_id=1)
        with self.assertRaises(ProtectedError):
            prueba.delete()


class GuiOperativaTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user('operador-gui', password='clave-segura')
        self.usuario.user_permissions.add(*Permission.objects.filter(codename__in=[
            'view_ruta',
            'view_inventariofibra',
            'view_reserva',
            'view_inventarioodf',
            'view_detallepuertoodf',
            'view_hubsite',
            'add_inventariotramo',
        ]))
        self.client.force_login(self.usuario)
        self.site, _, _, self.odf = crear_jerarquia_odf(nombre='ODF-GUI', capacidad=3)
        self.site.latitud = '-12.0463740'
        self.site.longitud = '-77.0427930'
        self.site.direccion = 'Lima Centro'
        self.site.save(update_fields=['latitud', 'longitud', 'direccion'])
        self.puertos = [
            DetallePuertoODF.objects.create(
                odf_obj=self.odf,
                puerto_odf=str(numero),
                estado_puerto='RESERVADO' if numero == 3 else 'LIBRE',
                destino=f'DESTINO-{numero}',
            )
            for numero in range(1, 4)
        ]
        DetallePuertoODF.objects.filter(pk=self.puertos[0].pk).update(
            estado_puerto='OCUPADO'
        )
        self.puertos[0].refresh_from_db()
        self.odf.actualizar_contadores()
        self.troncal = Ruta.objects.create(nombre='TRONCAL-GUI')
        self.tramo = InventarioTramo.objects.create(
            ruta=self.troncal,
            tramo_secuencia=1,
            hub_site=self.odf.hub_site,
            odf_nombre=self.odf.odf,
            destino='DESTINO-GUI',
            capacidad='12 Fibras',
        )
        CoordenadaRuta.objects.create(
            ruta=self.troncal,
            tramo=self.tramo,
            orden=1,
            latitud='-12.0463740',
            longitud='-77.0427930',
            tipo_trazado='AEREO',
            inicio_segmento=True,
        )
        self.fibra = InventarioFibra.objects.create(
            ruta=self.troncal,
            fibra_numero='F1',
            estado='OCUPADO',
            origen_estado='INFORMADO',
            nombre_fibra='DESTINO-GUI',
        )
        self.elemento = Reserva.objects.create(
            ruta=self.troncal,
            nombre='MUFA-GUI',
            tipo='MUFA',
            latitud=-23,
            longitud=-69,
        )

    def test_selector_de_extremos_no_impone_orientacion_geografica(self):
        self.usuario.user_permissions.add(
            Permission.objects.get(codename='change_detallepuertoodf')
        )
        response = self.client.get(reverse('planta_interna'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '>Extremo A<')
        self.assertContains(response, '>Extremo B<')
        self.assertContains(response, 'no define la orientación geográfica')
        self.assertNotContains(response, 'Extremo A (inicio)')

    def test_puertos_se_entregan_paginados_y_filtrados_desde_el_servidor(self):
        response = self.client.get(reverse('api_puertos_paginados'), {
            'q': 'DESTINO-1',
            'page_size': 25,
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['pagination']['total'], 1)
        self.assertEqual(payload['data'][0]['puerto'], '1')
        self.assertEqual(payload['summary']['total'], 1)
        self.assertEqual(payload['summary']['ocupados'], 1)
        self.assertEqual(payload['summary']['top_odfs'][0]['nombre'], 'ODF-GUI')
        self.assertEqual(payload['summary']['top_odfs'][0]['utilizacion'], 100)
        self.assertIn('/api/inventario/360/puerto/', payload['data'][0]['detail_url'])

    def test_puertos_se_filtran_por_ubicacion_fisica(self):
        response = self.client.get(reverse('api_puertos_paginados'), {
            'site': self.odf.hub_site,
            'sala': self.odf.sala,
            'rack': self.odf.rack,
            'page_size': 10,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['pagination']['total'], 3)

    def test_puertos_reservados_se_filtran_y_resumen_como_estado_independiente(self):
        response = self.client.get(reverse('api_puertos_paginados'), {
            'estado': 'RESERVADO',
            'page_size': 10,
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['pagination']['total'], 1)
        self.assertEqual(payload['data'][0]['estado'], 'RESERVADO')
        self.assertEqual(payload['summary']['reservados'], 1)

    def test_puertos_del_mapa_se_filtran_por_odf_exacto_y_admiten_48_por_pagina(self):
        otro_odf = InventarioODF.objects.create(
            rack_obj=self.odf.rack_obj,
            odf='ODF-GUI-OTRO',
            capacidad_puertos=1,
            puertos_libres=1,
        )
        DetallePuertoODF.objects.create(
            odf_obj=otro_odf,
            puerto_odf='1',
            estado_puerto='LIBRE',
        )

        response = self.client.get(reverse('api_puertos_paginados'), {
            'odf_id': self.odf.pk,
            'page_size': 48,
        })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['pagination']['page_size'], 48)
        self.assertEqual(payload['pagination']['total'], 3)
        self.assertEqual({item['odf'] for item in payload['data']}, {'ODF-GUI'})
        self.assertEqual(payload['summary']['reservados'], 1)

    def test_odf_seleccionado_tiene_prioridad_sobre_contexto_global_de_otro_site(self):
        response = self.client.get(reverse('api_puertos_paginados'), {
            'odf_id': self.odf.pk,
            'site': 'SITE-DISTINTO',
            'page_size': 48,
        })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['pagination']['total'], 3)
        self.assertEqual({item['odf'] for item in payload['data']}, {'ODF-GUI'})
        self.assertEqual(payload['summary']['ocupados'], 1)

    def test_puertos_del_mapa_se_ordenan_por_numero_y_no_como_texto(self):
        for numero in ('10', '11', '12'):
            DetallePuertoODF.objects.create(
                odf_obj=self.odf,
                puerto_odf=numero,
                estado_puerto='LIBRE',
            )

        response = self.client.get(reverse('api_puertos_paginados'), {
            'odf_id': self.odf.pk,
            'page_size': 48,
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item['puerto'] for item in response.json()['data']],
            ['1', '2', '3', '10', '11', '12'],
        )

    def test_puertos_odf_del_mapa_usan_la_ruta_de_cada_terminacion(self):
        TerminacionFibra.objects.create(
            fibra=self.fibra,
            extremo='A',
            puerto_odf=self.puertos[0],
        )
        otra_ruta = Ruta.objects.create(nombre='TRONCAL-PUERTO-2')
        otra_fibra = InventarioFibra.objects.create(
            ruta=otra_ruta,
            fibra_numero='F2',
            estado='OCUPADO',
            origen_estado='INFORMADO',
        )
        TerminacionFibra.objects.create(
            fibra=otra_fibra,
            extremo='A',
            puerto_odf=self.puertos[1],
        )

        response = self.client.get(reverse('api_puertos_odf_mapa'), {
            'hub_site': self.odf.hub_site,
            'odf_nombre': self.odf.odf,
        })

        self.assertEqual(response.status_code, 200)
        puertos = {
            item['puerto']: item for item in response.json()['puertos']
        }
        self.assertEqual(
            puertos['1']['ruta_troncal'],
            self.troncal.nombre,
        )
        self.assertEqual(
            puertos['2']['ruta_troncal'],
            otra_ruta.nombre,
        )
        self.assertEqual(
            set(puertos['1']),
            {
                'puerto', 'estado', 'bandeja', 'fibra', 'destino',
                'tipo_conector', 'ruta_troncal', 'estado_label',
            },
        )

    def test_puerto_odf_sin_terminacion_no_inventa_ruta(self):
        response = self.client.get(reverse('api_puertos_odf_mapa'), {
            'hub_site': self.odf.hub_site,
            'odf_nombre': self.odf.odf,
        })

        self.assertEqual(response.status_code, 200)
        puerto_ocupado = next(
            item for item in response.json()['puertos']
            if item['puerto'] == '1'
        )
        self.assertEqual(
            puerto_ocupado['ruta_troncal'],
            'No determinada',
        )

    def test_detalle_fibras_ordena_numeros_de_forma_natural(self):
        InventarioFibra.objects.create(
            ruta=self.troncal,
            fibra_numero='F10',
            estado='DISPONIBLE',
            origen_estado='INFORMADO',
        )
        InventarioFibra.objects.create(
            ruta=self.troncal,
            fibra_numero='F2',
            estado='DISPONIBLE',
            origen_estado='INFORMADO',
        )

        response = self.client.get(
            reverse('api_detalle_fibras', args=[self.troncal.nombre])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [
                item['fibra_numero']
                for item in response.json()['data']
            ],
            ['F1', 'F2', 'F10'],
        )

    def test_odfs_se_entregan_paginados_con_indicadores(self):
        response = self.client.get(reverse('api_odfs_paginados'), {
            'q': 'ODF-GUI',
            'site': self.odf.hub_site,
            'page_size': 10,
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['pagination']['total'], 1)
        self.assertEqual(payload['data'][0]['odf'], 'ODF-GUI')
        self.assertEqual(payload['summary']['total'], 1)
        self.assertEqual(payload['summary']['capacidad'], 3)
        self.assertEqual(payload['summary']['reservados'], 1)
        self.assertEqual(payload['summary']['sites'], 1)
        self.assertEqual(payload['summary']['top_ocupacion'][0]['odf'], 'ODF-GUI')
        self.assertEqual(payload['summary']['top_ocupacion'][0]['porcentaje'], 33.3)
        self.assertIn('/api/inventario/360/odf/', payload['data'][0]['detail_url'])

    def test_odfs_se_filtran_por_disponibilidad_calculada(self):
        _, _, _, odf_sin_disponibilidad = crear_jerarquia_odf(
            nombre='ODF-SIN-DISPONIBILIDAD',
            capacidad=2,
        )
        odf_sin_disponibilidad.puertos_ocupados = 1
        odf_sin_disponibilidad.puertos_reservados = 1
        odf_sin_disponibilidad.puertos_libres = 0
        odf_sin_disponibilidad.save()

        disponibles = self.client.get(
            reverse('api_odfs_paginados'),
            {'capacidad': 'disponible'},
        ).json()
        sin_disponibilidad = self.client.get(
            reverse('api_odfs_paginados'),
            {'capacidad': 'sin_disponibilidad'},
        ).json()

        self.assertEqual(disponibles['pagination']['total'], 1)
        self.assertEqual(disponibles['data'][0]['odf'], 'ODF-GUI')
        self.assertEqual(sin_disponibilidad['pagination']['total'], 1)
        self.assertEqual(
            sin_disponibilidad['data'][0]['odf'],
            'ODF-SIN-DISPONIBILIDAD',
        )

    def test_rankings_filtran_odf_troncal_y_tramo_por_identificador_exacto(self):
        odfs = self.client.get(
            reverse('api_odfs_paginados'),
            {'odf_id': self.odf.pk},
        ).json()
        troncales = self.client.get(
            reverse('api_troncales_paginadas'),
            {'ranking_id': self.troncal.pk},
        ).json()
        tramos = self.client.get(
            reverse('api_tramos_paginados'),
            {'ranking_id': self.tramo.pk},
        ).json()

        self.assertEqual(odfs['pagination']['total'], 1)
        self.assertEqual(odfs['data'][0]['id'], self.odf.pk)
        self.assertEqual(troncales['pagination']['total'], 1)
        self.assertEqual(troncales['data'][0]['id'], self.troncal.pk)
        self.assertEqual(tramos['pagination']['total'], 1)
        self.assertEqual(tramos['data'][0]['id'], self.tramo.pk)

    def test_ficha_odf_reconoce_troncal_desde_nodo_topologico(self):
        ruta = Ruta.objects.create(nombre='TRONCAL-NODO-ODF')
        nodo_odf = NodoRed.objects.create(
            tipo='ODF',
            codigo=self.odf.odf,
        )
        nodo_destino = NodoRed.objects.create(
            tipo='PUNTO',
            codigo='PUNTO-ODF-GUI',
        )
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            origen_nodo=nodo_odf,
            destino_nodo=nodo_destino,
        )

        response = self.client.get(
            reverse('asset_360', args=['odf', self.odf.pk])
        )

        self.assertEqual(response.status_code, 200)
        relacionados = next(
            item['value']
            for item in response.json()['data']['summary']
            if item['label'] == 'Troncales relacionadas'
        )
        self.assertEqual(relacionados, '2')

    def test_ficha_odf_expone_ocupacion_y_cuadricula_operativa_de_puertos(self):
        response = self.client.get(
            reverse('asset_360', args=['odf', self.odf.pk])
        )

        self.assertEqual(response.status_code, 200)
        operativo = response.json()['data']['odf_operational']
        self.assertEqual(operativo['capacity'], 3)
        self.assertEqual(operativo['occupied'], 1)
        self.assertEqual(operativo['free'], 1)
        self.assertEqual(operativo['reserved'], 1)
        self.assertEqual(operativo['utilization'], 33.3)
        self.assertEqual(
            [puerto['label'] for puerto in operativo['ports']],
            ['1', '2', '3'],
        )
        self.assertEqual(
            [puerto['status'] for puerto in operativo['ports']],
            ['OCUPADO', 'LIBRE', 'RESERVADO'],
        )
        self.assertTrue(
            all('/api/inventario/360/puerto/' in puerto['detail_url']
                for puerto in operativo['ports'])
        )

    def test_ficha_puerto_expone_estado_y_conexion_oficial(self):
        TerminacionFibra.objects.create(
            fibra=self.fibra,
            extremo='A',
            puerto_odf=self.puertos[0],
            tipo_conector='SC/APC',
        )

        response = self.client.get(
            reverse('asset_360', args=['puerto', self.puertos[0].pk])
        )

        self.assertEqual(response.status_code, 200)
        operativo = response.json()['data']['port_operational']
        self.assertTrue(operativo['is_connected'])
        self.assertFalse(operativo['is_free'])
        self.assertEqual(operativo['connector'], 'SC/APC')
        self.assertEqual(operativo['fiber']['number'], 'F1')
        self.assertEqual(operativo['fiber']['route'], 'TRONCAL-GUI')
        self.assertEqual(operativo['fiber']['endpoint'], 'Extremo A')
        self.assertIn('/api/inventario/360/fibra/', operativo['fiber']['detail_url'])
        self.assertIn('/api/inventario/360/odf/', operativo['odf_detail_url'])

    def test_ficha_puerto_libre_no_inventa_fibra_ni_destino_vacante(self):
        self.puertos[1].destino = 'VACANTE'
        self.puertos[1].bandeja = 'SIN_DATO'
        self.puertos[1].save(update_fields=['destino', 'bandeja'])

        response = self.client.get(
            reverse('asset_360', args=['puerto', self.puertos[1].pk])
        )

        self.assertEqual(response.status_code, 200)
        operativo = response.json()['data']['port_operational']
        self.assertTrue(operativo['is_free'])
        self.assertFalse(operativo['is_connected'])
        self.assertIsNone(operativo['fiber'])
        self.assertIsNone(operativo['destination'])
        self.assertIsNone(operativo['tray'])

    def test_mapa_entrega_navegacion_site_odf_con_datos_reales(self):
        response = self.client.get(
            reverse('api_mapa_site_navigation', args=[self.site.pk]),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['site']['nombre'], self.site.nombre)
        self.assertEqual(payload['summary']['total'], 1)
        self.assertEqual(payload['summary']['capacidad'], 3)
        self.assertEqual(payload['summary']['reservados'], 1)
        self.assertEqual(payload['odfs'][0]['id'], self.odf.pk)
        self.assertEqual(payload['odfs'][0]['odf'], 'ODF-GUI')
        self.assertTrue(payload['permissions']['view_ports'])
        self.assertIn('tab=fibras', payload['odfs'][0]['fibers_url'])

    def test_json_del_mapa_identifica_site_y_relaciona_tramo(self):
        response = self.client.get(reverse('api_datos_inventario'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['sites'][0]['id'], self.site.pk)
        self.assertEqual(payload['sites'][0]['nombre'], self.site.nombre)
        self.assertEqual(payload['data'][0]['tramos'][0]['hub_site_id'], self.site.pk)
        self.assertEqual(payload['data'][0]['reservas_nodos'][0]['tipo'], 'MUFA')
        self.assertEqual(payload['data'][0]['reservas_nodos'][0]['nombre'], 'MUFA-GUI')

    def test_json_del_mapa_deriva_site_desde_nodo_red(self):
        ruta = Ruta.objects.create(nombre='RUTA-NODO-SITE')
        nodo_site = NodoRed.objects.create(
            tipo='SITE',
            codigo=self.site.nombre,
        )
        nodo_destino = NodoRed.objects.create(
            tipo='PUNTO',
            codigo='PUNTO-NODO-SITE',
        )
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            origen_nodo=nodo_site,
            destino_nodo=nodo_destino,
            hub_site='',
        )
        CoordenadaRuta.objects.create(
            ruta=ruta,
            orden=1,
            latitud='-12.1000000',
            longitud='-77.1000000',
            tipo_trazado='AEREO',
            inicio_segmento=True,
        )

        response = self.client.get(reverse('api_datos_inventario'))

        self.assertEqual(response.status_code, 200)
        rutas = {
            item['nombre']: item for item in response.json()['data']
        }
        tramo = rutas[ruta.nombre]['tramos'][0]
        self.assertEqual(tramo['hub_site'], self.site.nombre)
        self.assertEqual(tramo['hub_site_id'], self.site.pk)

        ficha = self.client.get(
            reverse('asset_360', args=['site', self.site.pk])
        )
        self.assertEqual(ficha.status_code, 200)
        relacionados = next(
            item['value']
            for item in ficha.json()['data']['summary']
            if item['label'] == 'Troncales relacionadas'
        )
        self.assertEqual(relacionados, '2')

    def test_json_incluye_ruta_geografica_sin_tramo_tecnico(self):
        ruta = Ruta.objects.create(nombre='RUTA-SOLO-GEOGRAFIA')
        CoordenadaRuta.objects.bulk_create([
            CoordenadaRuta(
                ruta=ruta,
                orden=1,
                latitud='-12.1000000',
                longitud='-77.1000000',
                tipo_trazado='SOTERRADO',
                inicio_segmento=True,
            ),
            CoordenadaRuta(
                ruta=ruta,
                orden=2,
                latitud='-12.2000000',
                longitud='-77.2000000',
                tipo_trazado='SOTERRADO',
            ),
        ])

        response = self.client.get(reverse('api_datos_inventario'))

        self.assertEqual(response.status_code, 200)
        rutas = {
            item['nombre']: item for item in response.json()['data']
        }
        self.assertIn(ruta.nombre, rutas)
        self.assertEqual(
            rutas[ruta.nombre]['tramos'][0]['tipo_trazado'],
            'SOTERRADO',
        )

    def test_json_segmenta_por_geografia_y_no_por_inventario(self):
        ruta = Ruta.objects.create(nombre='RUTA-SEGMENTOS-GEO')
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            tipo_trazado='AEREO',
            capacidad='96 Hilos',
            distancia_m=100,
        )
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=2,
            tipo_trazado='SOTERRADO',
            capacidad='24 Hilos',
            distancia_m=200,
        )
        CoordenadaRuta.objects.bulk_create([
            CoordenadaRuta(
                ruta=ruta,
                orden=1,
                latitud='-12.1000000',
                longitud='-77.1000000',
                tipo_trazado='SOTERRADO',
                inicio_segmento=True,
                tramo_secuencia=1,
            ),
            CoordenadaRuta(
                ruta=ruta,
                orden=2,
                latitud='-12.2000000',
                longitud='-77.2000000',
                tipo_trazado='SOTERRADO',
                inicio_segmento=False,
                tramo_secuencia=1,
            ),
            CoordenadaRuta(
                ruta=ruta,
                orden=3,
                latitud='-12.3000000',
                longitud='-77.3000000',
                tipo_trazado='AEREO',
                inicio_segmento=True,
                tramo_secuencia=2,
            ),
        ])

        response = self.client.get(reverse('api_datos_inventario'))

        ruta_json = next(
            item for item in response.json()['data']
            if item['nombre'] == ruta.nombre
        )
        self.assertEqual(len(ruta_json['tramos']), 2)
        self.assertEqual(
            [segmento['tipo_trazado'] for segmento in ruta_json['tramos']],
            ['SOTERRADO', 'AEREO'],
        )
        self.assertEqual(
            ruta_json['tramos'][1]['coordenadas'],
            [[-12.2, -77.2], [-12.3, -77.3]],
        )
        self.assertEqual(
            [segmento['capacidad'] for segmento in ruta_json['tramos']],
            [
                '24 Hilos efectivos (24\u201396)',
                '24 Hilos efectivos (24\u201396)',
            ],
        )
        self.assertEqual(ruta_json['distancia_m'], 300)
        self.assertEqual(ruta.tramos_inventario.count(), 2)

    def test_dashboard_incluye_troncal_tecnica_sin_geometria(self):
        ruta = Ruta.objects.create(nombre='RUTA-TECNICA-SIN-GEO')
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            tipo_trazado='AEREO',
            capacidad='48 Hilos',
        )

        response = self.client.get(reverse('dashboard_inventario'))

        self.assertEqual(response.status_code, 200)
        nombres = {
            item['nombre'] for item in response.context['resumen_rutas']
        }
        self.assertIn(ruta.nombre, nombres)

    def test_json_del_mapa_admite_compresion_sin_cambiar_el_contrato(self):
        response = self.client.get(
            reverse('api_datos_inventario'),
            HTTP_ACCEPT_ENCODING='gzip',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Encoding'], 'gzip')
        payload = json.loads(gzip.decompress(response.content))
        self.assertEqual(payload['status'], 'success')
        self.assertEqual(payload['data'][0]['nombre'], 'TRONCAL-GUI')
        self.assertEqual(
            payload['data'][0]['tramos'][0]['coordenadas'],
            [[-12.046374, -77.042793]],
        )

    def test_troncales_y_tramos_se_entregan_paginados_con_indicadores(self):
        troncales = self.client.get(reverse('api_troncales_paginadas'), {
            'q': 'TRONCAL-GUI',
            'page_size': 10,
        })
        self.assertEqual(troncales.status_code, 200)
        payload = troncales.json()
        self.assertEqual(payload['pagination']['total'], 1)
        self.assertEqual(payload['data'][0]['nombre'], 'TRONCAL-GUI')
        self.assertEqual(payload['data'][0]['sin_inventariar'], 11)
        self.assertEqual(payload['data'][0]['hilos_sin_estado'], 11)
        self.assertEqual(payload['data'][0]['fibras_sin_cobertura'], 0)
        self.assertEqual(payload['summary']['total'], 1)
        self.assertEqual(payload['summary']['tramos'], 1)
        self.assertEqual(payload['summary']['fibras'], 1)
        self.assertEqual(payload['summary']['top_ocupacion'][0]['nombre'], 'TRONCAL-GUI')
        self.assertEqual(payload['summary']['top_ocupacion'][0]['utilizacion'], 8.3)

        tramos = self.client.get(reverse('api_tramos_paginados'), {
            'ruta': 'TRONCAL-GUI',
            'page_size': 10,
        })
        self.assertEqual(tramos.status_code, 200)
        payload = tramos.json()
        self.assertEqual(payload['pagination']['total'], 1)
        self.assertEqual(payload['data'][0]['secuencia'], 1)
        self.assertEqual(payload['summary']['total'], 1)

    def test_troncales_se_filtran_por_disponibilidad_y_umbral(self):
        ruta_disponible = Ruta.objects.create(nombre='TRONCAL-DISPONIBLE')
        InventarioTramo.objects.create(
            ruta=ruta_disponible,
            tramo_secuencia=1,
            hilos_ocupados=8,
            hilos_libres=2,
        )
        ruta_atencion = Ruta.objects.create(nombre='TRONCAL-ATENCION')
        InventarioTramo.objects.create(
            ruta=ruta_atencion,
            tramo_secuencia=1,
            hilos_ocupados=9,
            hilos_libres=1,
        )

        disponibles = self.client.get(
            reverse('api_troncales_paginadas'),
            {'capacidad': 'disponible'},
        ).json()
        atencion = self.client.get(
            reverse('api_troncales_paginadas'),
            {'capacidad': 'atencion'},
        ).json()

        self.assertEqual(disponibles['pagination']['total'], 2)
        self.assertEqual(
            {fila['nombre'] for fila in disponibles['data']},
            {'TRONCAL-DISPONIBLE', 'TRONCAL-ATENCION'},
        )
        self.assertEqual(atencion['pagination']['total'], 2)
        self.assertEqual(
            {fila['nombre'] for fila in atencion['data']},
            {'TRONCAL-DISPONIBLE', 'TRONCAL-ATENCION'},
        )

    def test_fibras_y_reservas_incluyen_indicadores_filtrados(self):
        fibras = self.client.get(reverse('api_fibras_paginadas'), {
            'ruta': 'TRONCAL-GUI',
            'estado': 'OCUPADO',
        })
        self.assertEqual(fibras.status_code, 200)
        payload_fibras = fibras.json()
        self.assertEqual(payload_fibras['summary']['ocupadas'], 1)
        self.assertEqual(payload_fibras['summary']['troncales'], 1)
        self.assertEqual(payload_fibras['data'][0]['ruta_id'], self.troncal.pk)
        self.assertEqual(
            payload_fibras['data'][0]['site_inicial'],
            '—',
        )
        self.assertEqual(
            payload_fibras['data'][0]['site_final'],
            '—',
        )
        self.assertEqual(
            payload_fibras['data'][0]['origen'],
            'Pendiente de orientación',
        )
        self.assertEqual(
            payload_fibras['data'][0]['destino'],
            'Pendiente de orientación',
        )
        self.assertEqual(
            payload_fibras['summary']['top_troncales'][0]['nombre'],
            'TRONCAL-GUI',
        )
        self.assertEqual(
            payload_fibras['summary']['destinos_mayor_uso'][0]['destino'],
            'DESTINO-GUI',
        )
        self.assertEqual(
            payload_fibras['summary']['destinos_mayor_uso'][0]['total'],
            1,
        )

        reservas = self.client.get(reverse('api_elementos_paginados'), {
            'ruta': 'TRONCAL-GUI',
            'tipo': 'MUFA',
        })
        self.assertEqual(reservas.status_code, 200)
        payload_reservas = reservas.json()
        self.assertEqual(payload_reservas['summary']['total'], 1)
        self.assertEqual(payload_reservas['summary']['tipos'], 1)
        self.assertEqual(payload_reservas['data'][0]['ruta_id'], self.troncal.pk)
        self.assertEqual(
            payload_reservas['summary']['tipos_detalle'][0]['tipo'],
            'MUFA',
        )

    def test_contexto_global_de_site_filtra_recursos_operativos(self):
        params = {'site': self.odf.hub_site, 'page_size': 25}
        endpoints = (
            'api_troncales_paginadas',
            'api_tramos_paginados',
            'api_fibras_paginadas',
            'api_elementos_paginados',
            'api_odfs_paginados',
            'api_puertos_paginados',
        )

        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                response = self.client.get(reverse(endpoint), params)
                self.assertEqual(response.status_code, 200)
                self.assertGreater(response.json()['pagination']['total'], 0)

    def test_base_incluye_contexto_global_de_site_removible(self):
        response = self.client.get(reverse('planta_externa'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'global-site-context')
        self.assertContains(response, 'global-site-context-clear')
        self.assertContains(response, 'global-site-context.js')

    def test_panel_contextual_de_troncal_entrega_capacidad_y_geometria(self):
        response = self.client.get(
            reverse('api_panel_troncal'),
            {'ruta_id': self.troncal.pk},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['ruta']['nombre'], 'TRONCAL-GUI')
        self.assertEqual(payload['fibras']['total'], 12)
        self.assertEqual(payload['fibras']['ocupadas'], 1)
        self.assertEqual(payload['reservas']['elementos'], 1)
        self.assertEqual(len(payload['coordenadas']), 1)
        self.assertEqual(payload['puntos'][0]['tipo'], 'MUFA')
        self.assertIn('ruta=TRONCAL-GUI', payload['ruta']['mapa_url'])

    def test_paginas_de_planta_externa_usan_la_plantilla_operativa(self):
        rutas = self.client.get(reverse('inventario_externo'))
        fibras = self.client.get(reverse('planta_externa'))
        self.assertContains(rutas, reverse('api_troncales_paginadas'))
        self.assertContains(rutas, reverse('api_tramos_paginados'))
        self.assertContains(rutas, 'Total troncales')
        self.assertContains(fibras, 'Total fibras')
        self.assertContains(fibras, 'Reservas')
        self.assertContains(rutas, 'network-inventory.css')
        self.assertContains(fibras, 'network-inventory.css')
        self.assertContains(rutas, 'leaflet-1.9.4.css')
        self.assertContains(rutas, 'leaflet-1.9.4.js')
        self.assertContains(fibras, reverse('api_panel_troncal'))
        self.assertContains(fibras, 'id="network-inspector"')
        self.assertContains(fibras, 'id="network-route-map"')
        for response in (rutas, fibras):
            self.assertContains(response, 'network-inspector-card--summary')
            self.assertContains(response, 'network-inspector-card--ranking')
            self.assertContains(response, 'network-inspector-card--detail')

        odfs = self.client.get(reverse('inventario_interno'))
        puertos = self.client.get(reverse('planta_interna'))
        for response in (odfs, puertos):
            self.assertContains(response, 'network-inspector-card--summary')
            self.assertContains(response, 'network-inspector-card--ranking')
            self.assertContains(response, 'network-inspector-card--detail')

        script_odf = (
            Path(__file__).resolve().parent / 'static' / 'js' / 'odf-inventory.js'
        ).read_text(encoding='utf-8')
        self.assertNotIn('!pendingOdfId && index === 0', script_odf)

        estilos = (
            Path(__file__).resolve().parent / 'static' / 'css' / 'network-inventory.css'
        ).read_text(encoding='utf-8')
        self.assertIn(
            '.network-panel .odf-kpis { grid-template-columns: repeat(6, minmax(0, 1fr)); }',
            estilos,
        )
        self.assertIn('--odf-line: var(--ports-line', estilos)
        self.assertIn('.network-map-empty', estilos)
        self.assertIn(
            '.network-route-metrics span {',
            estilos,
        )
        self.assertIn('background: var(--bg-surface-2);', estilos)

        exportacion = self.client.get(
            reverse('exportar_inventario', args=['troncales', 'xlsx']),
            {'q': 'TRONCAL-GUI'},
        )
        self.assertEqual(exportacion.status_code, 200)
        self.assertTrue(exportacion.content.startswith(b'PK'))

    def test_nuevo_tramo_se_registra_en_una_troncal_existente(self):
        response = self.client.post(
            reverse('api_create_tramo'),
            data=json.dumps({
                'ruta_id': self.troncal.pk,
                'tramo_secuencia': 2,
                'tipo_trazado': 'SOTERRADO',
                'estado': 'OPERATIVO',
                'distancia_km': 1.25,
                'reservas_m': 80,
                'origen': 'PUNTO-A',
                'destino': 'PUNTO-B',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(InventarioTramo.objects.filter(
            ruta=self.troncal,
            tramo_secuencia=2,
            distancia_m=1250,
        ).exists())

    def test_nuevo_tramo_rechaza_salto_en_la_secuencia(self):
        response = self.client.post(
            reverse('api_create_tramo'),
            data=json.dumps({
                'ruta_id': self.troncal.pk,
                'tramo_secuencia': 3,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('sin saltos', response.json()['message'])
        self.assertFalse(InventarioTramo.objects.filter(
            ruta=self.troncal,
            tramo_secuencia=3,
        ).exists())

    def test_pagina_odf_usa_consulta_paginada_y_exportacion_servidor(self):
        response = self.client.get(reverse('inventario_interno'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('api_odfs_paginados'))
        self.assertContains(response, reverse('exportar_inventario', args=['odfs', 'xlsx']))
        self.assertContains(response, 'odf-analysis-main')
        self.assertContains(response, 'odf-occupancy-ranking')
        self.assertContains(response, 'network-inspector-toggle')
        self.assertNotContains(response, 'odf-utilization-card')
        self.assertNotContains(response, 'odf_list')

        exportacion = self.client.get(
            reverse('exportar_inventario', args=['odfs', 'xlsx']),
            {'q': 'ODF-GUI'},
        )
        self.assertEqual(exportacion.status_code, 200)
        self.assertTrue(exportacion.content.startswith(b'PK'))

    def test_paginas_masivas_no_embeben_el_inventario_completo(self):
        interna = self.client.get(reverse('planta_interna'))
        externa = self.client.get(reverse('planta_externa'))
        self.assertEqual(interna.status_code, 200)
        self.assertEqual(externa.status_code, 200)
        self.assertNotContains(interna, 'puertos_json')
        self.assertNotContains(externa, 'fibras_json')
        self.assertContains(interna, reverse('api_puertos_paginados'))
        self.assertContains(externa, reverse('api_fibras_paginadas'))

    def test_mapa_muestra_ocupados_en_resumen_del_site(self):
        response = self.client.get(reverse('mapa_inventario'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'network-site-used')
        self.assertContains(response, '<span>Ocupados</span>', html=True)

    def test_busqueda_global_conecta_activo_con_ficha_360(self):
        search = self.client.get(reverse('busqueda_global'), {'q': 'TRONCAL-GUI'})
        self.assertEqual(search.status_code, 200)
        resultados = search.json()['data']
        troncal = next(item for item in resultados if item['type'] == 'troncal')
        detail = self.client.get(troncal['detail_url'])
        self.assertEqual(detail.status_code, 200)
        ficha = detail.json()['data']
        self.assertEqual(ficha['title'], 'TRONCAL-GUI')
        self.assertTrue(any(
            item['label'] == 'ODF de fibras (A)'
            for item in ficha['chain']
        ))

    def test_ficha_360_separa_etiquetas_y_normaliza_valores_sin_dato(self):
        puerto = self.puertos[0]
        puerto.bandeja = 'SIN_DATO'
        puerto.tipo_conector = 'Sin dato'
        puerto.observaciones = ''
        puerto.save(update_fields=['bandeja', 'tipo_conector', 'observaciones'])

        response = self.client.get(reverse('asset_360', args=['puerto', puerto.pk]))
        self.assertEqual(response.status_code, 200)
        ficha = response.json()['data']
        resumen = {item['label']: item['value'] for item in ficha['summary']}
        self.assertEqual(resumen['Bandeja'], '—')
        self.assertEqual(resumen['Conector'], '—')

        script = (
            Path(__file__).resolve().parent / 'static' / 'js' / 'global-search.js'
        ).read_text(encoding='utf-8')
        self.assertIn("renderItems(asset.chain, chain, 'asset-chain')", script)
        self.assertIn("`${groupClass}__label`", script)
        self.assertIn("drawer.dataset.assetType = asset.type || ''", script)
        self.assertIn("asset-section--quality", script)
        self.assertNotIn("`${itemClass}__label`", script)

    def test_exportacion_csv_se_genera_en_el_servidor(self):
        response = self.client.get(
            reverse('exportar_inventario', args=['puertos', 'csv']),
            {'q': 'ODF-GUI'},
        )
        self.assertEqual(response.status_code, 200)
        contenido = b''.join(response.streaming_content).decode('utf-8-sig')
        self.assertIn('Site,Sala,Rack,ODF', contenido)
        self.assertIn('ODF-GUI', contenido)

    def test_operacion_veex_no_aparece_si_el_modulo_visual_esta_desactivado(self):
        response = self.client.get(reverse('planta_interna'))
        self.assertNotContains(response, 'Mapa de alarmas')
        self.assertNotContains(response, 'Umbrales OTDR')

    def test_dashboard_inventario_muestra_ocupacion_y_capacidad(self):
        response = self.client.get(reverse('dashboard_inventario'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_odfs'], 1)
        self.assertEqual(response.context['total_salas'], 1)
        self.assertEqual(response.context['total_racks'], 1)
        self.assertEqual(response.context['total_fibras'], 1)
        self.assertEqual(response.context['fibras_sin_estado'], 0)
        self.assertEqual(response.context['ocupacion_fibras_pct'], 100)
        self.assertEqual(response.context['utilizacion_fibras_pct'], 100)
        self.assertTrue(response.context['alertas_inventario'])
        self.assertEqual(response.context['top_rutas_capacidad'][0]['nombre'], 'TRONCAL-GUI')
        self.assertEqual(
            response.context['top_rutas_capacidad'][0]['utilizacion_hilos'],
            8.3,
        )
        self.assertEqual(response.context['top_odfs_capacidad'][0]['odf'], 'ODF-GUI')
        self.assertContains(response, 'Dashboard de Inventario')
        self.assertContains(response, 'Uso de puertos ODF')
        self.assertContains(response, 'data-free-label="Libres"')
        self.assertContains(response, 'ODF con puertos libres')
        self.assertContains(response, 'ODF sin puertos libres')
        self.assertContains(response, 'por ciento de puertos libres')
        self.assertContains(response, 'Uso de fibras')
        self.assertContains(response, 'Mapa del inventario')
        self.assertContains(response, 'Reservas de cable')
        self.assertContains(response, 'Personalizar')
        self.assertContains(response, 'data-dashboard-widget="ports"')
        self.assertContains(response, 'data-dashboard-widget="fibers"')
        self.assertContains(
            response,
            f'href="{reverse("inventario_interno")}?capacidad=disponible"',
        )
        self.assertContains(
            response,
            f'href="{reverse("inventario_interno")}?capacidad=sin_disponibilidad"',
        )
        self.assertContains(
            response,
            f'href="{reverse("inventario_externo")}?tab=troncales&amp;capacidad=disponible"',
        )
        self.assertContains(
            response,
            f'href="{reverse("inventario_externo")}?tab=troncales&amp;capacidad=atencion"',
        )
        self.assertContains(
            response,
            f'href="{reverse("planta_externa")}?tab=reservas"',
        )
        self.assertContains(response, 'Guardar dise')
        self.assertNotContains(response, 'Actividad de cargas')
        self.assertNotContains(response, 'Últimas cargas')
        self.assertNotContains(response, 'Calidad ')

    def test_dashboard_inventario_filtra_por_site(self):
        response = self.client.get(
            reverse('dashboard_inventario'),
            {'site': self.odf.hub_site},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['site_seleccionado'], self.odf.hub_site)
        self.assertEqual(response.context['total_sites'], 1)
        self.assertEqual(response.context['total_odfs'], 1)
        self.assertEqual(response.context['total_rutas'], 1)
        for enlace in response.context['capacity_links'].values():
            self.assertIn(
                urlencode({'site': self.odf.hub_site}),
                enlace,
            )

    def test_filtros_de_inventario_comparten_patron_visual(self):
        casos = (
            (reverse('inventario_interno'), 'inventory-filter-grid--seven'),
            (reverse('planta_interna'), 'inventory-filter-grid--six'),
            (reverse('inventario_externo'), 'inventory-filter-grid--five'),
            (reverse('planta_externa'), 'inventory-filter-grid--four'),
        )

        for url, clase in casos:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'inventory-filters.css')
                self.assertContains(response, clase)

        respuesta_odf = self.client.get(reverse('inventario_interno'))
        self.assertContains(respuesta_odf, '<option value="25" selected>25</option>', html=True)


class SeguridadYRendimientoTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_superuser(
            'auditor-tecnico',
            'auditor@example.test',
            'clave-segura',
        )
        self.client.force_login(self.usuario)

    def test_dashboard_vacio_declara_ausencia_de_datos(self):
        response = self.client.get(reverse('dashboard_inventario'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['inventario_con_datos'])
        self.assertEqual(response.context['calidad_inventario'], 0)
        self.assertContains(response, 'Sin datos cargados')
        self.assertContains(response, 'Sin puertos registrados')
        self.assertContains(response, 'Sin fibras registradas')
        self.assertContains(response, 'Sin capacidad por ruta')
        self.assertContains(response, 'Sin capacidad por ODF')
        self.assertNotContains(response, 'Actividad de cargas')

    def test_dashboard_cuenta_fibras_globales_con_ruta_pendiente(self):
        InventarioFibra.objects.create(
            fibra_numero='F1',
            estado='DISPONIBLE',
            origen_estado='INFORMADO',
        )
        InventarioFibra.objects.create(
            fibra_numero='F2',
            estado='RESERVADO',
            origen_estado='INFORMADO',
        )

        response = self.client.get(reverse('dashboard_inventario'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_fibras'], 2)
        self.assertEqual(response.context['fibras_libres'], 1)
        self.assertEqual(response.context['fibras_reservadas'], 1)
        self.assertEqual(response.context['fibras_ocupadas'], 0)
        self.assertEqual(response.context['fibras_sin_estado'], 0)
        self.assertEqual(response.context['fibras_sin_cobertura'], 2)
        self.assertTrue(response.context['inventario_con_datos'])
        self.assertNotContains(response, 'Sin fibras registradas')

    def test_tarjeta_de_sites_abre_el_importador_guiado_sin_enlazar_el_endpoint_post(self):
        response = self.client.get(reverse('configuracion'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "openImportModal('sites_inventario'")
        self.assertNotContains(response, f'href="{reverse("importar_sites_csv")}"')

    def test_configuracion_muestra_rotulos_guiados_de_importacion(self):
        response = self.client.get(reverse('configuracion'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Cargar recorridos en el mapa (.csv)",
        )
        self.assertContains(
            response,
            "Cargar Inventario Técnico de Tramos (.csv)",
        )
        self.assertContains(
            response,
            "Cargar Fibras por Tramo (.csv)",
        )
        self.assertContains(response, "Archivo A")
        self.assertContains(response, "Archivo B")
        self.assertContains(
            response,
            "Importación guiada del inventario",
        )
        self.assertContains(
            response,
            "Cargar inventario global de fibras",
        )
        self.assertNotContains(response, "Fase 2: Inventario técnico por tramo")
        self.assertNotContains(response, "Fase 3: Fibras por tramo")

    def test_formulario_alterno_mantiene_ayuda_avanzada_secundaria(self):
        html_tramos = render_to_string(
            'configuracion/formulario_csv.html',
            {
                'tipo_csv': 'tramos_inventario',
                'nombre_amigable': 'Cargar Inventario Técnico de Tramos',
            },
        )
        html_fibras = render_to_string(
            'configuracion/formulario_csv.html',
            {
                'tipo_csv': 'fibras_inventario',
                'nombre_amigable': 'Cargar Fibras por Tramo',
            },
        )

        self.assertIn('Requisitos del CSV: Inventario Técnico', html_tramos)
        self.assertIn(
            'Opciones avanzadas para rutas con varios tramos',
            html_tramos,
        )
        self.assertIn(
            'Descargar plantilla opcional para varios tramos',
            html_tramos,
        )
        self.assertIn(
            'Requisitos del CSV: inventario de fibras ópticas (Archivo B)',
            html_fibras,
        )
        self.assertIn(
            'Opciones avanzadas para fibras en varios tramos',
            html_fibras,
        )

    def test_capacidad_odf_no_se_sobreasigna_desde_la_gui(self):
        _, _, _, odf = crear_jerarquia_odf(nombre='ODF-CAPACIDAD', capacidad=1)
        DetallePuertoODF.objects.create(
            odf_obj=odf,
            puerto_odf='1',
            estado_puerto='LIBRE',
        )

        response = self.client.post(
            reverse('api_create_puerto'),
            data=json.dumps({'odf_nombre': odf.odf, 'puerto_odf': '2'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(DetallePuertoODF.objects.filter(odf_obj=odf).count(), 1)

    def test_capacidad_troncal_no_se_sobreasigna_desde_la_gui(self):
        ruta = Ruta.objects.create(nombre='RUTA-CAPACIDAD')
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            capacidad='1 Hilo',
        )
        InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero='1',
            estado='DISPONIBLE',
            origen_estado='INFORMADO',
        )

        response = self.client.post(
            reverse('api_create_fibra'),
            data=json.dumps({
                'ruta_nombre': ruta.nombre,
                'fibra_numero': '2',
                'estado': 'DISPONIBLE',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(InventarioFibra.objects.filter(ruta=ruta).count(), 1)

    def test_importacion_reservas_omite_coordenadas_invalidas_sin_crear_cero_cero(self):
        ruta = Ruta.objects.create(nombre='RUTA-RESERVAS-VALIDADAS')
        archivo = SimpleUploadedFile(
            'reservas.csv',
            (
                'Nombre,Tipo,Reserva (m),Latitud,Longitud\n'
                'RESERVA-VALIDA,MUFA,10,-24.2724030,-69.0920510\n'
                'RESERVA-SIN-LATITUD,MUFA,10,,-69.0920510\n'
            ).encode(),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('api_import_reservas'),
            {'archivo': archivo, 'ruta_nombre': ruta.nombre},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        self.assertTrue(response.json()['warning'])
        self.assertEqual(response.json()['rechazadas'], 1)
        self.assertEqual(Reserva.objects.filter(ruta=ruta).count(), 1)
        self.assertFalse(Reserva.objects.filter(ruta=ruta, latitud=0, longitud=0).exists())

    def test_tablas_operativas_declaran_encabezados_y_el_mapa_tiene_titulo(self):
        odf = self.client.get(reverse('inventario_interno'))
        mapa = self.client.get(reverse('mapa_inventario'))

        self.assertContains(odf, 'scope="col"')
        self.assertContains(mapa, '<h1 class="sr-only">Mapa de red del inventario</h1>', html=True)
        self.assertContains(mapa, 'id="map-layers-toggle"')
        self.assertContains(mapa, 'id="map-state"')
        self.assertContains(mapa, 'css/mapa-red-v2.css')
        self.assertContains(mapa, 'role="combobox"')
        self.assertContains(mapa, 'id="route-search-results"')
        self.assertNotContains(mapa, 'routes-datalist')
        self.assertContains(mapa, 'aria-label="Detalle de troncal"')
        self.assertContains(mapa, 'id="network-nav-panel"')
        self.assertContains(mapa, 'data-open-asset-360', count=2)
        self.assertContains(mapa, reverse('api_mapa_site_navigation', args=[0]))
        self.assertContains(mapa, reverse('api_puertos_paginados'))
        self.assertContains(mapa, 'Mostrar elementos externos')

        script = (
            Path(__file__).resolve().parent / 'static' / 'js' / 'mapa_inventario.js'
        ).read_text(encoding='utf-8')
        self.assertIn('openAsset360(link.href)', script)
        self.assertGreaterEqual(script.count('data-open-asset-360'), 2)
        self.assertIn('NETWORK_MARKER_ICONS', script)
        self.assertIn('findRouteMatches', script)
        self.assertIn('.slice(0, 8)', script)
        self.assertIn('aria-activedescendant', script)
        self.assertIn('selectRoute(item.ruta)', script)
        self.assertIn('preferCanvas: true', script)
        self.assertIn('function syncTramosVisibility()', script)
        self.assertIn('state.routeLayerEntries.forEach', script)
        self.assertEqual(script.count('renderTramos();'), 1)
        self.assertIn('updateRouteSelectionOverlay', script)
        self.assertIn("const selectionHaloColor", script)
        self.assertIn("color: polyline._routeBaseColor", script)
        self.assertIn('route-selection-label', script)
        self.assertIn("nearestDistance <= 35", script)
        self.assertIn("classList.add('is-route-endpoint')", script)
        self.assertIn("state.map.on('baselayerchange'", script)
        self.assertIn('custom-network-marker-icon', script)
        self.assertNotIn('/static/img/iconos/${tipo}-icono.png', script)

        styles = (
            Path(__file__).resolve().parent / 'static' / 'css' / 'mapa-red-v2.css'
        ).read_text(encoding='utf-8')
        self.assertIn('#map[data-basemap-theme="dark"] .network-marker--asset', styles)
        self.assertIn('.network-marker.is-selected', styles)
        self.assertIn('.route-search-results', styles)
        self.assertIn('[data-theme="dark"] .route-search-results', styles)
        self.assertIn('.route-selection-endpoint', styles)
        self.assertIn('.network-marker--site.is-route-endpoint', styles)
        self.assertIn('#map[data-basemap-theme="dark"] .route-selection-label', styles)

    def test_navegacion_del_site_no_genera_consultas_por_cada_odf(self):
        site, _, rack, _ = crear_jerarquia_odf(nombre='ODF-MAPA-0', capacidad=24)
        for indice in range(1, 9):
            InventarioODF.objects.create(
                rack_obj=rack,
                odf=f'ODF-MAPA-{indice}',
                capacidad_puertos=24,
                puertos_libres=24,
            )

        cache.clear()
        with CaptureQueriesContext(connection) as consultas:
            response = self.client.get(
                reverse('api_mapa_site_navigation', args=[site.pk]),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['odfs']), 9)
        self.assertLessEqual(len(consultas), 15)

    def _crear_rutas(self, cantidad=8):
        for indice in range(cantidad):
            ruta = Ruta.objects.create(nombre=f'RUTA-PERF-{indice}', distancia_m=1000)
            tramo = InventarioTramo.objects.create(
                ruta=ruta,
                tramo_secuencia=1,
                tipo_trazado='AEREO',
                hub_site=f'SITE-{indice}',
            )
            CoordenadaRuta.objects.create(
                ruta=ruta,
                tramo=tramo,
                orden=1,
                latitud='-12.0000000',
                longitud='-77.0000000',
                tipo_trazado='AEREO',
                inicio_segmento=True,
            )
            InventarioFibra.objects.create(
                ruta=ruta,
                fibra_numero='F01',
                estado='DISPONIBLE',
                origen_estado='INFORMADO',
            )

    @tag('query_budget_historico')
    def test_consultas_inventario_externo_no_crecen_por_ruta(self):
        self._crear_rutas()
        cache.clear()
        with CaptureQueriesContext(connection) as consultas:
            response = self.client.get(reverse('inventario_externo'))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(consultas), 15)

    @tag('query_budget_historico')
    def test_consultas_json_mapa_no_crecen_por_tramo(self):
        self._crear_rutas()
        cache.clear()
        with CaptureQueriesContext(connection) as consultas:
            response = self.client.get(reverse('api_datos_inventario'))
        self.assertEqual(response.status_code, 200)
        # El endpoint mantiene un presupuesto constante; la consulta adicional
        # corresponde al registro de actividad de la sesión autenticada.
        self.assertLessEqual(len(consultas), 16)

    @tag('query_budget_historico')
    def test_consultas_dashboard_no_crecen_por_site(self):
        self._crear_rutas()
        for indice in range(8):
            HubSite.objects.create(
                nombre=f'SITE-{indice}',
                latitud='-12.0000000',
                longitud='-77.0000000',
            )
        cache.clear()
        with CaptureQueriesContext(connection) as consultas:
            response = self.client.get(reverse('dashboard_inventario'))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(consultas), 22)

    @override_settings(FIBERGENIUS_ACTIVITY_UPDATE_SECONDS=180)
    def test_actividad_de_usuario_se_limita_por_cache(self):
        cache.clear()
        request = RequestFactory().get('/inventario/')
        request.user = self.usuario
        middleware = ActiveUserMiddleware(lambda _request: None)
        middleware(request)
        with self.assertNumQueries(0):
            middleware(request)

    @override_settings(FIBERGENIUS_MAX_UPLOAD_BYTES=16)
    def test_carga_rechaza_archivo_demasiado_grande(self):
        archivo = SimpleUploadedFile('rutas.csv', b'x' * 32, content_type='text/csv')
        response = self.client.post(
            reverse('cargar_csv', args=['ruta_otu']),
            {'csv_file': archivo},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(LoteImportacion.objects.exists())

    @override_settings(
        FIBERGENIUS_MAX_UPLOAD_BYTES=1024 * 1024,
        FIBERGENIUS_MAX_ZIP_UNCOMPRESSED_BYTES=16,
    )
    def test_carga_rechaza_zip_contenido_descomprimido_excesivo(self):
        contenido = io.BytesIO()
        with zipfile.ZipFile(contenido, 'w', zipfile.ZIP_DEFLATED) as paquete:
            paquete.writestr('RUTA.csv', 'x' * 32)
        archivo = SimpleUploadedFile(
            'rutas.zip',
            contenido.getvalue(),
            content_type='application/zip',
        )
        response = self.client.post(
            reverse('cargar_csv', args=['coordenadas_rutas']),
            {'csv_file': archivo},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(LoteImportacion.objects.exists())

    def test_comando_registra_baseline_como_reconstruido(self):
        Ruta.objects.create(nombre='RUTA-BASELINE')
        directorio = Path(tempfile.gettempdir()) / f'fibergenius-{uuid.uuid4().hex}'
        directorio.mkdir(parents=True)
        try:
            manifest = Path(directorio) / 'manifest.json'
            manifest.write_text('{"version": 5}', encoding='utf-8')
            call_command(
                'registrar_baseline_inventario',
                manifest=str(manifest),
                fecha_origen='2026-07-01T10:00:00-05:00',
                verbosity=0,
            )
        finally:
            shutil.rmtree(directorio, ignore_errors=True)
        lote = LoteImportacion.objects.get(origen_registro='BASELINE_RECONSTRUIDO')
        self.assertEqual(lote.tipo, 'BASELINE_INVENTARIO_V5')
        self.assertEqual(lote.metadatos_origen['conteos']['rutas'], 1)
        self.assertIn('no representa una importacion', lote.metadatos_origen['nota'])
