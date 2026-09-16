"""Regresiones de cabeceras: ningún dato desconocido se descarta en silencio."""
import io
from pathlib import Path
import zipfile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from openpyxl import Workbook

from .models import HubSite, Ruta, Reserva, CoordenadaRuta, InventarioFibra, InventarioTramo
from .services.importacion_puertos import leer_operaciones_excel
from .views import importacion


def archivo(texto):
    return io.BytesIO(texto.encode('utf-8-sig'))


def excel(encabezados, valores, hoja='Operaciones'):
    libro = Workbook()
    libro.active.title = hoja
    libro.active.append(encabezados)
    libro.active.append(valores)
    contenido = io.BytesIO()
    libro.save(contenido)
    libro.close()
    return contenido.getvalue()


class LectorCabecerasTests(SimpleTestCase):
    def test_rechaza_duplicadas_originales_antes_del_renombrado_de_pandas(self):
        for cabecera in (
            'Nombre,Direccion,Direccion',
            'Nombre,Dirección, direccion ',
            'Nombre,Direccion,Direccion.1,Direccion',
        ):
            with self.subTest(cabecera=cabecera):
                with self.assertRaisesRegex(ValueError, 'encabezados equivalentes repetidos'):
                    importacion._leer_csv_normalizado(archivo(cabecera + '\nA,B,C,D\n'))

    def test_rechaza_aliases_equivalentes(self):
        with self.assertRaisesRegex(ValueError, 'repetidos: nombre'):
            importacion._leer_csv_normalizado(
                archivo('Nombre,Site\nA,B\n'), aliases={'site': 'nombre'},
            )

    def test_desconocida_sugiere_sin_corregir_automaticamente(self):
        with self.assertRaisesRegex(ValueError, "direcion.*direccion"):
            importacion._leer_csv_normalizado(
                archivo('Nombre,Direcion\nSITE-A,Oficina\n'),
                columnas_permitidas={'nombre', 'direccion'},
            )

    def test_rechaza_columna_sin_nombre_aunque_tenga_datos(self):
        with self.assertRaisesRegex(ValueError, 'columna 2 no tiene encabezado'):
            importacion._leer_csv_normalizado(archivo('Nombre,\nSITE-A,Oficina\n'))

    def test_conserva_bom_comillas_saltos_y_aliases_validos(self):
        df = importacion._leer_csv_normalizado(
            archivo('\n  \n"Site","Dirección"\r\nSITE-A,"Oficina, norte\nPiso 2"\r\n'),
            aliases={'site': 'nombre'}, columnas_permitidas={'nombre', 'direccion'},
        )
        self.assertEqual(list(df.columns), ['nombre', 'direccion'])
        self.assertEqual(df.iloc[0]['direccion'], 'Oficina, norte\nPiso 2')

    def test_csv_vacio_tiene_error_legible(self):
        with self.assertRaisesRegex(ValueError, 'no contiene encabezados'):
            importacion._leer_csv_normalizado(archivo(' \n\n'))

    def test_excel_operaciones_rechaza_desconocidas_y_duplicadas(self):
        for extra, mensaje in (('Sincronisar fibra', 'no reconocidas'), ('ODF', 'repetidas')):
            with self.subTest(extra=extra):
                contenido = excel(['ODF', 'Puerto', 'Accion', extra], ['O', '1', 'Reservar', 'Sí'])
                with self.assertRaisesRegex(ValidationError, mensaje):
                    leer_operaciones_excel(contenido)

    def test_excel_rechaza_datos_sin_cabecera_pero_admite_columna_vacia(self):
        for valor in ('Dato sin cabecera', None):
            contenido = excel(['ODF', 'Puerto', 'Accion', None], ['O', '1', 'Reservar', valor])
            if valor:
                with self.assertRaisesRegex(ValidationError, 'sin encabezado'):
                    leer_operaciones_excel(contenido)
            else:
                self.assertEqual(len(leer_operaciones_excel(contenido)), 1)


class ImportadoresCabecerasTests(TestCase):
    def test_errata_o_duplicada_no_actualiza_ni_crea_sites(self):
        site = HubSite.objects.create(nombre='SITE-A', direccion='Original')
        for contenido in (
            'Nombre,Direcion\nSITE-A,Modificada\nSITE-B,Nueva\n',
            'Nombre,Direccion,Direccion\nSITE-A,Modificada,Otra\n',
        ):
            with self.subTest(contenido=contenido), self.assertRaises(ValueError):
                importacion._procesar_sites_inventario(archivo(contenido))
            site.refresh_from_db()
            self.assertEqual(site.direccion, 'Original')
            self.assertEqual(HubSite.objects.count(), 1)

    def test_todos_los_procesadores_csv_rechazan_columnas_desconocidas(self):
        for tipo, (procesador, _) in importacion.obtener_procesadores_importacion().items():
            if tipo in {'coordenadas_rutas', 'coordenadas_inventario'}:
                continue
            with self.subTest(tipo=tipo), self.assertRaisesRegex(ValueError, 'no_reconocida'):
                procesador(archivo('Ruta,No reconocida\nRUTA-A,No perder\n'))

    def test_tramos_tecnicos_rechazan_columna_desconocida(self):
        with self.assertRaisesRegex(ValueError, 'no_reconocida'):
            importacion._procesar_tramos_inventario(archivo(
                'Ruta,Secuencia,Origen,Destino,No reconocida\nR,1,A,B,dato\n'
            ))

    def test_columna_geografica_legacy_omitida_deja_advertencia_visible(self):
        Ruta.objects.create(nombre='R-GEO')
        entrada = SimpleUploadedFile('mapa.csv', (
            'Ruta,Latitude,Longitude,hub_site\n'
            'R-GEO,-23,-70,SITE-X\nR-GEO,-23.1,-70.1,SITE-X\n'
        ).encode())
        lote, resultado = importacion.ejecutar_importacion_sincrona_auditada(
            entrada, 'coordenadas_csv', None, importacion._procesar_coordenadas_csv,
        )
        self.assertEqual(lote.estado, 'COMPLETADO')
        self.assertEqual(CoordenadaRuta.objects.count(), 2)
        self.assertIn('hub_site no se guardó', lote.detalle_errores[0])
        self.assertEqual(lote.detalle_errores, resultado['advertencias'])

    def test_fibras_por_tramo_legacy_conserva_compatibilidad_con_advertencia(self):
        ruta = Ruta.objects.create(nombre='R-LEGACY')
        InventarioTramo.objects.create(ruta=ruta, tramo_secuencia=1, capacidad_hilos=24)
        fibra = InventarioFibra.objects.create(
            ruta=ruta, codigo_fibra='FGF-LEGACY-1', fibra_numero='F1',
            nombre_fibra='Servicio original', tipo_conector='SC/APC',
        )
        entrada = SimpleUploadedFile('fibras.csv', (
            'Ruta,Fibra,Codigo Fibra,Estado,Tipo de Servicio,Origen,Destino,Conector\n'
            'R-LEGACY,F1,FGF-LEGACY-1,Ocupado,Servicio diferente,ODF-A,ODF-B,LC\n'
        ).encode())
        lote, resultado = importacion.ejecutar_importacion_sincrona_auditada(
            entrada, 'fibras_inventario', None, importacion._procesar_fibras_inventario,
        )
        self.assertEqual(lote.estado, 'COMPLETADO')
        self.assertEqual(resultado['creadas'], 1)
        self.assertIn('tipo_de_servicio', lote.detalle_errores[0])
        self.assertIn('no aplicadas', lote.detalle_errores[0])
        fibra.refresh_from_db()
        self.assertEqual(fibra.nombre_fibra, 'Servicio original')
        self.assertEqual(fibra.tipo_conector, 'SC/APC')
        self.assertFalse(fibra.terminaciones.exists())

    def test_zip_rechaza_duplicada_o_desconocida_y_revierte_archivo_anterior(self):
        Ruta.objects.create(nombre='RUTA-A')
        Ruta.objects.create(nombre='RUTA-B')
        for procesador in (importacion._procesar_coordenadas_zip, importacion._procesar_coordenadas_inventario_zip):
            for cabecera in ('latitude,longitude,latitude', 'latitude,longitude,nota_desconocida'):
                with self.subTest(procesador=procesador.__name__, cabecera=cabecera):
                    contenido = io.BytesIO()
                    with zipfile.ZipFile(contenido, 'w') as paquete:
                        paquete.writestr('RUTA-A.csv', 'latitude,longitude\n-23,-70\n-23.1,-70.1\n')
                        paquete.writestr('RUTA-B.csv', cabecera + '\n-24,-71,-24\n-24.1,-71.1,-24.1\n')
                    contenido.seek(0)
                    with self.assertRaises(ValueError):
                        procesador(contenido)
                    self.assertFalse(CoordenadaRuta.objects.exists())

    def test_plantillas_y_ejemplos_principales_no_tienen_columnas_desconocidas(self):
        ejemplos = {
            'sites_inventario': ['plantilla_sites.csv', 'sites_ejemplo.csv'],
            'odf_inventario': ['plantilla_odfs.csv', 'odfs_ejemplo.csv'],
            'fibras_globales': ['plantilla_fibras_globales.csv', 'fibras_globales_ejemplo.csv'],
            'terminaciones_fibra': ['plantilla_terminaciones_simplificada.csv', 'terminaciones_fibra_ejemplo.csv'],
            'ruta_otu': ['plantilla_troncales.csv', 'rutas_ejemplo.csv'],
            'tramos_inventario': ['plantilla_tramos.csv', 'inventario_tramos_ejemplo.csv'],
            'fibras_inventario': ['plantilla_fibras_por_tramo.csv', 'fibras_por_tramo_ejemplo.csv'],
            'coordenadas_csv': ['plantilla_recorridos_mapa.csv', 'coordenadas_unificadas_ejemplo.csv'],
            'puertos_odf_inventario': ['plantilla_datos_puertos.csv', 'datos_puertos_ejemplo.csv'],
            'reservas': ['plantilla_reservas_elementos.csv', 'reservas_ejemplo.csv'],
        }
        registro = importacion.obtener_procesadores_importacion()
        for tipo, nombres in ejemplos.items():
            for nombre in nombres:
                with self.subTest(tipo=tipo, archivo=nombre):
                    with (Path(settings.BASE_DIR) / 'mapas/static/ejemplos' / nombre).open('rb') as entrada:
                        try:
                            registro[tipo][0](entrada)
                        except ValueError as exc:
                            # Las referencias de ejemplo no tienen que existir en
                            # esta BD, pero sus cabeceras sí deben ser compatibles.
                            self.assertNotIn('Columnas no reconocidas', str(exc))
                            self.assertNotIn('encabezados equivalentes repetidos', str(exc))

    def test_ejemplo_otu_legacy_ambiguo_no_se_interpreta_por_adivinacion(self):
        # La compatibilidad antigua sigue rechazando nombres equivalentes.
        with self.assertRaisesRegex(ValueError, 'repetidos: otu'):
            importacion._procesar_equipos_otu(archivo('Descripcion,OTU,Puerto\nA,B,1\n'))

    def test_ejemplo_otu_actual_se_importa_completo(self):
        ruta = Path(settings.BASE_DIR) / 'mapas/static/ejemplos/equipos_otu_ejemplo.csv'
        with ruta.open('rb') as entrada:
            resultado = importacion._procesar_equipos_otu(entrada)
        self.assertEqual(resultado['total'], 3)


class AdaptadoresCabecerasTests(TestCase):
    def setUp(self):
        usuario = get_user_model().objects.create_superuser('cabeceras-admin', password='test-only')
        self.client.force_login(usuario)
        self.ruta = Ruta.objects.create(nombre='RUTA-CABECERAS')

    def test_reservas_csv_y_excel_no_ocultan_encabezados_repetidos(self):
        encabezados = ['Nombre', 'Tipo', 'Latitud', 'Longitud', 'Nombre']
        valores = ['RESERVA-A', 'MUFA', '-23', '-70', 'RESERVA-B']
        for extension, contenido in (
            ('csv', (','.join(encabezados) + '\n' + ','.join(valores) + '\n').encode()),
            ('xlsx', excel(encabezados, valores)),
        ):
            with self.subTest(extension=extension):
                respuesta = self.client.post(reverse('api_import_reservas'), {
                    'ruta_nombre': self.ruta.nombre,
                    'archivo': SimpleUploadedFile(f'reservas.{extension}', contenido),
                })
                self.assertEqual(respuesta.status_code, 400)
                self.assertIn('repetidos', respuesta.json()['message'])
                self.assertFalse(Reserva.objects.exists())

    def test_fibras_csv_no_oculta_duplicadas_antes_de_enviar_al_motor(self):
        respuesta = self.client.post(reverse('api_import_fibras'), {
            'ruta_nombre': self.ruta.nombre,
            'csv_fibras': SimpleUploadedFile('fibras.csv', b'Fibra,Fibra\nF1,F2\n'),
        })
        self.assertEqual(respuesta.status_code, 400)
        self.assertIn('repetidos', respuesta.json()['message'])
