import hashlib
import hmac
import io
import json
import time
import zipfile

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase
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
    _procesar_coordenadas_inventario_zip,
    _procesar_reservas,
    _procesar_ruta_otu,
    _procesar_tramos_inventario,
)


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
        self.assertEqual(ruta.distancia_m, 300)
        self.assertEqual([tramo.distancia_m for tramo in tramos], [100, 200])
        self.assertEqual([tramo.mufas for tramo in tramos], [2, 0])

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
        ]))
        self.client.force_login(self.usuario)
        _, _, _, self.odf = crear_jerarquia_odf(nombre='ODF-GUI', capacidad=3)
        self.puertos = [
            DetallePuertoODF.objects.create(
                odf_obj=self.odf,
                puerto_odf=str(numero),
                estado_puerto='Ocupado' if numero == 1 else 'Libre',
                fibra=f'F{numero}',
                destino=f'DESTINO-{numero}',
            )
            for numero in range(1, 4)
        ]
        self.troncal = Ruta.objects.create(nombre='TRONCAL-GUI', odf_origen=self.odf)
        InventarioTramo.objects.create(
            ruta=self.troncal,
            tramo_secuencia=1,
            hub_site=self.odf.hub_site,
            odf_nombre=self.odf.odf,
            destino='DESTINO-GUI',
            capacidad='12 Fibras',
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
        self.assertIn('/api/inventario/360/puerto/', payload['data'][0]['detail_url'])

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
