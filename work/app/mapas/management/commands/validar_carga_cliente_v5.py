from pathlib import Path

import pandas as pd
from django.core.management.base import BaseCommand, CommandError


def leer(path):
    return pd.read_csv(path, encoding='utf-8-sig', dtype=str).fillna('')


class Command(BaseCommand):
    help = 'Valida relaciones de la carga V5 de MEL y opcionalmente ejecuta la carga completa.'

    def add_arguments(self, parser):
        parser.add_argument('--directorio', required=True)
        parser.add_argument('--cargar-prueba', action='store_true')
        parser.add_argument('--permitir-sites-pendientes', action='store_true')

    def handle(self, *args, **options):
        raiz = Path(options['directorio']).resolve()
        if not raiz.exists():
            raise CommandError(f'No existe el directorio: {raiz}')

        def archivo(nombre):
            candidatos = sorted(raiz.rglob(nombre), key=lambda item: item.stat().st_size, reverse=True)
            if not candidatos:
                raise CommandError(f'No se encontró: {nombre}')
            return candidatos[0]

        rutas_path = archivo('Cargar Troncales y Metadatos.csv')
        fibras_path = archivo('Cargar Detalle de Fibras - Hilos.csv')
        trazado_path = archivo('Cargar Trazado de Troncales Unificado.csv')
        puertos_path = archivo('Cargar Detalle de Puertos ODF.csv')
        odfs_path = archivo('Cargar Inventario de ODFs.csv')
        reservas_path = archivo('Cargar Reservas - Landmarks.csv')
        tecnicos_path = archivo('Cargar Detalles Tecnicos Complementarios.csv')
        sites_path = archivo('Cargar Hubs - Sites.csv')

        rutas = leer(rutas_path)
        fibras = leer(fibras_path)
        trazado = leer(trazado_path)
        puertos = leer(puertos_path)
        odfs = leer(odfs_path)
        reservas = leer(reservas_path)
        tecnicos = leer(tecnicos_path)
        sites = leer(sites_path)

        problemas = []

        def exigir(df, columnas, etiqueta):
            faltantes = [columna for columna in columnas if columna not in df.columns]
            if faltantes:
                problemas.append(f'{etiqueta}: faltan columnas {faltantes}')

        exigir(rutas, ['Ruta'], 'Rutas')
        exigir(fibras, ['Ruta', 'Fibra'], 'Fibras')
        exigir(trazado, ['ruta', 'latitude', 'longitude'], 'Trazado')
        exigir(puertos, ['hub_site', 'odf', 'puerto_odf'], 'Puertos')
        exigir(odfs, ['hub_site', 'sala', 'rack', 'odf', 'capacidad_puertos'], 'ODF')
        exigir(reservas, ['Enlace'], 'Reservas')
        exigir(tecnicos, ['ruta'], 'Técnicos')
        exigir(sites, ['nombre'], 'Sites')
        if problemas:
            raise CommandError('; '.join(problemas))

        for columna in ['hub_site', 'sala', 'rack', 'odf']:
            cantidad = int((odfs[columna].str.strip() == '').sum())
            if cantidad:
                problemas.append(f'ODF: {cantidad} filas sin {columna}')
        for columna in ['hub_site', 'odf', 'puerto_odf']:
            cantidad = int((puertos[columna].str.strip() == '').sum())
            if cantidad:
                problemas.append(f'Puertos: {cantidad} filas sin {columna}')

        duplicados_odf = int(odfs.duplicated(['hub_site', 'sala', 'rack', 'odf'], keep=False).sum())
        duplicados_puerto = int(puertos.duplicated(['hub_site', 'odf', 'puerto_odf'], keep=False).sum())
        if duplicados_odf:
            problemas.append(f'ODF: {duplicados_odf} filas duplicadas por ubicación/nombre')
        if duplicados_puerto:
            problemas.append(f'Puertos: {duplicados_puerto} filas duplicadas')

        claves_odf = set(zip(odfs['hub_site'].str.strip(), odfs['odf'].str.strip()))
        claves_puerto = set(zip(puertos['hub_site'].str.strip(), puertos['odf'].str.strip()))
        puertos_huerfanos = claves_puerto - claves_odf
        if puertos_huerfanos:
            problemas.append(f'Puertos: {len(puertos_huerfanos)} ODF referenciados no existen')

        sites_oficiales = set(sites['nombre'].str.strip())
        hubs_huerfanos = set(odfs['hub_site'].str.strip()) - sites_oficiales
        if hubs_huerfanos:
            mensaje = (
                f"ODF: {len(hubs_huerfanos)} hubs no existen en el catálogo de sites: "
                f"{', '.join(sorted(hubs_huerfanos))}"
            )
            if options['permitir_sites_pendientes']:
                self.stdout.write(self.style.WARNING('ADVERTENCIA: ' + mensaje))
            else:
                problemas.append(mensaje)

        rutas_catalogo = set(rutas['Ruta'].str.strip())
        relaciones_ruta = {
            'Fibras': set(fibras['Ruta'].str.strip()),
            'Trazado': set(trazado['ruta'].str.strip()),
            'Técnicos': set(tecnicos['ruta'].str.strip()),
            'Reservas': set(reservas['Enlace'].str.strip()) - {''},
        }
        for etiqueta, valores in relaciones_ruta.items():
            huerfanas = valores - rutas_catalogo
            if huerfanas:
                problemas.append(f'{etiqueta}: {len(huerfanas)} rutas no existen en el catálogo')

        capacidades = odfs.set_index(['hub_site', 'odf'])['capacidad_puertos'].apply(
            lambda value: int(float(value)) if str(value).strip() else 0
        )
        conteos = puertos.groupby(['hub_site', 'odf']).size()
        excedidos = [
            clave for clave, cantidad in conteos.items()
            if clave in capacidades.index and capacidades.loc[clave] and cantidad > capacidades.loc[clave]
        ]
        if excedidos:
            problemas.append(f'Puertos: {len(excedidos)} ODF exceden su capacidad declarada')

        self.stdout.write(f'Rutas: {len(rutas)}')
        self.stdout.write(f'Sites: {len(sites)}')
        self.stdout.write(f'ODF: {len(odfs)}')
        self.stdout.write(f'Puertos ODF: {len(puertos)}')
        self.stdout.write(f'Fibras/Hilos: {len(fibras)}')
        self.stdout.write(f'Coordenadas: {len(trazado)}')
        self.stdout.write(f'Reservas/Landmarks: {len(reservas)}')

        if problemas:
            raise CommandError(' | '.join(problemas))

        if options['cargar_prueba']:
            from mapas.views.importacion import (
                _procesar_coordenadas_csv,
                _procesar_fibras_inventario,
                _procesar_odfs_inventario,
                _procesar_puertos_odf_inventario,
                _procesar_reservas,
                _procesar_ruta_otu,
                _procesar_tramos_inventario,
            )
            with rutas_path.open('rb') as archivo_rutas:
                _procesar_ruta_otu(archivo_rutas)
            with odfs_path.open('rb') as archivo_odfs:
                _procesar_odfs_inventario(archivo_odfs)
            with puertos_path.open('rb') as archivo_puertos:
                _procesar_puertos_odf_inventario(archivo_puertos)
            with trazado_path.open('rb') as archivo_trazado:
                _procesar_coordenadas_csv(archivo_trazado)
            with tecnicos_path.open('rb') as archivo_tecnicos:
                _procesar_tramos_inventario(archivo_tecnicos)
            with fibras_path.open('rb') as archivo_fibras:
                _procesar_fibras_inventario(archivo_fibras)
            with reservas_path.open('rb') as archivo_reservas:
                _procesar_reservas(archivo_reservas)

            from mapas.models import (
                CoordenadaRuta,
                DetallePuertoODF,
                InventarioFibra,
                InventarioODF,
                Reserva,
                Ruta,
            )
            if Ruta.objects.count() != len(rutas):
                raise CommandError('La cantidad cargada de rutas no coincide con el archivo.')
            if InventarioODF.objects.count() != len(odfs):
                raise CommandError('La cantidad cargada de ODF no coincide con el archivo.')
            if DetallePuertoODF.objects.count() != len(puertos):
                raise CommandError('La cantidad cargada de puertos no coincide con el archivo.')
            if CoordenadaRuta.objects.count() != len(trazado):
                raise CommandError('La cantidad cargada de coordenadas no coincide con el archivo.')
            if InventarioFibra.objects.count() != len(fibras):
                raise CommandError('La cantidad cargada de fibras no coincide con el archivo.')
            if Reserva.objects.count() != len(reservas):
                raise CommandError('La cantidad cargada de reservas no coincide con el archivo.')
            if CoordenadaRuta.objects.filter(tramo__isnull=True).exists():
                raise CommandError('Quedaron coordenadas segmentadas sin tramo relacionado.')
            self.stdout.write(self.style.SUCCESS('Carga completa V5 y relaciones principales verificadas.'))

        self.stdout.write(self.style.SUCCESS('Validación V5 completada sin errores relacionales.'))
