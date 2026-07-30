import hashlib
import hmac
import gzip
import io
import json
import tempfile
import time
import zipfile
from pathlib import Path

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db import connection
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from .distancias import (
    FUENTE_GEOMETRIA,
    FUENTE_INVENTARIO_TRAMOS,
    FUENTE_INVENTARIO_TRONCAL,
    resolver_distancia_ruta,
    resolver_distancia_tramo,
)
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
    LoteImportacion,
    Medicion,
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
    _procesar_puertos_odf_inventario,
    _procesar_reservas,
    _procesar_ruta_otu,
    _procesar_troncales_inventario,
    _procesar_tramos_inventario,
)
from .middleware import ActiveUserMiddleware


def crear_jerarquia_odf(nombre='ODF-01', capacidad=24):
    hub = HubSite.objects.create(nombre=f'HUB-{nombre}')
    sala = SalaTecnica.objects.create(hub_site=hub, nombre='SALA-01')
    rack = RackFisico.objects.create(sala=sala, nombre='RACK-01')
    odf = InventarioODF.objects.create(
        rack_obj=rack,
        hub_site=hub.nombre,
        sala=sala.nombre,
        rack=rack.nombre,
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
                hub_site=odf.hub_site,
                sala=odf.sala,
                rack=odf.rack,
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
                [DetallePuertoODF(odf='HUERFANO', puerto_odf='1')]
            )

    def test_puerto_duplicado_es_rechazado(self):
        _, _, _, odf = crear_jerarquia_odf()
        DetallePuertoODF.objects.create(odf_obj=odf, puerto_odf='1')
        with self.assertRaises(IntegrityError), transaction.atomic():
            DetallePuertoODF.objects.bulk_create(
                [DetallePuertoODF(odf_obj=odf, odf=odf.odf, puerto_odf='1')]
            )

    def test_renombrar_odf_sincroniza_el_texto_legacy(self):
        _, _, _, odf = crear_jerarquia_odf(nombre='ODF-OLD')
        puerto = DetallePuertoODF.objects.create(odf_obj=odf, puerto_odf='1')
        odf.odf = 'ODF-NEW'
        odf.save()
        puerto.refresh_from_db()
        self.assertEqual(puerto.odf, 'ODF-NEW')

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
        self.assertEqual(puerto.estado_puerto, 'Reservado')
        self.assertEqual(odf.puertos_reservados, 1)
        self.assertEqual(odf.puertos_ocupados, 0)
        self.assertEqual(odf.puertos_libres, 3)

    def test_importacion_infiere_reserva_desde_el_destino_legado(self):
        hub, _, _, odf = crear_jerarquia_odf(nombre='ODF-RESERVA-LEGADA', capacidad=4)
        archivo = SimpleUploadedFile(
            'puertos.csv',
            (
                'hub_site,odf,puerto_odf,estado_puerto,destino\n'
                f'{hub.nombre},{odf.odf},1,Ocupado,SW-01 | Reservado proyecto CCTV\n'
            ).encode(),
            content_type='text/csv',
        )

        _procesar_puertos_odf_inventario(archivo)

        puerto = DetallePuertoODF.objects.get(odf_obj=odf, puerto_odf='1')
        odf.refresh_from_db()
        self.assertEqual(puerto.estado_puerto, 'Reservado')
        self.assertEqual(odf.puertos_reservados, 1)
        self.assertEqual(odf.puertos_ocupados, 0)
        self.assertEqual(odf.puertos_libres, 3)


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

    def test_terminacion_exige_odf_del_extremo_y_sincroniza_puerto(self):
        _, _, _, odf_a = crear_jerarquia_odf(nombre='ODF-EXT-A')
        _, _, _, odf_b = crear_jerarquia_odf(nombre='ODF-EXT-B')
        puerto_a = DetallePuertoODF.objects.create(odf_obj=odf_a, puerto_odf='1')
        puerto_b = DetallePuertoODF.objects.create(odf_obj=odf_b, puerto_odf='1')
        ruta = Ruta.objects.create(
            nombre='TRONCAL-TERMINACION',
            odf_origen=odf_a,
            odf_destino=odf_b,
        )
        fibra = InventarioFibra.objects.create(ruta=ruta, fibra_numero='F1')

        with self.assertRaises(ValidationError):
            TerminacionFibra(
                fibra=fibra,
                extremo='A',
                puerto_odf=puerto_b,
            ).full_clean()

        terminacion = TerminacionFibra.objects.create(
            fibra=fibra,
            extremo='A',
            puerto_odf=puerto_a,
        )
        puerto_a.refresh_from_db()
        self.assertEqual(puerto_a.estado_puerto, 'Ocupado')
        self.assertEqual(puerto_a.fibra, 'F1')
        terminacion.delete()
        puerto_a.refresh_from_db()
        self.assertEqual(puerto_a.estado_puerto, 'Libre')


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

    def test_zip_segmentado_vincula_coordenadas_y_conserva_distancias(self):
        ruta = Ruta.objects.create(nombre='RUTA-ZIP')
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            codigo_tramo='T001',
            tipo_trazado='AEREO',
        )
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=2,
            codigo_tramo='T002',
            tipo_trazado='SOTERRADO',
        )
        contenido = (
            'Latitude,Longitude,tipo_trazado,new_seg\n'
            '-23.0000000,-69.0000000,AEREO,false\n'
            '-23.0010000,-69.0010000,AEREO,false\n'
            '-23.0020000,-69.0020000,SOTERRADO,true\n'
            '-23.0030000,-69.0030000,SOTERRADO,false\n'
        ).encode()
        archivo = io.BytesIO()
        with zipfile.ZipFile(archivo, 'w') as paquete:
            paquete.writestr('RUTA-ZIP.csv', contenido)
        archivo.seek(0)

        _procesar_coordenadas_inventario_zip(archivo)
        self.assertEqual(ruta.tramos_inventario.count(), 2)
        self.assertFalse(ruta.coordenadas.filter(tramo__isnull=True).exists())

    def test_metadata_total_no_sobrescribe_distancias_de_segmento(self):
        ruta = Ruta.objects.create(nombre='RUTA-METRICAS')
        InventarioTramo.objects.create(ruta=ruta, tramo_secuencia=1, distancia_m=100)
        InventarioTramo.objects.create(ruta=ruta, tramo_secuencia=2, distancia_m=200)
        archivo = io.BytesIO(
            b'ruta,distancia,mufas,splitters,reservas_m,hilos_ocupados,hilos_libres\n'
            b'RUTA-METRICAS,300,2,1,50,3,9\n'
        )
        _procesar_tramos_inventario(archivo)
        ruta.refresh_from_db()
        tramos = list(ruta.tramos_inventario.order_by('tramo_secuencia'))
        self.assertIsNone(ruta.distancia_m)
        self.assertEqual(ruta.distancia_documentada_m, 300)
        self.assertEqual(ruta.mufas, 2)
        self.assertEqual([tramo.distancia_m for tramo in tramos], [100, 200])
        self.assertEqual([tramo.mufas for tramo in tramos], [0, 0])

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

    def test_actualizacion_de_puerto_acepta_estado_reservado(self):
        permiso = Permission.objects.get(codename='change_detallepuertoodf')
        self.usuario.user_permissions.add(permiso)
        _, _, _, odf = crear_jerarquia_odf(capacidad=2)
        puerto = DetallePuertoODF.objects.create(odf_obj=odf, puerto_odf='1')

        response = self.client.post(
            reverse('api_update_puerto'),
            data=json.dumps({
                'id': puerto.pk,
                'puerto_odf': '1',
                'estado_puerto': 'Reservado',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        puerto.refresh_from_db()
        odf.refresh_from_db()
        self.assertEqual(puerto.estado_puerto, 'Reservado')
        self.assertEqual(odf.puertos_reservados, 1)
        self.assertEqual(odf.puertos_libres, 1)

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
                estado_puerto=(
                    'Ocupado' if numero == 1 else ('Reservado' if numero == 3 else 'Libre')
                ),
                fibra=f'F{numero}',
                destino=f'DESTINO-{numero}',
            )
            for numero in range(1, 4)
        ]
        self.odf.actualizar_contadores()
        self.troncal = Ruta.objects.create(nombre='TRONCAL-GUI', odf_origen=self.odf)
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
        )
        self.fibra = InventarioFibra.objects.create(
            ruta=self.troncal,
            fibra_numero='F1',
            estado='Ocupado',
            destino='DESTINO-GUI',
        )
        self.elemento = Reserva.objects.create(
            ruta=self.troncal,
            nombre='MUFA-GUI',
            tipo='MUFA',
            latitud=-23,
            longitud=-69,
        )

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
            'estado': 'Reservado',
            'page_size': 10,
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['pagination']['total'], 1)
        self.assertEqual(payload['data'][0]['estado'], 'Reservado')
        self.assertEqual(payload['summary']['reservados'], 1)

    def test_puertos_del_mapa_se_filtran_por_odf_exacto_y_admiten_48_por_pagina(self):
        otro_odf = InventarioODF.objects.create(
            rack_obj=self.odf.rack_obj,
            hub_site=self.odf.hub_site,
            sala=self.odf.sala,
            rack=self.odf.rack,
            odf='ODF-GUI-OTRO',
            capacidad_puertos=1,
            puertos_libres=1,
        )
        DetallePuertoODF.objects.create(
            odf_obj=otro_odf,
            puerto_odf='1',
            estado_puerto='Libre',
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
        self.assertEqual(payload['summary']['total'], 1)
        self.assertEqual(payload['summary']['tramos'], 1)
        self.assertEqual(payload['summary']['fibras'], 1)
        self.assertEqual(payload['summary']['top_ocupacion'][0]['nombre'], 'TRONCAL-GUI')
        self.assertEqual(payload['summary']['top_ocupacion'][0]['utilizacion'], 100)

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
        ruta_disponible = Ruta.objects.create(
            nombre='TRONCAL-DISPONIBLE',
            hilos_ocupados_declarados=8,
            hilos_libres_declarados=2,
        )
        InventarioTramo.objects.create(
            ruta=ruta_disponible,
            tramo_secuencia=1,
        )
        ruta_atencion = Ruta.objects.create(
            nombre='TRONCAL-ATENCION',
            hilos_ocupados_declarados=9,
            hilos_libres_declarados=1,
        )
        InventarioTramo.objects.create(
            ruta=ruta_atencion,
            tramo_secuencia=1,
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
            'estado': 'Ocupado',
        })
        self.assertEqual(fibras.status_code, 200)
        payload_fibras = fibras.json()
        self.assertEqual(payload_fibras['summary']['ocupadas'], 1)
        self.assertEqual(payload_fibras['summary']['troncales'], 1)
        self.assertEqual(payload_fibras['data'][0]['ruta_id'], self.troncal.pk)
        self.assertEqual(
            payload_fibras['summary']['top_troncales'][0]['nombre'],
            'TRONCAL-GUI',
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

    def test_panel_contextual_de_troncal_entrega_capacidad_y_geometria(self):
        response = self.client.get(
            reverse('api_panel_troncal'),
            {'ruta_id': self.troncal.pk},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['ruta']['nombre'], 'TRONCAL-GUI')
        self.assertEqual(payload['fibras']['total'], 1)
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
            '.network-panel .odf-kpis { grid-template-columns: repeat(5, minmax(0, 1fr)); }',
            estilos,
        )
        self.assertIn('--odf-line: var(--ports-line', estilos)
        self.assertIn('.network-map-empty', estilos)

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
            distancia_documentada_m=1250,
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

    def test_busqueda_global_conecta_activo_con_ficha_360(self):
        search = self.client.get(reverse('busqueda_global'), {'q': 'TRONCAL-GUI'})
        self.assertEqual(search.status_code, 200)
        resultados = search.json()['data']
        troncal = next(item for item in resultados if item['type'] == 'troncal')
        detail = self.client.get(troncal['detail_url'])
        self.assertEqual(detail.status_code, 200)
        ficha = detail.json()['data']
        self.assertEqual(ficha['title'], 'TRONCAL-GUI')
        self.assertTrue(any(item['label'] == 'ODF extremo A' for item in ficha['chain']))

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
        self.assertEqual(response.context['ocupacion_fibras_pct'], 100)
        self.assertEqual(response.context['utilizacion_fibras_pct'], 100)
        self.assertTrue(response.context['alertas_inventario'])
        self.assertEqual(response.context['top_rutas_capacidad'][0]['nombre'], 'TRONCAL-GUI')
        self.assertEqual(response.context['top_odfs_capacidad'][0]['odf'], 'ODF-GUI')
        self.assertContains(response, 'Dashboard de Inventario')
        self.assertContains(response, 'Uso de puertos ODF')
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


class SegmentacionTramosFase1Tests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            'segmentador',
            password='clave-segura',
        )
        self.usuario.user_permissions.add(
            Permission.objects.get(codename='view_ruta')
        )
        self.client.force_login(self.usuario)
        self.ruta = Ruta.objects.create(
            nombre='RUTA-FASE-1',
            distancia_m=123,
        )
        self.tramo_inicial = InventarioTramo.objects.create(
            ruta=self.ruta,
            tramo_secuencia=1,
            codigo_tramo='T001',
            estado_calidad=InventarioTramo.CALIDAD_LEGACY,
            capacidad='24 Hilos',
        )
        self.reserva = Reserva.objects.create(
            ruta=self.ruta,
            tramo=self.tramo_inicial,
            nombre='RESERVA-FASE-1',
            latitud='-12.0000000',
            longitud='-77.0000000',
        )

    def _geometria_dos_tramos(self):
        contenido = (
            'ruta,latitude,longitude,tramo_secuencia,codigo_tramo,tipo_trazado\n'
            'RUTA-FASE-1,-12.0000,-77.0000,1,T001,AEREO\n'
            'RUTA-FASE-1,-12.0010,-77.0010,1,T001,AEREO\n'
            'RUTA-FASE-1,-12.0020,-77.0020,2,T002,SOTERRADO\n'
            'RUTA-FASE-1,-12.0030,-77.0030,2,T002,SOTERRADO\n'
        )
        return SimpleUploadedFile(
            'tramos.csv',
            contenido.encode('utf-8'),
            content_type='text/csv',
        )

    def _cargar_inventario_dos_tramos(self):
        archivo = SimpleUploadedFile(
            'inventario-tramos.csv',
            (
                'ruta,tramo_secuencia,codigo_tramo,origen,destino,'
                'tipo_trazado,estado_tramo,distancia_documentada_m\n'
                'RUTA-FASE-1,1,T001,HUB-A,CAMARA-1,AEREO,ACTIVO,100\n'
                'RUTA-FASE-1,2,T002,CAMARA-1,HUB-B,SOTERRADO,ACTIVO,120\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )
        return _procesar_tramos_inventario(archivo)

    def test_recarga_segmentada_es_idempotente_y_conserva_relaciones(self):
        self._cargar_inventario_dos_tramos()
        _procesar_coordenadas_csv(self._geometria_dos_tramos())
        ids_primera = list(
            InventarioTramo.objects.filter(
                ruta=self.ruta,
                vigente=True,
            ).order_by('tramo_secuencia').values_list('id', flat=True)
        )
        coordenadas_primera = list(
            CoordenadaRuta.objects.filter(
                ruta=self.ruta
            ).order_by('orden').values_list('id', flat=True)
        )

        _procesar_coordenadas_csv(self._geometria_dos_tramos())

        ids_segunda = list(
            InventarioTramo.objects.filter(
                ruta=self.ruta,
                vigente=True,
            ).order_by('tramo_secuencia').values_list('id', flat=True)
        )
        coordenadas_segunda = list(
            CoordenadaRuta.objects.filter(
                ruta=self.ruta
            ).order_by('orden').values_list('id', flat=True)
        )
        self.reserva.refresh_from_db()
        self.assertEqual(ids_primera, ids_segunda)
        self.assertEqual(coordenadas_primera, coordenadas_segunda)
        self.assertEqual(self.reserva.tramo_id, self.tramo_inicial.pk)
        self.assertEqual(
            list(
                InventarioTramo.objects.filter(
                    ruta=self.ruta,
                    vigente=True,
                ).values_list('codigo_tramo', flat=True)
            ),
            ['T001', 'T002'],
        )

    def test_rechaza_tramos_intercalados_o_fuera_de_orden(self):
        self._cargar_inventario_dos_tramos()
        archivo = SimpleUploadedFile(
            'tramos-desordenados.csv',
            (
                'ruta,latitude,longitude,tramo_secuencia,codigo_tramo\n'
                'RUTA-FASE-1,-12.0000,-77.0000,1,T001\n'
                'RUTA-FASE-1,-12.0010,-77.0010,2,T002\n'
                'RUTA-FASE-1,-12.0020,-77.0020,1,T001\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        with self.assertRaisesRegex(ValueError, 'bloque continuo'):
            _procesar_coordenadas_csv(archivo)

    def test_rechaza_codigo_repetido_en_dos_secuencias(self):
        self._cargar_inventario_dos_tramos()
        archivo = SimpleUploadedFile(
            'tramos-codigo-repetido.csv',
            (
                'ruta,latitude,longitude,tramo_secuencia,codigo_tramo\n'
                'RUTA-FASE-1,-12.0000,-77.0000,1,T001\n'
                'RUTA-FASE-1,-12.0010,-77.0010,1,T001\n'
                'RUTA-FASE-1,-12.0020,-77.0020,2,T001\n'
                'RUTA-FASE-1,-12.0030,-77.0030,2,T001\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        with self.assertRaisesRegex(ValueError, 'pertenece a la secuencia'):
            _procesar_coordenadas_csv(archivo)

    def test_panel_de_tramo_dibuja_solo_el_segmento_seleccionado(self):
        self._cargar_inventario_dos_tramos()
        _procesar_coordenadas_csv(self._geometria_dos_tramos())
        tramo = InventarioTramo.objects.get(
            ruta=self.ruta,
            codigo_tramo='T002',
        )

        response = self.client.get(
            reverse('api_panel_tramo'),
            {'tramo_id': tramo.pk},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['asset_kind'], 'tramo')
        self.assertEqual(payload['ruta']['codigo'], 'T002')
        self.assertEqual(payload['ruta']['troncal'], 'RUTA-FASE-1')
        self.assertEqual(len(payload['coordenadas']), 2)
        self.assertNotIn('calidad', payload['ruta'])

    def test_metadatos_separan_distancia_documentada_y_geometrica(self):
        InventarioTramo.objects.create(
            ruta=self.ruta,
            tramo_secuencia=2,
            codigo_tramo='T002',
        )
        archivo = SimpleUploadedFile(
            'detalle.csv',
            (
                'ruta,distancia,capacidad,estado\n'
                'RUTA-FASE-1,500,24,OPERATIVO\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        _procesar_tramos_inventario(archivo)

        self.ruta.refresh_from_db()
        self.assertEqual(self.ruta.distancia_m, 123)
        self.assertEqual(self.ruta.distancia_documentada_m, 500)
        self.assertEqual(self.ruta.capacidad_hilos_declarada, 24)
        self.assertEqual(
            InventarioTramo.objects.filter(
                ruta=self.ruta,
                vigente=True,
                capacidad='24 Hilos',
            ).count(),
            1,
        )

    def test_api_tramos_expone_codigo_y_capacidad_sin_calidad_interna(self):
        self.ruta.capacidad_hilos_declarada = 24
        self.ruta.save(update_fields=['capacidad_hilos_declarada'])

        response = self.client.get(
            reverse('api_tramos_paginados'),
            {'ruta': self.ruta.nombre},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['data'][0]['codigo'], 'T001')
        self.assertEqual(payload['data'][0]['capacidad'], 24)
        self.assertNotIn('calidad_label', payload['data'][0])
        self.assertIn('/api/inventario/360/tramo/', payload['data'][0]['detail_url'])
        self.assertEqual(payload['summary']['troncales'], 1)
        self.assertEqual(payload['summary']['top_troncales'][0]['tramos'], 1)

    def test_gui_tramos_oculta_calidad_y_muestra_resumen_operativo(self):
        response = self.client.get(
            reverse('inventario_externo'),
            {'tab': 'tramos'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="tramos-stat-routes"')
        self.assertNotContains(response, 'id="tramos-stat-review"')
        self.assertNotContains(response, '<th scope="col">Calidad</th>')
        self.assertNotContains(response, 'Segmentos pendientes de homologación')

    def test_plantillas_separan_troncales_tramos_y_geometria(self):
        carpeta = (
            Path(__file__).resolve().parent
            / 'static'
            / 'ejemplos'
        )
        for nombre, procesador in (
            ('troncales_inventario_ejemplo.csv', _procesar_troncales_inventario),
            ('tramos_inventario_ejemplo.csv', _procesar_tramos_inventario),
        ):
            ruta_plantilla = carpeta / nombre
            procesador(SimpleUploadedFile(
                ruta_plantilla.name,
                ruta_plantilla.read_bytes(),
                content_type='text/csv',
            ))

        ruta_demo = Ruta.objects.get(nombre='RUTA-DEMO-01')
        ids_antes = list(
            ruta_demo.tramos_inventario.order_by('tramo_secuencia')
            .values_list('pk', flat=True)
        )
        ruta_geometria = carpeta / 'coordenadas_unificadas_ejemplo.csv'
        _procesar_coordenadas_csv(SimpleUploadedFile(
            ruta_geometria.name,
            ruta_geometria.read_bytes(),
            content_type='text/csv',
        ))
        self.assertEqual(
            list(
                ruta_demo.tramos_inventario.filter(vigente=True)
                .order_by('tramo_secuencia')
                .values_list('codigo_tramo', flat=True)
            ),
            ['T001', 'T002'],
        )
        self.assertEqual(
            ids_antes,
            list(
                ruta_demo.tramos_inventario.order_by('tramo_secuencia')
                .values_list('pk', flat=True)
            ),
        )
        self.assertEqual(ruta_demo.coordenadas.count(), 4)

    def test_troncal_y_tramos_se_cargan_sin_coordenadas(self):
        troncal = SimpleUploadedFile(
            'troncal.csv',
            (
                'ruta,capacidad,hilos_ocupados,hilos_libres,'
                'hilos_reservados,origen,destino\n'
                'RUTA-SIN-MAPA,12,4,6,2,HUB-A,HUB-B\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )
        _procesar_troncales_inventario(troncal)
        ruta = Ruta.objects.get(nombre='RUTA-SIN-MAPA')
        self.assertEqual(ruta.capacidad_hilos_declarada, 12)
        self.assertFalse(ruta.coordenadas.exists())

        tramos = SimpleUploadedFile(
            'tramos.csv',
            (
                'ruta,tramo_secuencia,codigo_tramo,origen,destino\n'
                'RUTA-SIN-MAPA,1,T001,HUB-A,CAMARA-1\n'
                'RUTA-SIN-MAPA,2,T002,CAMARA-1,HUB-B\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )
        _procesar_tramos_inventario(tramos)
        self.assertEqual(ruta.tramos_inventario.count(), 2)
        self.assertFalse(ruta.coordenadas.exists())

    def test_geometria_no_crea_inventario_faltante(self):
        archivo = SimpleUploadedFile(
            'geometria-sin-inventario.csv',
            (
                'ruta,codigo_tramo,latitude,longitude\n'
                'RUTA-FANTASMA,T001,-12.0,-77.0\n'
                'RUTA-FANTASMA,T001,-12.1,-77.1\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )
        with self.assertRaisesRegex(ValueError, 'no existe en el inventario'):
            _procesar_coordenadas_csv(archivo)
        self.assertFalse(Ruta.objects.filter(nombre='RUTA-FANTASMA').exists())

    def test_ruta_rechaza_conteos_superiores_a_capacidad(self):
        self.ruta.capacidad_hilos_declarada = 12
        self.ruta.hilos_ocupados_declarados = 8
        self.ruta.hilos_libres_declarados = 5
        with self.assertRaises(ValidationError):
            self.ruta.full_clean()

    def test_distancia_ruta_prioriza_inventario_troncal(self):
        self.ruta.distancia_documentada_m = 500
        self.ruta.save()
        self.tramo_inicial.distancia_documentada_m = 100
        self.tramo_inicial.save()

        distancia = resolver_distancia_ruta(
            self.ruta,
            [self.tramo_inicial],
        )

        self.assertEqual(distancia.valor_m, 500)
        self.assertEqual(distancia.fuente, FUENTE_INVENTARIO_TRONCAL)

    def test_distancia_ruta_suma_tramos_solo_si_todos_tienen_inventario(self):
        self.tramo_inicial.distancia_documentada_m = 100
        self.tramo_inicial.save()
        segundo = InventarioTramo.objects.create(
            ruta=self.ruta,
            tramo_secuencia=2,
            codigo_tramo='T002',
            distancia_documentada_m=120,
        )

        distancia = resolver_distancia_ruta(
            self.ruta,
            [self.tramo_inicial, segundo],
        )
        self.assertEqual(distancia.valor_m, 220)
        self.assertEqual(distancia.fuente, FUENTE_INVENTARIO_TRAMOS)

        segundo.distancia_documentada_m = None
        segundo.save()
        distancia = resolver_distancia_ruta(
            self.ruta,
            [self.tramo_inicial, segundo],
        )
        self.assertEqual(distancia.valor_m, 123)
        self.assertEqual(distancia.fuente, FUENTE_GEOMETRIA)

        self.ruta.distancia_m = None
        self.ruta.save()
        distancia = resolver_distancia_ruta(
            self.ruta,
            [self.tramo_inicial, segundo],
        )
        self.assertIsNone(distancia.valor_m)
        self.assertIsNone(distancia.fuente)

    def test_distancia_tramo_prioriza_inventario_y_respeta_cero(self):
        self.tramo_inicial.distancia_m = 250
        self.tramo_inicial.distancia_documentada_m = 0
        self.tramo_inicial.save()

        distancia = resolver_distancia_tramo(self.tramo_inicial)

        self.assertEqual(distancia.valor_m, 0)
        self.assertEqual(distancia.fuente, FUENTE_INVENTARIO_TRAMOS)

    def test_geometria_parcial_conserva_tramo_omitido(self):
        self._cargar_inventario_dos_tramos()
        _procesar_coordenadas_csv(self._geometria_dos_tramos())
        tramo_dos = InventarioTramo.objects.get(
            ruta=self.ruta,
            codigo_tramo='T002',
        )
        coordenadas_antes = list(
            CoordenadaRuta.objects.filter(tramo=tramo_dos)
            .order_by('orden')
            .values_list('id', 'latitud', 'longitud')
        )
        distancia_antes = tramo_dos.distancia_m
        archivo = SimpleUploadedFile(
            'solo-t001.csv',
            (
                'ruta,latitude,longitude,codigo_tramo\n'
                'RUTA-FASE-1,-12.0100,-77.0100,T001\n'
                'RUTA-FASE-1,-12.0110,-77.0110,T001\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        _procesar_coordenadas_csv(archivo)

        tramo_dos.refresh_from_db()
        self.assertEqual(
            coordenadas_antes,
            list(
                CoordenadaRuta.objects.filter(tramo=tramo_dos)
                .order_by('orden')
                .values_list('id', 'latitud', 'longitud')
            ),
        )
        self.assertEqual(tramo_dos.distancia_m, distancia_antes)
        self.assertIsNotNone(Ruta.objects.get(pk=self.ruta.pk).distancia_m)

    def test_geometria_parcial_no_se_presenta_como_distancia_total(self):
        self._cargar_inventario_dos_tramos()
        archivo = SimpleUploadedFile(
            'solo-t001.csv',
            (
                'ruta,latitude,longitude,codigo_tramo\n'
                'RUTA-FASE-1,-12.0100,-77.0100,T001\n'
                'RUTA-FASE-1,-12.0110,-77.0110,T001\n'
            ).encode('utf-8'),
            content_type='text/csv',
        )

        _procesar_coordenadas_csv(archivo)

        ruta = Ruta.objects.get(pk=self.ruta.pk)
        tramo_uno = ruta.tramos_inventario.get(codigo_tramo='T001')
        tramo_dos = ruta.tramos_inventario.get(codigo_tramo='T002')
        self.assertIsNotNone(tramo_uno.distancia_m)
        self.assertIsNone(tramo_dos.distancia_m)
        self.assertIsNone(ruta.distancia_m)


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

    def test_tarjeta_de_sites_abre_la_gestion_en_lugar_del_endpoint_post(self):
        response = self.client.get(reverse('configuracion'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("gestion_sites")}"')
        self.assertNotContains(response, f'href="{reverse("importar_sites_csv")}"')

    def test_ayuda_importacion_muestra_segmentacion_explicita(self):
        response = self.client.get(reverse('configuracion'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Inventario de Troncales')
        self.assertContains(response, 'Inventario de Tramos')
        self.assertContains(response, 'Geometría de Tramos')
        self.assertContains(response, 'tramo_secuencia')
        self.assertContains(response, 'codigo_tramo')
        self.assertContains(
            response,
            '/static/ejemplos/coordenadas_unificadas_ejemplo.csv',
        )

    def test_capacidad_odf_no_se_sobreasigna_desde_la_gui(self):
        _, _, _, odf = crear_jerarquia_odf(nombre='ODF-CAPACIDAD', capacidad=1)
        DetallePuertoODF.objects.create(
            odf_obj=odf,
            puerto_odf='1',
            estado_puerto='Libre',
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
        InventarioFibra.objects.create(ruta=ruta, fibra_numero='1', estado='Libre')

        response = self.client.post(
            reverse('api_create_fibra'),
            data=json.dumps({
                'ruta_nombre': ruta.nombre,
                'fibra_numero': '2',
                'estado': 'Libre',
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
                hub_site=site.nombre,
                sala=rack.sala.nombre,
                rack=rack.nombre,
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
            )
            InventarioFibra.objects.create(
                ruta=ruta,
                fibra_numero='F01',
                estado='Libre',
            )

    def test_consultas_inventario_externo_no_crecen_por_ruta(self):
        self._crear_rutas()
        cache.clear()
        with CaptureQueriesContext(connection) as consultas:
            response = self.client.get(reverse('inventario_externo'))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(consultas), 15)

    def test_consultas_json_mapa_no_crecen_por_tramo(self):
        self._crear_rutas()
        cache.clear()
        with CaptureQueriesContext(connection) as consultas:
            response = self.client.get(reverse('api_datos_inventario'))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(consultas), 15)

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
        with tempfile.TemporaryDirectory() as directorio:
            manifest = Path(directorio) / 'manifest.json'
            manifest.write_text('{"version": 5}', encoding='utf-8')
            call_command(
                'registrar_baseline_inventario',
                manifest=str(manifest),
                fecha_origen='2026-07-01T10:00:00-05:00',
                verbosity=0,
            )
        lote = LoteImportacion.objects.get(origen_registro='BASELINE_RECONSTRUIDO')
        self.assertEqual(lote.tipo, 'BASELINE_INVENTARIO_V5')
        self.assertEqual(lote.metadatos_origen['conteos']['rutas'], 1)
        self.assertIn('no representa una importacion', lote.metadatos_origen['nota'])
