"""Regresión de las actualizaciones aprobadas; hooks no sustituyen pruebas PG."""
import io
import json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.query import QuerySet
from django.test import RequestFactory, TestCase
from django.test.runner import DiscoverRunner
from mapas.models import AuditoriaFibra, AuditoriaPuertoODF, FibraTramo, InventarioODF, DetallePuertoODF, HubSite, InventarioFibra, InventarioTramo, Reserva, Ruta
from mapas.tests_calidad_fibras import CalidadFixtureMixin
from mapas.views import importacion as imp
from mapas.views.inventario import create_odf_manual, create_ruta_manual, update_odf_manual, update_detalle_fibra, update_detalle_puerto, update_ruta_manual
from mapas.services.inventario import ajustar_puertos_a_capacidad, actualizar_odf
from mapas.services.fibras import establecer_estado_fibra_informado, restablecer_estado_fibra
from mapas.services.calidad import evaluar_completitud_fibra
from mapas.views.sites import crear_site_manual


def csv(value):
    return io.BytesIO(value.encode('utf-8'))


class AuditoriaActualizaciones(CalidadFixtureMixin, TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='audit-local')

    def request(self, payload, is_json=True):
        request = RequestFactory().post(
            '/', json.dumps(payload) if is_json else payload,
            content_type='application/json' if is_json else 'application/x-www-form-urlencoded',
        ) if is_json else RequestFactory().post('/', payload)
        request.user = self.user
        return request

    def test_01_crear_site_existente_no_borra_ubicacion(self):
        site = HubSite.objects.create(nombre='AUD-SITE', latitud=-12, longitud=-77, direccion='Direccion vigente')
        with patch('mapas.views.sites.messages.error'):
            response = crear_site_manual(self.request({'nombre': site.nombre}, False))
        self.assertEqual(response.status_code, 302)
        site.refresh_from_db()
        self.assertEqual(site.latitud, -12)
        self.assertEqual(site.longitud, -77)
        self.assertEqual(site.direccion, 'Direccion vigente')

    def test_02_sites_csv_preserva_omitidos(self):
        site = HubSite.objects.create(nombre='AUD-SITE', latitud=-12, longitud=-77, direccion='Direccion vigente')
        imp._procesar_sites_inventario(csv('Nombre,Direccion\nAUD-SITE,Nueva direccion\n'))
        site.refresh_from_db()
        self.assertEqual(site.latitud, -12)
        self.assertEqual(site.longitud, -77)
        self.assertEqual(site.direccion, 'Nueva direccion')

    def test_03_troncal_gui_conserva_contador_no_enviado(self):
        ruta = Ruta.objects.create(nombre='AUD-RUTA')
        tramo = InventarioTramo.objects.create(ruta=ruta, tramo_secuencia=1,
            capacidad_hilos=48, hilos_reservados=3, hilos_libres=45)
        # El JS conserva ocupados/libres pero no incluye hilos_reservados.
        response = update_ruta_manual(self.request({
            'nombre_original': ruta.nombre, 'nombre': ruta.nombre, 'capacidad': '48 Hilos',
            'hilos_ocupados': 0, 'hilos_libres': 45, 'estado': 'OPERATIVO',
        }))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(json.loads(response.content)['status'], 'success')
        tramo.refresh_from_db()
        self.assertEqual(tramo.hilos_reservados, 3)

    def test_04_troncal_error_revierte_nombre(self):
        ruta = Ruta.objects.create(nombre='AUD-RUTA')
        InventarioTramo.objects.create(ruta=ruta, tramo_secuencia=1, capacidad_hilos=48)
        response = update_ruta_manual(self.request({
            'nombre_original': ruta.nombre, 'nombre': 'AUD-RENOMBRADA', 'mufas': 'no-es-numero',
        }))
        self.assertEqual(response.status_code, 400)
        ruta.refresh_from_db()
        self.assertEqual(ruta.nombre, 'AUD-RUTA')

    def test_05_puertos_csv_no_reescribe_estado_ni_metadata_omitida(self):
        puerto = DetallePuertoODF.objects.create(odf_obj=self.odf_a, puerto_odf='1', patchcord='ANTERIOR')
        original = QuerySet.bulk_update
        def intercalar(queryset, objs, fields, **kwargs):
            if queryset.model is DetallePuertoODF:
                # Simula datos ya cambiados entre lectura y escritura.
                DetallePuertoODF.objects.filter(pk=puerto.pk).update(estado_puerto='RESERVADO', patchcord='NUEVO')
                self.assertNotIn('estado_puerto', fields)
                self.assertNotIn('patchcord', fields)
            return original(queryset, objs, fields, **kwargs)
        with patch.object(QuerySet, 'bulk_update', intercalar):
            imp._procesar_puertos_odf_inventario(csv(f'ODF,Puerto,Observaciones\n{self.odf_a.odf},1,Nota nueva\n'))
        puerto.refresh_from_db()
        self.assertEqual(puerto.estado_puerto, 'RESERVADO')
        self.assertEqual(puerto.patchcord, 'NUEVO')
        self.assertEqual(puerto.observaciones, 'Nota nueva')

    def test_06_puerto_gui_parche_conserva_estado_y_otras_notas(self):
        puerto = DetallePuertoODF.objects.create(odf_obj=self.odf_a, puerto_odf='1', estado_puerto='RESERVADO', patchcord='NUEVO')
        response = update_detalle_puerto(self.request({'id': puerto.pk, 'observaciones': 'Nota nueva'}))
        self.assertEqual(response.status_code, 200)
        puerto.refresh_from_db()
        self.assertEqual(puerto.estado_puerto, 'RESERVADO')
        self.assertEqual(puerto.patchcord, 'NUEVO')

    def test_07_fibra_formulario_antiguo_rechaza_conflicto(self):
        fibra = InventarioFibra.objects.create(fibra_numero='F1', estado='OCUPADO', origen_estado='INFORMADO')
        # Snapshot de GUI abierto antes: Disponible; el usuario solo edita nota.
        response = update_detalle_fibra(self.request({'id': fibra.pk, 'estado': 'DISPONIBLE', '_original': {'estado': 'DISPONIBLE'}, 'observaciones': 'Nota nueva'}))
        self.assertEqual(response.status_code, 400, response.content)
        fibra.refresh_from_db()
        self.assertEqual(fibra.estado, 'OCUPADO')

    def test_08_fibra_parche_omite_estado_y_lo_conserva(self):
        fibra = InventarioFibra.objects.create(fibra_numero='F1', estado='OCUPADO', origen_estado='INFORMADO')
        response = update_detalle_fibra(self.request({'id': fibra.pk, 'observaciones': 'Nota nueva'}))
        self.assertEqual(response.status_code, 200)
        fibra.refresh_from_db()
        self.assertEqual(fibra.estado, 'OCUPADO')

    def test_09_reservas_csv_conserva_cambio_en_campo_omitido(self):
        ruta = Ruta.objects.create(nombre='AUD-RUTA')
        reserva = Reserva.objects.create(ruta=ruta, codigo='AUD-RES', nombre='Reserva', tipo='MUFA', latitud=-12, longitud=-77, reserva_m=10)
        original = Reserva.save
        def intercalar(obj, *args, **kwargs):
            Reserva.objects.filter(pk=obj.pk).update(reserva_m=22)
            return original(obj, *args, **kwargs)
        with patch.object(Reserva, 'save', intercalar):
            imp._procesar_reservas(csv('Ruta,Codigo,Nombre,Tipo,Latitud,Longitud\nAUD-RUTA,AUD-RES,Reserva,MUFA,-12,-77\n'))
        reserva.refresh_from_db()
        self.assertEqual(reserva.reserva_m, 22)

    def test_10_troncal_csv_preserva_campos_omitidos(self):
        ruta = Ruta.objects.create(nombre='AUD-RUTA', olt='SITE-A', distancia_m=1500)
        imp._procesar_ruta_otu(csv('Ruta,OLT\nAUD-RUTA,SITE-B\n'))
        ruta.refresh_from_db()
        self.assertEqual(ruta.olt, 'SITE-B')
        self.assertEqual(ruta.distancia_m, 1500)

    def test_estado_global_no_altera_recorrido_uno_dos_cuatro_tramos(self):
        for cantidad in (1, 2, 4):
            fibra, _ = self._recorrido(f'ESTADO-{cantidad}', cantidad)
            antes = list(fibra.asignaciones_tramo.values_list('pk', 'numero_hilo', 'estado'))
            establecer_estado_fibra_informado(fibra=fibra, estado='OCUPADO')
            restablecer_estado_fibra(fibra=fibra)
            self.assertEqual(list(fibra.asignaciones_tramo.values_list('pk', 'numero_hilo', 'estado')), antes)

    def test_cambiar_global_sin_asignacion_no_crea_fibratramo(self):
        ruta = Ruta.objects.create(nombre='GLOBAL-SIN-DETALLE')
        InventarioTramo.objects.create(ruta=ruta, tramo_secuencia=1, capacidad_hilos=48)
        cabecera = 'Codigo Fibra,Fibra,Ruta,Estado\n'
        for estado in ('Disponible', 'Ocupado', 'Reservado', 'Sin informacion'):
            imp._procesar_fibras_globales(csv(cabecera + f'FGF-SIN-DETALLE,F17,{ruta.nombre},{estado}\n'))
            self.assertFalse(FibraTramo.objects.filter(tramo__ruta=ruta).exists())

    def test_numeracion_unica_troncal_y_provisionales_permitidos(self):
        ruta = Ruta.objects.create(nombre='NUMEROS-UNICOS')
        InventarioFibra.objects.create(ruta=ruta, fibra_numero='F17')
        with self.assertRaises(ValidationError):
            InventarioFibra.objects.create(ruta=ruta, fibra_numero='F17')
        with self.assertRaises(IntegrityError), transaction.atomic():
            InventarioFibra.objects.bulk_create([InventarioFibra(ruta=ruta, fibra_numero='f17')])
        for _ in range(2):
            InventarioFibra.objects.create(fibra_numero='F17')
        InventarioFibra.objects.create(ruta=Ruta.objects.create(nombre='OTRA-TRONCAL'), fibra_numero='F17')

    def test_odf_capacidad_falla_sin_guardar_metadatos(self):
        ajustar_puertos_a_capacidad(self.odf_a, 20)
        DetallePuertoODF.objects.filter(odf_obj=self.odf_a, puerto_odf='20').update(observaciones='Conservar')
        previo = self.odf_a.tipo_conector
        respuesta = update_odf_manual(self.request({'id': self.odf_a.pk, 'capacidad_puertos': 4,
            'tipo_conector': 'LC', 'confirmar_capacidad': True}))
        self.assertEqual(respuesta.status_code, 400)
        self.odf_a.refresh_from_db()
        self.assertEqual(self.odf_a.capacidad_puertos, 20)
        self.assertEqual(self.odf_a.tipo_conector, previo)
        self.assertEqual(self.odf_a.puertos_detalle.count(), 20)

    def test_odf_reducir_csv_y_auditar(self):
        ajustar_puertos_a_capacidad(self.odf_a, 20)
        imp._procesar_odfs_inventario(csv(f'ODF,Capacidad\n{self.odf_a.odf},4\n'))
        self.odf_a.refresh_from_db()
        self.assertEqual((self.odf_a.capacidad_puertos, self.odf_a.puertos_libres), (4, 4))
        evento = AuditoriaPuertoODF.objects.get(accion='AJUSTAR_CAPACIDAD')
        self.assertEqual(evento.metadatos['capacidad_anterior'], 20)
        self.assertEqual(len(evento.metadatos['puertos_retirados']), 16)

    def test_solo_administrador_y_confirmacion_para_capacidad(self):
        ajustar_puertos_a_capacidad(self.odf_a, 20)
        request = self.request({'id': self.odf_a.pk, 'capacidad_puertos': 24})
        self.assertEqual(update_odf_manual(request).status_code, 400)
        usuario = get_user_model().objects.create_user(username='operador')
        with self.assertRaisesMessage(ValidationError, 'administrador'):
            actualizar_odf(self.odf_a, {'capacidad_puertos': 24}, usuario=usuario)
        self.odf_a.refresh_from_db()
        self.assertEqual(self.odf_a.capacidad_puertos, 20)

    def test_puerto_especial_no_genera_adicionales_y_admite_editar_nota(self):
        DetallePuertoODF.objects.create(odf_obj=self.odf_a, puerto_odf='X1')
        with self.assertRaisesMessage(ValidationError, 'especial'):
            ajustar_puertos_a_capacidad(self.odf_a, 4)
        self.assertEqual(self.odf_a.puertos_detalle.count(), 1)
        respuesta = update_odf_manual(self.request({'id': self.odf_a.pk, 'observaciones': 'Nueva'}))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.odf_a.puertos_detalle.count(), 1)

    def test_puertos_1_y_01_no_se_fusionan(self):
        for numero in ('1', '01'):
            DetallePuertoODF.objects.create(odf_obj=self.odf_a, puerto_odf=numero)
        with self.assertRaisesMessage(ValidationError, 'misma posición'):
            ajustar_puertos_a_capacidad(self.odf_a, 4)
        self.assertEqual(self.odf_a.puertos_detalle.count(), 2)

    def test_contadores_csv_cuentan_puertos_no_capacidad(self):
        DetallePuertoODF.objects.create(odf_obj=self.odf_a, puerto_odf='1')
        imp._procesar_puertos_odf_inventario(csv(f'ODF,Puerto,Observaciones\n{self.odf_a.odf},1,Nota\n'))
        self.odf_a.refresh_from_db()
        self.assertEqual((self.odf_a.capacidad_puertos, self.odf_a.puertos_libres), (20, 1))

    def test_odf_sin_ubicacion_gui_crea_puertos_y_no_duplica_al_completar(self):
        response = create_odf_manual(self.request({'odf': 'ODF-PENDIENTE', 'capacidad_puertos': 4}))
        self.assertEqual(response.status_code, 200, response.content)
        odf = InventarioODF.objects.get(odf='ODF-PENDIENTE')
        puertos = list(odf.puertos_detalle.values_list('pk', flat=True))
        self.assertEqual(len(puertos), 4)
        response = update_odf_manual(self.request({'id': odf.pk, 'hub_site': self.site_a.nombre,
            'sala': self.odf_a.rack_obj.sala.nombre, 'rack': self.odf_a.rack_obj.nombre}))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(list(odf.puertos_detalle.values_list('pk', flat=True)), puertos)

    def test_calidad_no_certifica_recorrido_desconocido_ni_bloquea_datos(self):
        fibra, tramos = self._recorrido('CALIDAD-PENDIENTE', 1)
        tramo = tramos[0]
        tramo.origen_nodo = tramo.destino_nodo = None
        tramo.origen = tramo.destino = ''
        tramo.save()
        self._terminar(fibra, 'A', self.odf_a, '1')
        self._terminar(fibra, 'B', self.odf_b, '1')
        resultado = evaluar_completitud_fibra(fibra)
        self.assertFalse(resultado['valido'])
        self.assertTrue(resultado['pendiente_verificar'])
        self.assertEqual(resultado['nivel'], 'ADVERTENCIA')
        self.assertEqual(fibra.terminaciones.count(), 2)

    def test_auditoria_movimiento_csv_sin_evento_ficticio_en_recarga(self):
        fibra, tramos = self._recorrido('AUD-POSICION', 1)
        contenido = f'Ruta,Codigo Fibra,Fibra\n{fibra.ruta.nombre},{fibra.codigo_fibra},F13\n'
        imp._procesar_fibras_inventario(csv(contenido))
        evento = AuditoriaFibra.objects.get(fibra=fibra, accion='ASIGNAR_TRAMO')
        self.assertEqual(evento.metadatos['anterior']['numero_hilo'], 'F12')
        self.assertEqual(evento.metadatos['nuevo']['numero_hilo'], 'F13')
        imp._procesar_fibras_inventario(csv(contenido))
        self.assertEqual(AuditoriaFibra.objects.filter(fibra=fibra, accion='ASIGNAR_TRAMO').count(), 1)

    def test_admin_detecta_formulario_desactualizado(self):
        from django.contrib import admin
        site = HubSite.objects.create(nombre='ADMIN-SEGURO', direccion='Anterior')
        formulario = admin.site._registry[HubSite].get_form(self.request({}), site, fields=['nombre', 'direccion'])
        original = formulario(instance=site).initial['revision_inventario']
        HubSite.objects.filter(pk=site.pk).update(direccion='Cambio posterior')
        bound = formulario({'nombre': site.nombre, 'direccion': 'Mi edicion', 'revision_inventario': original}, instance=site)
        self.assertFalse(bound.is_valid())
        self.assertIn('cambió', str(bound.errors))

    def test_crear_troncal_invalida_no_deja_ruta_parcial(self):
        response = create_ruta_manual(self.request({'nombre': 'NO-PARCIAL', 'mufas': 'invalido'}))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Ruta.objects.filter(nombre='NO-PARCIAL').exists())

    def test_editor_troncal_usa_distancia_exacta_no_redondeada(self):
        from mapas.services.edicion import valores_edicion_troncal
        ruta = Ruta.objects.create(nombre='DISTANCIA-EXACTA', distancia_m=1234.567)
        original = valores_edicion_troncal(ruta, [])
        self.assertAlmostEqual(original['distancia_km'], 1.234567)
        response = update_ruta_manual(self.request({
            'nombre_original': ruta.nombre, 'distancia_km': '2.345', '_original': original,
        }))
        self.assertEqual(response.status_code, 200)
        ruta.refresh_from_db()
        self.assertEqual(ruta.distancia_m, 2345)

    def test_asignacion_explicita_gui_guarda_usuario_en_auditoria(self):
        from mapas.services.fibras import asignar_fibra_a_tramos
        fibra, tramos = self._recorrido('AUD-ASIGNACION-GUI', 1, asignados=0)
        tramo = tramos[0]
        asignar_fibra_a_tramos(fibra=fibra, tramos=[tramo], numero_hilo='F17',
                              estado='DISPONIBLE', usuario=self.user)
        evento = AuditoriaFibra.objects.get(fibra=fibra, accion='ASIGNAR_TRAMO')
        self.assertEqual(evento.usuario_id, self.user.pk)
        self.assertEqual(evento.origen, 'GUI')

    def test_terminaciones_rechaza_numero_repetido_en_troncal_sin_escribir(self):
        ajustar_puertos_a_capacidad(self.odf_a, 20)
        ruta = Ruta.objects.create(nombre='TERMINACIONES-UNICO')
        contenido = ('Fibra,Ruta,ODF A,Puerto A,ODF B,Puerto B\n'
                     f'F17,{ruta.nombre},{self.odf_a.odf},1,,\n'
                     f'F17,{ruta.nombre},{self.odf_a.odf},2,,\n')
        antes = InventarioFibra.objects.count()
        with self.assertRaisesRegex(ValueError, 'fila 3.*repetido'):
            imp._procesar_terminaciones_fibra(csv(contenido))
        self.assertEqual(InventarioFibra.objects.count(), antes)

    def test_vistas_y_admin_renderizan_sin_cambiar_dashboard(self):
        self.client.force_login(self.user)
        for url in ('/inventario/interno/', '/inventario/planta-interna/',
                    '/inventario/planta-externa/', '/inventario/externo/',
                    f'/admin/mapas/inventarioodf/{self.odf_a.pk}/change/'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, (url, response.status_code))
