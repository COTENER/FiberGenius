import hashlib
import hmac
import json
import os
import tempfile
import time
import requests
import pandas as pd
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from .forms import (
    CustomAuthenticationForm,
    CustomUserChangeForm,
    CustomUserCreationForm,
    PermisosPersonalizadosField,
)
from .models import (
    AlarmaVeex,
    CoordenadaRuta,
    DetallePuertoODF,
    EventLog,
    HubSite,
    IDRuta,
    InventarioFibra,
    InventarioTramo,
    OTU,
    PerfilUmbral,
    PuertoOTU,
    Ruta,
    Reserva,
    TrazaOnDemand,
    TrazaReferencia,
)
from .otdr_analyzer import analizar_trazas
from .services.inventario import guardar_odf_normalizado, limpiar_texto, resolver_rack
from .views.on_demand import process_on_demand_flow
from .views.api import actualizar_medicion_individual, ejecutar_alarmas, ejecutar_script_actualizacion
from .views.usuarios import obtener_permisos_agrupados
from .views.utils import clasificar_tipo_evento, get_color_evento, get_geo_bounds
from .views.webhook import sync_veex_id_in_background


class ServiciosYFormulariosCoverageTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin-cobertura', 'admin@example.test', 'clave-segura')

    def test_normalizacion_y_guardado_odf_cubren_creacion_actualizacion_y_conflicto(self):
        self.assertEqual(limpiar_texto(None), '')
        self.assertEqual(limpiar_texto(' NaN '), '')
        self.assertEqual(limpiar_texto('  HUB-A  '), 'HUB-A')
        with self.assertRaises(ValidationError):
            resolver_rack('', 'SALA', '')

        odf, creado = guardar_odf_normalizado(
            odf_nombre='ODF-A', hub_nombre='HUB-A', sala_nombre='SALA-A', rack_nombre='RACK-A',
            defaults={'capacidad_puertos': 24},
        )
        self.assertTrue(creado)
        actualizado, creado = guardar_odf_normalizado(
            odf_nombre='odf-a', hub_nombre='hub-a', sala_nombre='SALA-A', rack_nombre='RACK-A',
            defaults={'capacidad_puertos': 48},
        )
        self.assertFalse(creado)
        self.assertEqual(actualizado.pk, odf.pk)
        self.assertEqual(actualizado.capacidad_puertos, 48)

        with self.assertRaises(ValidationError):
            guardar_odf_normalizado(
                odf_nombre='ODF-A', hub_nombre='HUB-B', sala_nombre='SALA-B', rack_nombre='RACK-B'
            )
        with self.assertRaises(ValidationError):
            guardar_odf_normalizado(
                odf_nombre='', hub_nombre='HUB-A', sala_nombre='SALA-A', rack_nombre='RACK-A'
            )

    def test_motor_otdr_clasifica_atenuaciones_y_corte(self):
        perfil = SimpleNamespace(
            atenuacion_minor_min=1.0,
            atenuacion_major_min=3.0,
            atenuacion_critical_min=5.0,
            nivel_minimo_corte_db=-15.0,
        )
        alarmas = analizar_trazas(
            [0, 150, 300, 450, 600],
            [-1, -3, -5, -8, -20],
            [0, 150, 300, 450, 1000],
            [-1, -1, -1, -1, -1],
            perfil,
        )
        self.assertTrue(any(a['severidad'] == 'Minor' for a in alarmas))
        self.assertTrue(any(a['severidad'] == 'Major' for a in alarmas))
        self.assertTrue(any(a['severidad'] == 'Critical' for a in alarmas))
        self.assertTrue(any(a['tipo_falla'] == 'Corte de Fibra' for a in alarmas))
        self.assertEqual(analizar_trazas([], [], [0], [0], None), [])

    @override_settings(GEO_BOUNDS={'LAT_MIN': -25, 'LAT_MAX': -20, 'LON_MIN': -71, 'LON_MAX': -66})
    def test_utilidades_de_clasificacion_colores_y_limites(self):
        self.assertEqual(clasificar_tipo_evento('FIBER_CUT', 'UNCLEARED', 1), 'FIBER_CUT')
        self.assertEqual(clasificar_tipo_evento('FIBER_CUT', 'RESOLVED', 10), 'DISCONNECTIONS')
        self.assertEqual(clasificar_tipo_evento('FIBER_CUT', 'RESOLVED', 61), 'FIBER_CUT')
        self.assertEqual(clasificar_tipo_evento('ATTENUATION', '', None), 'ATTENUATION')
        self.assertIsNone(clasificar_tipo_evento('OTRO', '', None))
        self.assertEqual(get_color_evento('FIBER_CUT'), 'red')
        self.assertEqual(get_color_evento('DISCONNECTIONS'), '#33daff')
        self.assertEqual(get_color_evento('ATTENUATION'), 'orange')
        self.assertEqual(get_color_evento('OTRO', 'INJECTION'), 'blue')
        self.assertEqual(get_color_evento('OTRO'), 'gray')
        self.assertEqual(get_geo_bounds(), (-25, -20, -71, -66))

    def test_formularios_de_usuario_aplican_rol_y_restricciones(self):
        grupo = Group.objects.create(name='Operadores')
        datos = {
            'username': 'nuevo-operador',
            'password1': 'Clave-segura-2026',
            'password2': 'Clave-segura-2026',
            'email': 'nuevo@example.test',
            'grupo_rol': grupo.pk,
            'is_staff': True,
        }
        form = CustomUserCreationForm(datos, actor=self.admin)
        self.assertTrue(form.is_valid(), form.errors)
        usuario = form.save()
        self.assertTrue(usuario.groups.filter(pk=grupo.pk).exists())
        self.assertTrue(usuario.is_staff)

        restringido = CustomUserCreationForm(actor=usuario)
        self.assertNotIn('is_staff', restringido.fields)
        self.assertNotIn('is_superuser', restringido.fields)

        cambio = CustomUserChangeForm(
            {'username': usuario.username, 'email': usuario.email, 'first_name': 'Ana', 'last_name': '',
             'is_active': True, 'grupo_rol': ''},
            instance=usuario,
            actor=self.admin,
        )
        self.assertTrue(cambio.is_valid(), cambio.errors)
        cambio.save()
        self.assertFalse(usuario.groups.exists())

        auth_form = CustomAuthenticationForm()
        self.assertIn('incorrectos', auth_form.error_messages['invalid_login'])

    def test_etiquetas_de_permisos_y_agrupacion(self):
        field = PermisosPersonalizadosField(queryset=Permission.objects.all())
        permiso = Permission.objects.get(codename='can_view_reports')
        self.assertIn('Reportes', field.label_from_instance(permiso))
        permiso = Permission.objects.filter(codename__startswith='add_').first()
        self.assertEqual(field.label_from_instance(permiso), 'Crear')
        agrupados = obtener_permisos_agrupados()
        self.assertIn('Inventario', agrupados)
        self.assertIn('Administración', agrupados)


class AdministracionCoverageTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin-vistas', 'admin-vistas@example.test', 'clave-segura')
        self.client.force_login(self.admin)

    def test_sites_listado_creacion_actualizacion_y_errores(self):
        self.assertEqual(self.client.get(reverse('gestion_sites')).status_code, 200)
        respuesta = self.client.post(reverse('crear_site_manual'), {
            'nombre': 'SITE-01', 'latitud': '-23.5', 'longitud': '-69.1', 'direccion': 'Sala A'
        })
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(HubSite.objects.filter(nombre='SITE-01').exists())
        self.client.post(reverse('crear_site_manual'), {'nombre': ''})
        self.client.post(reverse('crear_site_manual'), {'nombre': 'SITE-ERR', 'latitud': 'no-numero'})

    def test_importacion_sites_valida_extension_columnas_y_actualiza(self):
        invalido = SimpleUploadedFile('sites.txt', b'nombre\nSITE-X', content_type='text/plain')
        self.assertEqual(self.client.post(reverse('importar_sites_csv'), {'csv_file': invalido}).status_code, 302)

        sin_nombre = SimpleUploadedFile('sites.csv', b'codigo\n1', content_type='text/csv')
        self.client.post(reverse('importar_sites_csv'), {'csv_file': sin_nombre})

        contenido = b'nombre,latitud,longitud,direccion\nSITE-A,-23.1,-69.2,Principal\nSITE-B,,,\n'
        archivo = SimpleUploadedFile('sites.csv', contenido, content_type='text/csv')
        self.client.post(reverse('importar_sites_csv'), {'csv_file': archivo})
        self.assertEqual(HubSite.objects.count(), 2)
        archivo = SimpleUploadedFile('sites.csv', contenido.replace(b'Principal', b'Actualizada'), content_type='text/csv')
        self.client.post(reverse('importar_sites_csv'), {'csv_file': archivo})
        self.assertEqual(HubSite.objects.count(), 2)

    def test_api_umbrales_ciclo_completo_y_validaciones(self):
        self.assertEqual(self.client.get(reverse('configuracion_umbrales')).status_code, 200)
        self.assertEqual(self.client.get(reverse('api_perfiles_umbral')).json()['status'], 'success')

        crear = self.client.post(
            reverse('api_perfiles_umbral'),
            data=json.dumps({'action': 'create', 'nombre_perfil': 'Perfil Norte', 'splice_loss': 0.2}),
            content_type='application/json',
        )
        self.assertEqual(crear.status_code, 200)
        perfil = PerfilUmbral.objects.get(nombre_perfil='Perfil Norte')
        ruta = Ruta.objects.create(nombre='RUTA-UMBRAL')

        actualizar = self.client.post(
            reverse('api_perfiles_umbral'),
            data=json.dumps({'action': 'update', 'id': perfil.pk, 'nombre_perfil': 'Perfil Norte 2'}),
            content_type='application/json',
        )
        self.assertEqual(actualizar.status_code, 200)
        asignar = self.client.post(
            reverse('api_perfiles_umbral'),
            data=json.dumps({'action': 'assign', 'perfil_id': perfil.pk, 'ruta_ids': [ruta.pk]}),
            content_type='application/json',
        )
        self.assertEqual(asignar.status_code, 200)
        ruta.refresh_from_db()
        self.assertEqual(ruta.perfil_umbral_id, perfil.pk)

        eliminar = self.client.post(
            reverse('api_perfiles_umbral'),
            data=json.dumps({'action': 'delete', 'id': perfil.pk}),
            content_type='application/json',
        )
        self.assertEqual(eliminar.status_code, 200)
        error = self.client.post(reverse('api_perfiles_umbral'), data='{', content_type='application/json')
        self.assertEqual(error.status_code, 400)

    def test_gestion_de_usuarios_grupos_y_exportacion(self):
        grupo = Group.objects.create(name='Grupo inicial')
        usuario = User.objects.create_user('usuario-editable', 'u@example.test', 'Clave-2026-segura')
        self.assertEqual(self.client.get(reverse('lista_usuarios')).status_code, 200)
        self.assertEqual(self.client.get(reverse('lista_grupos')).status_code, 200)
        self.assertEqual(self.client.get(reverse('crear_usuario')).status_code, 200)
        self.assertEqual(self.client.get(reverse('editar_usuario', args=[usuario.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('crear_grupo')).status_code, 200)
        self.assertEqual(self.client.get(reverse('editar_grupo', args=[grupo.pk])).status_code, 200)

        creado = self.client.post(reverse('crear_grupo'), {'name': 'Grupo nuevo'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(creado.json(), {'success': True})
        grupo_nuevo = Group.objects.get(name='Grupo nuevo')
        editado = self.client.post(
            reverse('editar_grupo', args=[grupo_nuevo.pk]),
            {'name': 'Grupo actualizado'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(editado.json(), {'success': True})

        exportado = self.client.get(reverse('exportar_usuarios_csv'))
        self.assertEqual(exportado.status_code, 200)
        self.assertIn('usuario-editable', exportado.content.decode())
        self.assertEqual(self.client.post(reverse('eliminar_grupo', args=[grupo_nuevo.pk])).status_code, 302)
        self.assertEqual(self.client.post(reverse('eliminar_usuario', args=[usuario.pk])).status_code, 302)


class PermisosTrazaOnDemandTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.media_override.enable()
        self.addCleanup(self.media_dir.cleanup)
        self.addCleanup(self.media_override.disable)

        self.ruta = Ruta.objects.create(nombre='RUTA-PERMISOS-ONDEMAND')
        self.traza = TrazaOnDemand.objects.create(
            route_name=self.ruta.nombre,
            ruta_obj=self.ruta,
            status='Completed',
            archivo_sor=SimpleUploadedFile('ondemand-permisos.sor', b'SOR-ONDEMAND'),
        )

    def _usuario_con_permisos(self, nombre, *codenames):
        usuario = User.objects.create_user(nombre, password='clave-segura')
        if codenames:
            usuario.user_permissions.add(
                *Permission.objects.filter(
                    content_type__app_label='mapas',
                    codename__in=codenames,
                )
            )
        return usuario

    def test_usuario_sin_permiso_no_puede_consultar_la_traza(self):
        usuario = self._usuario_con_permisos('sin-permiso')
        self.client.force_login(usuario)

        self.assertEqual(self.client.get(reverse('visor_ondemand')).status_code, 403)
        self.assertEqual(
            self.client.get(reverse('get_on_demand_status', args=[self.traza.pk])).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse('api_get_ondemand_data', args=[self.traza.pk])).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse('descargar_traza_ondemand', args=[self.traza.pk])).status_code,
            403,
        )

    def test_permiso_de_traza_permite_visualizar_y_descargar(self):
        usuario = self._usuario_con_permisos('lector-ondemand', 'view_trazaondemand')
        self.client.force_login(usuario)

        visor = self.client.get(
            reverse('visor_ondemand'),
            {'route_name': self.ruta.nombre, 'traza_id': self.traza.pk},
        )
        self.assertEqual(visor.status_code, 200)
        self.assertNotContains(visor, 'id="btn-request"')
        self.assertEqual(
            self.client.get(reverse('get_on_demand_status', args=[self.traza.pk])).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                reverse('iniciar_traza_ondemand'),
                data=json.dumps({'route_name': self.ruta.nombre}),
                content_type='application/json',
            ).status_code,
            403,
        )

        descarga = self.client.get(
            reverse('descargar_traza_ondemand', args=[self.traza.pk])
        )
        self.assertEqual(descarga.status_code, 200)
        self.assertEqual(b''.join(descarga.streaming_content), b'SOR-ONDEMAND')

    @override_settings(VEEX_ENABLED=False)
    def test_permiso_de_creacion_controla_el_disparo(self):
        usuario = self._usuario_con_permisos(
            'operador-ondemand',
            'add_trazaondemand',
            'view_trazaondemand',
        )
        self.client.force_login(usuario)

        visor = self.client.get(reverse('visor_ondemand'))
        self.assertEqual(visor.status_code, 200)
        self.assertContains(visor, 'id="btn-request"')
        respuesta = self.client.post(
            reverse('iniciar_traza_ondemand'),
            data=json.dumps({'route_name': self.ruta.nombre}),
            content_type='application/json',
        )
        self.assertEqual(respuesta.status_code, 503)

    def test_permiso_de_alarmas_no_autoriza_descarga_ondemand(self):
        usuario = self._usuario_con_permisos('lector-alarmas', 'view_alarmaveex')
        self.client.force_login(usuario)

        self.assertEqual(
            self.client.get(reverse('descargar_traza_ondemand', args=[self.traza.pk])).status_code,
            403,
        )


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class IntegracionesCoverageTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin-integracion', 'integracion@example.test', 'clave-segura')
        self.client.force_login(self.admin)
        self.otu = OTU.objects.create(nombre='OTU-01', modelo='FX150', latitud='-23.0', longitud='-69.0')
        self.ruta = Ruta.objects.create(nombre='RUTA-INTEGRACION', otu=self.otu, distancia_m=1000)
        PuertoOTU.objects.create(otu=self.otu, numero=1, estado='Monitoreado', ruta_asociada=self.ruta)
        CoordenadaRuta.objects.create(ruta=self.ruta, orden=1, latitud='-23.0000000', longitud='-69.0000000')
        CoordenadaRuta.objects.create(ruta=self.ruta, orden=2, latitud='-23.0100000', longitud='-69.0100000')

    def _alarma(self, alarm_type='High Loss', veex_alarm_id=900):
        return AlarmaVeex.objects.create(
            status='Active', alarm_type=alarm_type, alarm_level='MAJOR', timestamp='2026-07-19T10:00:00Z',
            device_serial='OTU-01', device_ip='127.0.0.1', port='1', distance=100,
            route_name=self.ruta.nombre, latitude='-23.001', longitude='-69.001',
            veex_alarm_id=veex_alarm_id, ruta_obj=self.ruta,
        )

    def test_mapa_equipos_y_visor_historico(self):
        respuesta = self.client.get(reverse('mapa_alarmas'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'RUTA-INTEGRACION')

        alarma = self._alarma('Fiber Break')
        log = EventLog.objects.create(
            incident=alarma, status='Resolved', alarm_type='Reflective', alarm_level='MINOR',
            timestamp='2026-07-19T10:01:00Z', device_serial='OTU-01', device_ip='127.0.0.1', port='1',
            distance=90, route_name=self.ruta.nombre, latitude='-23.001', longitude='-69.001',
            veex_alarm_id=901, ruta_obj=self.ruta,
        )
        por_incidente = self.client.get(reverse('visor_traza', args=[alarma.veex_alarm_id]), {'incident_id': alarma.pk})
        self.assertEqual(por_incidente.status_code, 200)
        historico = self.client.get(reverse('visor_traza', args=[alarma.veex_alarm_id]), {'log_id': log.pk})
        self.assertEqual(historico.status_code, 200)

    @override_settings(VEEX_ENABLED=False)
    def test_on_demand_valida_configuracion_y_estado(self):
        self.assertEqual(self.client.get(reverse('visor_ondemand'), {'route_name': self.ruta.nombre}).status_code, 200)
        deshabilitado = self.client.post(
            reverse('iniciar_traza_ondemand'), data=json.dumps({'route_name': self.ruta.nombre}),
            content_type='application/json',
        )
        self.assertEqual(deshabilitado.status_code, 503)
        traza = TrazaOnDemand.objects.create(route_name=self.ruta.nombre, ruta_obj=self.ruta, status='Pending')
        estado = self.client.get(reverse('get_on_demand_status', args=[traza.pk])).json()
        self.assertEqual(estado['status'], 'Pending')
        self.assertFalse(estado['has_file'])

    @override_settings(VEEX_ENABLED=True)
    @patch('mapas.views.on_demand._ON_DEMAND_EXECUTOR')
    def test_on_demand_inicia_solicitud_y_valida_payload(self, executor):
        vacio = self.client.post(reverse('iniciar_traza_ondemand'), data='{}', content_type='application/json')
        self.assertEqual(vacio.json()['status'], 'error')
        ausente = self.client.post(
            reverse('iniciar_traza_ondemand'), data=json.dumps({'route_name': 'NO-EXISTE'}),
            content_type='application/json',
        )
        self.assertEqual(ausente.status_code, 404)
        creado = self.client.post(
            reverse('iniciar_traza_ondemand'), data=json.dumps({'route_name': self.ruta.nombre}),
            content_type='application/json',
        )
        self.assertEqual(creado.json()['status'], 'success')
        executor.submit.assert_called_once()

    @patch('mapas.views.on_demand.time.sleep')
    @patch('mapas.views.on_demand.download_on_demand_sor', return_value=b'SOR')
    @patch('mapas.views.on_demand.check_on_demand_status', return_value='completed')
    @patch('mapas.views.on_demand.create_on_demand_task', return_value='TASK-1')
    @patch('mapas.views.on_demand.get_monitoring_port_id', return_value='PORT-1')
    @patch('mapas.views.on_demand.get_veex_device_lasers', return_value=['SM1550'])
    def test_flujo_on_demand_completa_archivo(self, *_mocks):
        traza = TrazaOnDemand.objects.create(route_name=self.ruta.nombre, ruta_obj=self.ruta)
        process_on_demand_flow(traza.pk)
        traza.refresh_from_db()
        self.assertEqual(traza.status, 'Completed')
        self.assertTrue(traza.archivo_sor)
        traza.archivo_sor.delete(save=False)

    def test_flujo_on_demand_cubre_fallos_controlados(self):
        casos = [
            ({'get_veex_device_lasers': []}, 'láseres'),
            ({'get_veex_device_lasers': ['SM1625'], 'get_monitoring_port_id': None}, 'puerto'),
            ({'get_veex_device_lasers': ['SM1650'], 'get_monitoring_port_id': 'P', 'create_on_demand_task': None}, 'tarea'),
        ]
        for indice, (valores, fragmento) in enumerate(casos):
            traza = TrazaOnDemand.objects.create(route_name=f'{self.ruta.nombre}-{indice}')
            with patch('mapas.views.on_demand.get_veex_device_lasers', return_value=valores.get('get_veex_device_lasers')), \
                 patch('mapas.views.on_demand.get_monitoring_port_id', return_value=valores.get('get_monitoring_port_id')), \
                 patch('mapas.views.on_demand.create_on_demand_task', return_value=valores.get('create_on_demand_task')):
                process_on_demand_flow(traza.pk)
            traza.refresh_from_db()
            self.assertEqual(traza.status, 'Failed')
            self.assertIn(fragmento, traza.error_message)

    @patch('mapas.views.on_demand.time.sleep')
    @patch('mapas.views.on_demand.create_on_demand_task', return_value='TASK-FALLA')
    @patch('mapas.views.on_demand.get_monitoring_port_id', return_value='P')
    @patch('mapas.views.on_demand.get_veex_device_lasers', return_value=['SM1550'])
    def test_flujo_on_demand_cubre_estado_final_y_descarga_fallida(self, *_mocks):
        traza_estado = TrazaOnDemand.objects.create(route_name='RUTA-ESTADO')
        with patch('mapas.views.on_demand.check_on_demand_status', return_value='failed'):
            process_on_demand_flow(traza_estado.pk)
        traza_estado.refresh_from_db()
        self.assertEqual(traza_estado.status, 'Failed')
        self.assertIn('Estado final', traza_estado.error_message)

        traza_descarga = TrazaOnDemand.objects.create(route_name='RUTA-DESCARGA')
        with patch('mapas.views.on_demand.check_on_demand_status', return_value='completed'), \
             patch('mapas.views.on_demand.download_on_demand_sor', return_value=None):
            process_on_demand_flow(traza_descarga.pk)
        traza_descarga.refresh_from_db()
        self.assertEqual(traza_descarga.status, 'Failed')
        self.assertIn('descargar', traza_descarga.error_message)

        traza_excepcion = TrazaOnDemand.objects.create(route_name='RUTA-EXCEPCION')
        with patch('mapas.views.on_demand.get_veex_device_lasers', side_effect=RuntimeError('fallo controlado')):
            process_on_demand_flow(traza_excepcion.pk)
        traza_excepcion.refresh_from_db()
        self.assertEqual(traza_excepcion.status, 'Failed')
        self.assertIn('fallo controlado', traza_excepcion.error_message)

    @patch('mapas.views.api.management.call_command')
    def test_actualizaciones_manual_y_alarmas(self, call_command):
        request = RequestFactory().post('/api/actualizar/', {'nombre_ruta': self.ruta.nombre})
        request.user = self.admin
        ciclo = ejecutar_script_actualizacion(request)
        self.assertEqual(ciclo.status_code, 200)
        self.assertEqual(call_command.call_count, 4)
        request = RequestFactory().post('/api/actualizar-alarmas/')
        request.user = self.admin
        alarmas = ejecutar_alarmas(request)
        self.assertEqual(alarmas.status_code, 200)

        call_command.side_effect = RuntimeError('fallo interno')
        request = RequestFactory().post('/api/actualizar-alarmas/')
        request.user = self.admin
        self.assertEqual(ejecutar_alarmas(request).status_code, 500)
        request = RequestFactory().post('/api/actualizar/', {'nombre_ruta': self.ruta.nombre})
        request.user = self.admin
        self.assertEqual(ejecutar_script_actualizacion(request).status_code, 500)

    @override_settings(MEDICION_INDIVIDUAL_API_URL='https://onmsi.example/api')
    @patch('mapas.views.api.requests.post')
    def test_medicion_individual_valida_y_maneja_respuestas(self, post):
        def ejecutar(datos):
            request = RequestFactory().post('/api/medicion/', datos)
            request.user = self.admin
            return actualizar_medicion_individual(request)

        self.assertEqual(ejecutar({}).status_code, 400)
        self.assertEqual(ejecutar({'nombre_ruta': 'SIN-ID'}).status_code, 404)
        IDRuta.objects.create(ruta_obj=self.ruta, id_onmsi=123)
        post.return_value = SimpleNamespace(status_code=202)
        self.assertEqual(ejecutar({'nombre_ruta': self.ruta.nombre}).status_code, 200)
        post.return_value = SimpleNamespace(status_code=500)
        self.assertEqual(ejecutar({'nombre_ruta': self.ruta.nombre}).status_code, 502)
        post.side_effect = requests.exceptions.ConnectionError('sin conexión')
        self.assertEqual(ejecutar({'nombre_ruta': self.ruta.nombre}).status_code, 503)

    @override_settings(MEDICION_INDIVIDUAL_API_URL='')
    def test_medicion_individual_requiere_configuracion(self):
        IDRuta.objects.create(ruta_obj=self.ruta, id_onmsi=321)
        request = RequestFactory().post('/api/medicion/', {'nombre_ruta': self.ruta.nombre})
        request.user = self.admin
        self.assertEqual(actualizar_medicion_individual(request).status_code, 503)

    @patch('mapas.veex_api.get_sor_file', return_value=None)
    def test_descarga_sor_maneja_ausencia_y_contenido(self, get_sor):
        self.assertEqual(self.client.get(reverse('descargar_sor', args=[999])).status_code, 404)
        get_sor.return_value = b'SOR-DATA'
        respuesta = self.client.get(reverse('descargar_sor', args=[999]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.content, b'SOR-DATA')

    @patch('pyotdr.read.sorparse')
    def test_parseo_sor_api_referencia_y_ondemand(self, sorparse):
        resultados = {
            'KeyEvents': {
                'num events': 2,
                'event 1': {'event number': 1, 'distance': 0.5, 'type': 'Splice', 'splice loss': '0.2'},
                'Summary': {'total loss': '1.1'},
            },
            'FxdParams': {'resolution': 0.25},
        }
        sorparse.return_value = ('ok', resultados, ['0.0\t-1.0\n', '1.0\t-2.0\n'])

        alarma = self._alarma()
        alarma.archivo_sor = SimpleUploadedFile('alarma.sor', b'SOR')
        alarma.save()
        datos = self.client.get(reverse('get_traza_data', args=[alarma.veex_alarm_id]), {'incident_id': alarma.pk})
        self.assertEqual(datos.status_code, 200)
        self.assertEqual(datos.json()['data']['x'], [0.0, 1000.0])

        traza = TrazaOnDemand.objects.create(
            route_name=self.ruta.nombre, ruta_obj=self.ruta, status='Completed',
            archivo_sor=SimpleUploadedFile('ondemand.sor', b'SOR'),
        )
        ondemand = self.client.get(reverse('api_get_ondemand_data', args=[traza.pk]))
        self.assertEqual(ondemand.status_code, 200)
        self.assertEqual(ondemand.json()['data']['link_loss'], 1.3)

        referencia = TrazaReferencia.objects.create(
            ruta=self.ruta, nombre='Base', archivo_sor=SimpleUploadedFile('base.sor', b'SOR')
        )
        ref = self.client.get(reverse('get_referencia_data', args=[referencia.pk]))
        self.assertEqual(ref.status_code, 200)

    @patch('mapas.veex_api.get_sor_file', return_value=b'SOR')
    @patch('pyotdr.read.sorparse', return_value=('invalid', {}, []))
    def test_parseo_sor_reporta_archivo_invalido(self, _parse, _sor):
        respuesta = self.client.get(reverse('get_traza_data', args=[1234]))
        self.assertEqual(respuesta.status_code, 500)

    @patch('mapas.veex_api.get_sor_file', return_value=None)
    def test_parseo_sor_reporta_archivo_ausente(self, _sor):
        respuesta = self.client.get(reverse('get_traza_data', args=[1234]))
        self.assertEqual(respuesta.status_code, 404)

    def test_visor_traza_resuelve_ids_veex_e_historicos(self):
        alarma = self._alarma('High Loss', veex_alarm_id=950)
        por_veex = self.client.get(reverse('visor_traza', args=[950]))
        self.assertEqual(por_veex.status_code, 200)
        self.assertContains(por_veex, 'Atenuación')

        log = EventLog.objects.create(
            incident=alarma, status='Resolved', alarm_type='Evento desconocido', alarm_level='MINOR',
            timestamp='t-log', device_serial='OTU-01', device_ip='127.0.0.1', port='1',
            route_name=self.ruta.nombre, latitude='-23.0', longitude='-69.0', veex_alarm_id=951,
        )
        historico = self.client.get(reverse('visor_traza', args=[951]))
        self.assertEqual(historico.status_code, 200)
        self.assertEqual(historico.context['alarma'].pk, log.pk)

    def test_proactivo_asigna_perfil_promueve_y_calcula_coordenadas(self):
        perfil = PerfilUmbral.objects.create(nombre_perfil='Perfil Proactivo', margen_corte_fibra_db=2.5)
        self.assertEqual(
            self.client.get(reverse('visor_proactivo'), {'route_name': self.ruta.nombre}).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse('visor_establecer_referencia'), {'route_name': self.ruta.nombre}).status_code, 200
        )
        asignacion = self.client.post(
            reverse('api_asignar_perfil_ruta'),
            data=json.dumps({'route_name': self.ruta.nombre, 'perfil_id': perfil.pk}),
            content_type='application/json',
        )
        self.assertEqual(asignacion.json()['status'], 'success')

        traza = TrazaOnDemand.objects.create(
            route_name=self.ruta.nombre, ruta_obj=self.ruta, status='Completed',
            archivo_sor=SimpleUploadedFile('promover.sor', b'SOR'),
        )
        promover = self.client.post(
            reverse('promover_referencia'),
            data=json.dumps({'traza_id': traza.pk, 'nombre_referencia': 'Base Oficial'}),
            content_type='application/json',
        )
        self.assertEqual(promover.status_code, 200)
        self.assertTrue(TrazaReferencia.objects.filter(ruta=self.ruta, activa=True).exists())

        coordenadas = self.client.get(
            reverse('api_calcular_coordenadas', args=[self.ruta.pk]), {'d': ['0', '500', 'invalida']}
        )
        self.assertEqual(coordenadas.status_code, 200)
        self.assertEqual(len(coordenadas.json()['coordenadas']), 2)


@override_settings(
    VEEX_ENABLED=True,
    VEEX_TLS_VERIFY=True,
    VEEX_CA_BUNDLE='',
)
class VeexApiCoverageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.constantes = patch.multiple(
            'mapas.veex_api',
            VEEX_API_URL='https://veex.example/api',
            VEEX_API_USER='usuario',
            VEEX_API_PASS='secreto',
        )
        self.constantes.start()
        self.addCleanup(self.constantes.stop)
        self.addCleanup(cache.clear)

    @patch('mapas.veex_api.requests.post')
    def test_token_se_obtiene_y_reutiliza_desde_cache(self, post):
        respuesta = MagicMock()
        respuesta.json.return_value = {'token': 'JWT-123'}
        post.return_value = respuesta
        from .veex_api import get_veex_token

        self.assertEqual(get_veex_token(), 'JWT-123')
        self.assertEqual(get_veex_token(), 'JWT-123')
        post.assert_called_once()

    @patch('mapas.veex_api.get_veex_token', return_value='JWT')
    @patch('mapas.veex_api.requests.get')
    @patch('mapas.veex_api.requests.post')
    def test_operaciones_veex_exitosas(self, post, get, _token):
        from .veex_api import (
            check_on_demand_status,
            create_on_demand_task,
            download_on_demand_sor,
            fetch_veex_alarm_id,
            get_monitoring_port_id,
            get_sor_file,
            get_veex_device_lasers,
        )

        alarmas = MagicMock()
        alarmas.json.return_value = {'items': [
            {'id': 10, 'distanceMeters': 100.2, 'type': 'Otro', 'activeAt': '2026-01-01'},
            {'id': 11, 'distanceMeters': 100.1, 'type': 'Event Loss', 'activeAt': '2026-01-02'},
        ]}
        sor = MagicMock(content=b'SOR-ALARMA')
        dispositivo = MagicMock()
        dispositivo.json.return_value = {'supportedMeasurementParameters': {'laserUnits': {'SM1550': {}, 'SM1625': {}}}}
        puertos = MagicMock()
        puertos.json.return_value = [{'id': 'P-1', 'note': ' ruta-veex '}]
        estado = MagicMock()
        estado.json.return_value = {'status': 'completed'}
        descarga = MagicMock(content=b'SOR-ONDEMAND')
        get.side_effect = [alarmas, sor, dispositivo, puertos, estado, descarga]

        crear = MagicMock()
        crear.json.return_value = {'onDemandId': 'TASK-7'}
        post.return_value = crear

        self.assertEqual(fetch_veex_alarm_id(100, 'ignorado', 'EventLoss'), 11)
        self.assertEqual(get_sor_file(11), b'SOR-ALARMA')
        self.assertEqual(get_veex_device_lasers(), ['SM1550', 'SM1625'])
        self.assertEqual(get_monitoring_port_id('RUTA-VEEX'), 'P-1')
        self.assertEqual(create_on_demand_task('P-1', 'SM1550'), 'TASK-7')
        self.assertEqual(check_on_demand_status('TASK-7'), 'completed')
        self.assertEqual(download_on_demand_sor('TASK-7'), b'SOR-ONDEMAND')

    @patch('mapas.veex_api.get_veex_token', return_value='JWT')
    @patch('mapas.veex_api.requests.get', side_effect=RuntimeError('red no disponible'))
    @patch('mapas.veex_api.requests.post', side_effect=RuntimeError('red no disponible'))
    def test_operaciones_veex_degradan_sin_exponer_excepciones(self, _post, _get, _token):
        from .veex_api import (
            check_on_demand_status,
            create_on_demand_task,
            download_on_demand_sor,
            fetch_veex_alarm_id,
            get_monitoring_port_id,
            get_sor_file,
            get_veex_device_lasers,
        )

        self.assertIsNone(fetch_veex_alarm_id(1, 'x'))
        self.assertIsNone(get_sor_file(1))
        self.assertEqual(get_veex_device_lasers(), [])
        self.assertIsNone(get_monitoring_port_id('ruta'))
        self.assertIsNone(create_on_demand_task('p', 'laser'))
        self.assertEqual(check_on_demand_status('t'), 'Failed')
        self.assertIsNone(download_on_demand_sor('t'))

    @override_settings(VEEX_CA_BUNDLE='C:/certificados/ca.pem')
    def test_tls_prefiere_bundle_configurado(self):
        from .veex_api import _tls_verify
        self.assertEqual(_tls_verify(), 'C:/certificados/ca.pem')


@override_settings(
    VEEX_ENABLED=True,
    FIBERGENIUS_WEBHOOK_SECRET='secreto-pruebas-webhook',
    FIBERGENIUS_WEBHOOK_MAX_SKEW_SECONDS=300,
    MEDIA_ROOT=tempfile.gettempdir(),
)
class WebhookCompletoCoverageTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin-webhook', 'webhook@example.test', 'clave-segura')
        self.client.force_login(self.admin)
        self.otu = OTU.objects.create(nombre='OTU-WEBHOOK')
        self.ruta = Ruta.objects.create(nombre='RUTA-WEBHOOK', otu=self.otu)

    def _post_firmado(self, payload, timestamp=None):
        cuerpo = payload if isinstance(payload, bytes) else json.dumps(payload).encode('utf-8')
        marca = str(timestamp or int(time.time()))
        firma = hmac.new(
            b'secreto-pruebas-webhook', marca.encode('ascii') + b'.' + cuerpo, hashlib.sha256
        ).hexdigest()
        return self.client.post(
            reverse('webhook_alarma'),
            data=cuerpo,
            content_type='application/json',
            HTTP_X_FIBERGENIUS_TIMESTAMP=marca,
            HTTP_X_FIBERGENIUS_SIGNATURE=f'sha256={firma}',
        )

    @patch('mapas.views.webhook._enviar_sincronizacion')
    def test_ciclo_incidente_activo_duplicado_mutacion_y_resolucion(self, sincronizar):
        base = {
            'route_name': self.ruta.nombre,
            'status': 'Active',
            'alarm_type': 'Event Loss',
            'alarm_level': 'Major',
            'latitude': '-23.1',
            'longitude': '-69.2',
            'port': '2',
            'distance': '150.5',
            'device_ip': '127.0.0.1',
            'timestamp': '2026-07-19T10:00:00-05:00',
        }
        creado = self._post_firmado(base)
        self.assertEqual(creado.status_code, 200)
        incidente = AlarmaVeex.objects.get()
        self.assertEqual(incidente.device_serial, self.otu.nombre)
        self.assertEqual(EventLog.objects.count(), 1)
        sincronizar.assert_called_once()

        duplicado = self._post_firmado(base)
        self.assertIn('duplicada', duplicado.json()['message'])
        self.assertEqual(EventLog.objects.count(), 1)

        mutacion = dict(base, alarm_level='Critical', timestamp='2026-07-19T10:01:00-05:00')
        self.assertEqual(self._post_firmado(mutacion).status_code, 200)
        incidente.refresh_from_db()
        self.assertEqual(incidente.alarm_level, 'Critical')

        resuelto = dict(base, status='Resolved', timestamp='2026-07-19T10:02:00-05:00')
        self.assertEqual(self._post_firmado(resuelto).status_code, 200)
        incidente.refresh_from_db()
        self.assertEqual(incidente.status, 'Resolved')

        redundante = dict(resuelto, timestamp='2026-07-19T10:03:00-05:00')
        self.assertEqual(self._post_firmado(redundante).status_code, 200)
        self.assertEqual(AlarmaVeex.objects.count(), 1)
        self.assertEqual(EventLog.objects.count(), 4)

    def test_webhook_valida_json_coordenadas_y_mensaje_de_prueba(self):
        self.assertEqual(self._post_firmado(b'{').status_code, 400)
        self.assertEqual(self._post_firmado({'route_name': 'R'}).status_code, 400)
        self.assertEqual(
            self._post_firmado({'route_name': 'R', 'latitude': 'x', 'longitude': 'y'}).status_code,
            400,
        )
        prueba = self._post_firmado({'test_message': 'trap de prueba', 'timestamp': '2026-07-19T11:00:00-05:00'})
        self.assertEqual(prueba.status_code, 200)
        self.assertEqual(AlarmaVeex.objects.get().route_name, 'Ruta de Prueba SNMP')

    def test_api_alarmas_filtra_pagina_y_rechaza_parametros_invalidos(self):
        for indice, estado in enumerate(('Active', 'Resolved')):
            AlarmaVeex.objects.create(
                status=estado, alarm_type='High Loss', alarm_level='MAJOR', timestamp=f't-{indice}',
                device_serial='OTU', device_ip='127.0.0.1', port=str(indice), route_name='RUTA',
                latitude='-23.0', longitude='-69.0', veex_alarm_id=700 + indice,
            )
        activas = self.client.get(reverse('get_alarmas_activas'), {'limit': 1, 'offset': 0}).json()
        self.assertEqual(activas['pagination']['total'], 1)
        todas = self.client.get(reverse('get_alarmas_activas'), {'include_resolved': '1'}).json()
        self.assertEqual(todas['pagination']['total'], 2)
        self.assertEqual(
            self.client.get(reverse('get_alarmas_activas'), {'limit': 'invalido'}).status_code,
            400,
        )

    @patch('mapas.veex_api.get_sor_file', return_value=b'SOR-SYNC')
    @patch('mapas.veex_api.fetch_veex_alarm_id', return_value=808)
    def test_sincronizacion_asocia_id_y_archivos(self, _fetch, _sor):
        incidente = AlarmaVeex.objects.create(
            status='Active', alarm_type='High Loss', alarm_level='MAJOR', timestamp='t',
            device_serial='OTU', device_ip='127.0.0.1', port='1', route_name=self.ruta.nombre,
            latitude='-23.0', longitude='-69.0', ruta_obj=self.ruta,
        )
        log = EventLog.objects.create(
            incident=incidente, status='Active', alarm_type='High Loss', alarm_level='MAJOR', timestamp='t',
            device_serial='OTU', device_ip='127.0.0.1', port='1', route_name=self.ruta.nombre,
            latitude='-23.0', longitude='-69.0', ruta_obj=self.ruta,
        )
        sync_veex_id_in_background(incidente.pk, 10, 't', log.pk, 'High Loss')
        incidente.refresh_from_db()
        log.refresh_from_db()
        self.assertEqual(incidente.veex_alarm_id, 808)
        self.assertEqual(log.veex_alarm_id, 808)
        self.assertTrue(incidente.archivo_sor)
        self.assertTrue(log.archivo_sor)
        incidente.archivo_sor.delete(save=False)
        log.archivo_sor.delete(save=False)


class InfraestructuraCoverageTests(TestCase):
    @override_settings(
        MAP_TILE_URL_LIGHT='light', MAP_TILE_URL_DARK='dark',
        MAP_TILE_URL_SATELLITE='satellite', MAP_TILE_ATTRIBUTION='atribucion',
    )
    def test_context_processor_de_despliegue(self):
        from fibergenius.context_processors import deployment
        datos = deployment(RequestFactory().get('/'))
        self.assertEqual(datos['MAP_TILE_URL_LIGHT'], 'light')
        self.assertEqual(datos['MAP_TILE_ATTRIBUTION'], 'atribucion')

    def test_health_verifica_base_y_reporta_indisponibilidad(self):
        from fibergenius.health import health
        request = RequestFactory().get('/health/')
        self.assertEqual(health(request).status_code, 200)
        with patch('fibergenius.health.connection.cursor', side_effect=RuntimeError('db caída')):
            self.assertEqual(health(request).status_code, 503)


class _CursorAnaliticoFalso:
    def __init__(self, fallar_opticos=False):
        self.resultado = []
        self.fallar_opticos = fallar_opticos
        self.fallo_consumido = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, consulta, parametros=None):
        texto = ' '.join(str(consulta).split())
        if 'SELECT DISTINCT EXTRACT(YEAR' in texto:
            self.resultado = [(2026,), (2025,)]
        elif 'SELECT DISTINCT o.nombre' in texto:
            self.resultado = [('OTU-ANALITICA',)]
        elif 'FROM mon_otdr_eventos_geo' in texto:
            if self.fallar_opticos and 'event_test_status' in texto and not self.fallo_consumido:
                self.fallo_consumido = True
                raise RuntimeError('columna opcional ausente')
            self.resultado = [('RUTA-A', None, 2, 1, 2, 3, 4, 5, 15)]
        elif 'SELECT DISTINCT COALESCE(r.nombre, a.route_name)' in texto:
            self.resultado = [('RUTA-ANALITICA',)]
        elif 'SELECT alarm_level' in texto:
            self.resultado = [
                ('critical', 'FIBER_CUT', 'Active', 2026, 1, 1, 'RUTA-A', 3),
                ('major', 'ATTENUATION', 'Resolved', 2026, 2, 4, 'RUTA-B', 2),
                ('minor', 'DISCONNECTIONS', 'Resolved', 2025, 3, 8, 'RUTA-A', 1),
                (None, 'REFLEXION', 'Active', 2025, 4, 12, None, 4),
                ('major', 'INJECTION', 'Active', 2026, 1, 2, 'RUTA-C', 1),
                ('minor', 'IGNORADO', 'Active', 2026, 1, 1, 'RUTA-X', 99),
            ]
        elif 'GROUP BY COALESCE(r.nombre, a.route_name)' in texto:
            self.resultado = [('RUTA-A', 'OTU-A', 2, 1, 1, 1, 1, 6)]
        else:
            self.resultado = []

    def fetchall(self):
        return self.resultado


class AnaliticaCoverageTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin-analitica', 'analitica@example.test', 'clave-segura')

    def _request(self, ruta, parametros=None, ajax=False):
        extras = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'} if ajax else {}
        request = RequestFactory().get(ruta, parametros or {}, **extras)
        request.user = self.admin
        return request

    def _cursor(self, fallar_opticos=False):
        cursor = _CursorAnaliticoFalso(fallar_opticos=fallar_opticos)
        return MagicMock(return_value=cursor)

    def test_dashboard_procesa_series_filtros_y_respuesta_ajax(self):
        from .views.dashboard import dashboard
        parametros = {
            'anio': '2026', 'otu': 'OTU-ANALITICA', 'ruta': 'RUTA-A',
            'severidad': 'CRITICAL', 'tipo_evento': 'DISCONNECTIONS',
        }
        with patch('mapas.views.dashboard.connection.cursor', self._cursor()), \
             patch('mapas.views.dashboard.contar_rutas_db', return_value=1):
            respuesta = dashboard(self._request('/dashboard/', parametros, ajax=True))
        self.assertEqual(respuesta.status_code, 200)
        datos = json.loads(respuesta.content)
        self.assertEqual(datos['cantidad_eventos'], 11)
        self.assertEqual(len(datos['yoy_series']), 2)
        self.assertGreater(datos['criticidad_pct'], 0)

        with patch('mapas.views.dashboard.connection.cursor', self._cursor()), \
             patch('mapas.views.dashboard.contar_rutas_db', return_value=1), \
             patch('mapas.views.dashboard.render', return_value=HttpResponse('RUTA-A')):
            html = dashboard(self._request('/dashboard/', {'tipo_evento': 'FIBER_CUT'}))
        self.assertEqual(html.status_code, 200)
        self.assertContains(html, 'RUTA-A')

        with patch('mapas.views.dashboard.connection.cursor', self._cursor()), \
             patch('mapas.views.dashboard.contar_rutas_db', return_value=1), \
             patch('mapas.views.dashboard.render', return_value=HttpResponse('ok')):
            otro = dashboard(self._request('/dashboard/', {'tipo_evento': 'ATTENUATION'}))
        self.assertEqual(otro.status_code, 200)

    def test_ranking_procesa_datos_ajax_html_y_fallback_optico(self):
        from .views.ranking import ranking
        parametros = {
            'anio': '2026', 'otu': 'OTU-ANALITICA', 'ruta': 'RUTA-A',
            'show_all_regulares': 'true', 'show_all_opticos': 'true',
        }
        with patch('mapas.views.ranking.connection.cursor', self._cursor()), \
             patch('mapas.views.ranking.contar_rutas_db', return_value=1):
            ajax = ranking(self._request('/ranking/', parametros, ajax=True))
        self.assertEqual(ajax.status_code, 200)
        datos = json.loads(ajax.content)
        self.assertEqual(datos['top_rutas_data'][0]['total'], 6)
        self.assertEqual(datos['top_opticos_data'][0]['otu'], 'N/A')

        with patch('mapas.views.ranking.connection.cursor', self._cursor(fallar_opticos=True)), \
             patch('mapas.views.ranking.contar_rutas_db', return_value=1), \
             patch('mapas.views.ranking.render', return_value=HttpResponse('RUTA-A')):
            html = ranking(self._request('/ranking/'))
        self.assertEqual(html.status_code, 200)
        self.assertContains(html, 'RUTA-A')

    def test_exportaciones_analiticas_generan_csv_filtrado(self):
        from .views.ranking import export_top_opticos_csv, export_top_rutas_csv
        parametros = {'anio': '2026', 'otu': 'OTU-A', 'ruta': 'RUTA-A', 'show_all_regulares': 'true'}
        with patch('mapas.views.ranking.connection.cursor', self._cursor()):
            rutas = export_top_rutas_csv(self._request('/export-csv/', parametros))
        self.assertEqual(rutas.status_code, 200)
        self.assertIn('RUTA-A', rutas.content.decode())

        parametros = {'otu': 'OTU-A', 'ruta': 'RUTA-A', 'show_all_opticos': 'true'}
        with patch('mapas.views.ranking.connection.cursor', self._cursor()):
            opticos = export_top_opticos_csv(self._request('/export-opticos-csv/', parametros))
        self.assertEqual(opticos.status_code, 200)
        self.assertIn('First_Connector', opticos.content.decode())


class _CursorGeograficoFalso:
    def __init__(self, fallar_alter=False):
        self.description = [('event_id',), ('distance_m',), ('event_type',)]
        self.fallar_alter = fallar_alter
        self.executadas = []
        self.multiples = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, consulta, parametros=None):
        self.executadas.append((consulta, parametros))
        if self.fallar_alter and str(consulta).strip().startswith('ALTER TABLE'):
            raise RuntimeError('columna existente')

    def executemany(self, consulta, datos):
        self.multiples.extend(datos)

    def fetchall(self):
        return [('1', 0.5, 'Splice'), ('2', 1.0, 'Fiber End')]


class GeorreferenciacionCoverageTests(TestCase):
    def test_geometria_kml_e_interpolacion(self):
        from .management.commands.georeferenciar_eventos import (
            ensure_dir_for_path, haversine, interpolate_on_route, load_kml_route, slerp_latlon,
        )
        with tempfile.TemporaryDirectory() as tmp:
            destino = f'{tmp}/sub/directorio/archivo.csv'
            ensure_dir_for_path(destino)
            kml = f'{tmp}/ruta.kml'
            with open(kml, 'w', encoding='utf-8') as archivo:
                archivo.write(
                    '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><LineString>'
                    '<coordinates>-69.0,-23.0,0 -69.01,-23.01,0 -69.02,-23.02,0</coordinates>'
                    '</LineString></Document></kml>'
                )
            points, acumulada, segmentos = load_kml_route(kml)
            self.assertEqual(len(points), 3)
            self.assertGreater(haversine(-69, -23, -69.01, -23.01), 0)
            self.assertEqual(slerp_latlon(-23, -69, -23, -69, 0.5), (-23, -69))
            self.assertEqual(interpolate_on_route(points, acumulada, segmentos, -1), points[0])
            self.assertEqual(interpolate_on_route(points, acumulada, segmentos, acumulada[-1] + 1, False), points[-1])
            self.assertNotEqual(interpolate_on_route(points, acumulada, segmentos, acumulada[-1] + 10, True), points[-1])
            interior = interpolate_on_route(points, acumulada, segmentos, acumulada[1] / 2)
            self.assertEqual(len(interior), 2)

            invalido = f'{tmp}/invalido.kml'
            with open(invalido, 'w', encoding='utf-8') as archivo:
                archivo.write('<kml><Document /></kml>')
            with self.assertRaises(ValueError):
                load_kml_route(invalido)

    def test_tablas_calibracion_unidades_y_eventos(self):
        from .management.commands.georeferenciar_eventos import (
            build_calibration, detect_col, guess_units_to_m, load_events, load_table,
            optical_to_geo_piecewise,
        )
        self.assertEqual(detect_col(['Distancia', 'Latitud'], ['dist']), 'Distancia')
        self.assertIsNone(detect_col(['A'], ['dist']))
        metros = guess_units_to_m(pd.Series([0.1, 1.0]))
        self.assertEqual(metros.iloc[-1], 1000)
        self.assertEqual(guess_units_to_m(pd.Series([500, 1000])).iloc[-1], 1000)

        calibracion = build_calibration(pd.DataFrame({
            'distancia_km': [1.0, 0.0, 2.0], 'latitud': [-23.01, -23.0, -23.02],
            'longitud': [-69.01, -69.0, -69.02],
        }))
        self.assertEqual(optical_to_geo_piecewise(calibracion, -1), (-23.0, -69.0))
        self.assertEqual(optical_to_geo_piecewise(calibracion, 3000), (-23.02, -69.02))
        lat, lon = optical_to_geo_piecewise(calibracion, 500)
        self.assertAlmostEqual(lat, -23.005)
        self.assertAlmostEqual(lon, -69.005)
        with self.assertRaises(ValueError):
            build_calibration(pd.DataFrame({'x': [1]}))

        eventos = load_events(pd.DataFrame({
            'distance_m': [0.1, 0.2, 0.3],
            'event_type': ['Splice', 'Fiber End', 'Posterior'],
        }))
        self.assertEqual(len(eventos), 2)
        self.assertEqual(eventos['optical_distance_m'].iloc[-1], 200)
        with self.assertRaises(ValueError):
            load_events(pd.DataFrame({'otra': [1]}))

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = f'{tmp}/datos.csv'
            pd.DataFrame({'a': [1]}).to_csv(csv_path, index=False)
            self.assertEqual(load_table(csv_path).iloc[0]['a'], 1)
        with patch('mapas.management.commands.georeferenciar_eventos.pd.read_excel', return_value=pd.DataFrame({'a': [2]})):
            self.assertEqual(load_table('datos.xlsx').iloc[0]['a'], 2)

    def test_helpers_de_base_y_persistencia_masiva(self):
        from .management.commands.georeferenciar_eventos import (
            ensure_output_table_exists, fetch_events_from_db, replace_measurement_for_enlace,
        )
        cursor = _CursorGeograficoFalso(fallar_alter=True)
        with patch('mapas.management.commands.georeferenciar_eventos.connection.cursor', return_value=cursor):
            tabla = fetch_events_from_db('RUTA-A')
            self.assertEqual(len(tabla), 2)
            ensure_output_table_exists()
        self.assertGreater(len(cursor.executadas), 5)

        salida = pd.DataFrame([{
            'prueba_id': 1, 'event_id': 'E1', 'event_type': 'Splice',
            'optical_distance_m': 100, 'latitude': -23, 'longitude': -69,
            'loss_db': float('nan'), 'real_cumulative_loss_db': 0.2,
        }])
        cursor = _CursorGeograficoFalso()
        with patch('mapas.management.commands.georeferenciar_eventos.ensure_output_table_exists'), \
             patch('mapas.management.commands.georeferenciar_eventos.connection.cursor', return_value=cursor):
            replace_measurement_for_enlace('RUTA-A', salida)
        self.assertEqual(len(cursor.multiples), 1)
        self.assertIsNone(cursor.multiples[0][10])

    def test_procesamiento_directorio_con_y_sin_calibracion(self):
        from .management.commands.georeferenciar_eventos import find_single_file, process_directory
        eventos = pd.DataFrame({
            'prueba_id': [1, 1], 'event_id': ['1', '2'], 'distance_m': [100.0, 200.0],
            'event_type': ['Splice', 'Fiber End'], 'loss_db': [0.2, 0.3],
        })
        with tempfile.TemporaryDirectory() as tmp:
            kml = f'{tmp}/ruta.kml'
            with open(kml, 'w', encoding='utf-8') as archivo:
                archivo.write('<kml><LineString><coordinates>-69,-23 -69.02,-23.02</coordinates></LineString></kml>')
            self.assertEqual(os.path.normpath(find_single_file(tmp, ['*.kml'])), os.path.normpath(kml))
            self.assertIsNone(find_single_file(tmp, ['*.inexistente']))
            with patch('mapas.management.commands.georeferenciar_eventos.fetch_events_from_db', return_value=eventos), \
                 patch('mapas.management.commands.georeferenciar_eventos.replace_measurement_for_enlace') as reemplazo:
                self.assertTrue(process_directory(tmp, 'RUTA-A', None, True, False, False, False))
                procesados = reemplazo.call_args.args[1]
                self.assertIn('latitude', procesados.columns)
                self.assertAlmostEqual(procesados['real_cumulative_loss_db'].iloc[-1], 0.5)

            pd.DataFrame({
                'distance': [0, 200], 'latitude': [-23, -23.02], 'longitude': [-69, -69.02],
            }).to_csv(f'{tmp}/calibracion.csv', index=False)
            with patch('mapas.management.commands.georeferenciar_eventos.fetch_events_from_db', return_value=eventos), \
                 patch('mapas.management.commands.georeferenciar_eventos.replace_measurement_for_enlace') as reemplazo:
                self.assertTrue(process_directory(tmp, 'RUTA-B', None, False, True, False, False))
                self.assertTrue(reemplazo.call_args.args[1]['segment_index'].isna().all())

        with tempfile.TemporaryDirectory() as vacio:
            self.assertFalse(process_directory(vacio, 'SIN-KML', None, True, False, False, False))

    def test_comando_recursivo_filtra_ruta_y_maneja_errores(self):
        from .management.commands.georeferenciar_eventos import Command
        comando = Command()
        comando.handle(root='Z:/ruta/no/existe', recursive=False, ruta=None)
        with tempfile.TemporaryDirectory() as tmp:
            for nombre in ('RUTA-A', 'RUTA-B'):
                os.makedirs(f'{tmp}/{nombre}', exist_ok=True)
                with open(f'{tmp}/{nombre}/ruta.kml', 'w', encoding='utf-8') as archivo:
                    archivo.write('<kml/>')
            with patch('mapas.management.commands.georeferenciar_eventos.process_directory', return_value=True) as procesar:
                comando.handle(root=tmp, recursive=True, ruta='RUTA-A')
                procesar.assert_called_once()
            with patch('mapas.management.commands.georeferenciar_eventos.process_directory', side_effect=RuntimeError('fallo')):
                comando.handle(root=tmp, recursive=False, ruta=None)


class OperacionInventarioAmpliadaCoverageTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin-operacion', 'operacion@example.test', 'clave-segura')
        self.client.force_login(self.admin)
        self.odf, _ = guardar_odf_normalizado(
            odf_nombre='ODF-OPERACION', hub_nombre='SITE-OPERACION',
            sala_nombre='SALA-OPERACION', rack_nombre='RACK-OPERACION',
            defaults={
                'capacidad_puertos': 24, 'puertos_ocupados': 1, 'puertos_libres': 23,
                'estado': 'Operativo', 'tipo_conector': 'SC/APC', 'observaciones': 'Principal',
            },
        )
        self.puerto = DetallePuertoODF.objects.create(
            odf_obj=self.odf, puerto_odf='1', bandeja='B1', fibra='F01',
            estado_puerto='Ocupado', tipo_conector='SC/APC', patchcord='Sí', destino='RUTA-OPERACION',
        )
        self.ruta = Ruta.objects.create(
            nombre='RUTA-OPERACION', distancia_m=1200, odf_origen=self.odf, odf_destino=self.odf,
        )
        self.tramo = InventarioTramo.objects.create(
            ruta=self.ruta, tramo_secuencia=1, hub_site='SITE-OPERACION', odf_nombre=self.odf.odf,
            origen='SITE-OPERACION', destino='DESTINO', estado='Operativo', capacidad='24',
            tipo_fibra='Monomodo', tipo_trazado='Subterráneo', marca_modelo='Cable-X',
        )
        self.fibra = InventarioFibra.objects.create(
            ruta=self.ruta, fibra_numero='F01', estado='Libre', nombre_fibra='=SERVICIO',
            origen_odf='', destino='DESTINO', tipo_conector='SC/APC',
        )
        self.elemento = Reserva.objects.create(
            ruta=self.ruta, tramo=self.tramo, nombre='MUFA-OPERACION', codigo='M-01', tipo='MUFA',
            reserva_m=30, latitud='-23.1', longitud='-69.2', orden_en_ruta=1, estado='CONFIRMADO',
        )

    def test_apis_paginadas_cubren_filtros_y_serializadores(self):
        fibras = self.client.get(reverse('api_fibras_paginadas'), {
            'q': 'SERVICIO', 'estado': 'Libre', 'page_size': 'invalido', 'page': 1,
        })
        self.assertEqual(fibras.status_code, 200)
        self.assertEqual(fibras.json()['data'][0]['origen'], self.odf.odf)

        elementos = self.client.get(reverse('api_elementos_paginados'), {
            'q': 'MUFA', 'tipo': 'MUFA', 'page_size': 25,
        })
        self.assertEqual(elementos.status_code, 200)
        self.assertEqual(elementos.json()['data'][0]['tramo'], 1)

        puertos = self.client.get(reverse('api_puertos_paginados'), {
            'q': 'ODF-OPERACION', 'estado': 'Ocupado', 'page_size': 200,
        })
        self.assertEqual(puertos.status_code, 200)
        self.assertEqual(puertos.json()['data'][0]['patchcord'], 'Sí')

    def test_exportaciones_xlsx_y_csv_protegen_formulas(self):
        for recurso in ('puertos', 'fibras', 'elementos'):
            respuesta = self.client.get(reverse('exportar_inventario', args=[recurso, 'xlsx']))
            self.assertEqual(respuesta.status_code, 200)
            self.assertTrue(respuesta.content.startswith(b'PK'))

        csv_fibras = self.client.get(reverse('exportar_inventario', args=['fibras', 'csv']))
        contenido = b''.join(csv_fibras.streaming_content).decode('utf-8-sig')
        self.assertIn("'=SERVICIO", contenido)
        self.assertEqual(
            self.client.get(reverse('exportar_inventario', args=['desconocido', 'csv'])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse('exportar_inventario', args=['fibras', 'pdf'])).status_code,
            404,
        )

    def test_busqueda_global_devuelve_todos_los_tipos_de_activo(self):
        corto = self.client.get(reverse('busqueda_global'), {'q': 'R'})
        self.assertEqual(corto.json()['data'], [])
        respuestas = []
        for termino in ('OPERACION', 'F01', 'MUFA'):
            respuestas.extend(self.client.get(reverse('busqueda_global'), {'q': termino}).json()['data'])
        tipos = {resultado['type'] for resultado in respuestas}
        self.assertTrue({'troncal', 'odf', 'puerto', 'fibra', 'site', 'elemento'}.issubset(tipos))

    def test_fichas_360_cubren_todos_los_activos(self):
        activos = {
            'troncal': self.ruta.pk,
            'odf': self.odf.pk,
            'puerto': self.puerto.pk,
            'fibra': self.fibra.pk,
            'site': self.odf.rack_obj.sala.hub_site.pk,
            'elemento': self.elemento.pk,
        }
        for tipo, pk in activos.items():
            respuesta = self.client.get(reverse('asset_360', args=[tipo, pk]))
            self.assertEqual(respuesta.status_code, 200, tipo)
            self.assertEqual(respuesta.json()['data']['type'], tipo)
        self.assertEqual(self.client.get(reverse('asset_360', args=['desconocido', 1])).status_code, 404)
