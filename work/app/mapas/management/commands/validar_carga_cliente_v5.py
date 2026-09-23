from pathlib import Path
import re
import unicodedata

import pandas as pd
from django.core.management.base import BaseCommand, CommandError


def leer(path):
    return pd.read_csv(path, encoding='utf-8-sig', dtype=str).fillna('')


def normalizar_columna(nombre):
    texto = unicodedata.normalize('NFKD', str(nombre or '').strip())
    texto = ''.join(
        caracter for caracter in texto
        if not unicodedata.combining(caracter)
    )
    return re.sub(r'[^a-z0-9]+', '_', texto.casefold()).strip('_')


def normalizar(df, aliases=None):
    aliases = aliases or {}
    return df.rename(columns={
        columna: aliases.get(
            normalizar_columna(columna),
            normalizar_columna(columna),
        )
        for columna in df.columns
    })


def capacidad_entera(valor):
    texto = str(valor or '').strip()
    if not texto:
        return None
    coincidencia = re.fullmatch(
        r'(\d+)(?:[.,]0+)?(?:\s*(?:hilos?|fibras?))?',
        texto,
        flags=re.IGNORECASE,
    )
    return int(coincidencia.group(1)) if coincidencia else None


def normalizar_tipo_nodo(valor):
    texto = unicodedata.normalize('NFKD', str(valor or '').strip())
    texto = ''.join(
        caracter
        for caracter in texto
        if not unicodedata.combining(caracter)
    )
    texto = texto.upper().replace(' ', '_')
    return {
        'CAMARA_DE_EMPAME': 'CAMARA',
        'CAMARA_DE_EMPALME': 'CAMARA',
        'CAJA_EMPAME': 'CAJA_EMPALME',
        'CAJA_DE_EMPAME': 'CAJA_EMPALME',
        'CAJA_DE_EMPALME': 'CAJA_EMPALME',
    }.get(texto, texto)


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

        def archivo(nombre, *, requerido=True):
            candidatos = sorted(raiz.rglob(nombre), key=lambda item: item.stat().st_size, reverse=True)
            if not candidatos:
                if requerido:
                    raise CommandError(f'No se encontró: {nombre}')
                return None
            return candidatos[0]

        rutas_path = archivo(
            'Cargar Troncales y Datos Generales.csv',
            requerido=False,
        )
        if rutas_path is None:
            # Mantiene compatibles los paquetes preparados con el nombre
            # anterior de esta importación.
            rutas_path = archivo(
                'Cargar Troncales y Metadatos.csv',
                requerido=False,
            )
        fibras_path = archivo('Cargar Detalle de Fibras - Hilos.csv')
        trazado_path = archivo('Cargar Trazado de Troncales Unificado.csv')
        puertos_path = archivo('Cargar Detalle de Puertos ODF.csv')
        odfs_path = archivo('Cargar Inventario de ODFs.csv')
        reservas_path = archivo('Cargar Reservas - Landmarks.csv')
        tecnicos_path = archivo('Cargar Detalles Tecnicos Complementarios.csv')
        sites_path = archivo('Cargar Hubs - Sites.csv')

        fibras = leer(fibras_path)
        trazado = leer(trazado_path)
        if rutas_path:
            rutas = leer(rutas_path)
        else:
            if 'ruta' not in trazado.columns:
                raise CommandError(
                    'Trazado: falta la columna ruta y no existe un archivo '
                    'opcional de Troncales y Datos Generales.'
                )
            rutas = pd.DataFrame({
                'Ruta': sorted(
                    set(trazado['ruta'].str.strip()) - {''}
                )
            })
            self.stdout.write(self.style.WARNING(
                'ADVERTENCIA: no se encontró '
                'Cargar Troncales y Datos Generales.csv '
                '(ni su nombre anterior Cargar Troncales y Metadatos.csv); '
                'se usará el catálogo de rutas del trazado geográfico.'
            ))
        puertos = leer(puertos_path)
        odfs = leer(odfs_path)
        reservas = leer(reservas_path)
        tecnicos = leer(tecnicos_path)
        sites = leer(sites_path)
        fibras_normalizadas = normalizar(fibras, aliases={
            'troncal': 'ruta',
            'codigo_de_tramo': 'codigo_tramo',
            'tipo_de_servicio': 'tipo_servicio',
            'servicio': 'tipo_servicio',
            'tipo_de_conector': 'conector',
            'tipo_conector': 'conector',
            'codigo_de_fibra': 'codigo_fibra',
        })
        tecnicos_normalizados = normalizar(tecnicos, aliases={
            'ident': 'ruta',
            'troncal': 'ruta',
            'distancia': 'distancia_m',
            'codigo_de_tramo': 'codigo_tramo',
            'numero_de_tramo': 'secuencia',
        })

        problemas = []

        def exigir(df, columnas, etiqueta):
            faltantes = [columna for columna in columnas if columna not in df.columns]
            if faltantes:
                problemas.append(f'{etiqueta}: faltan columnas {faltantes}')

        exigir(rutas, ['Ruta'], 'Rutas')
        exigir(
            fibras_normalizadas,
            [
                'ruta', 'fibra', 'estado', 'tipo_servicio',
                'origen', 'destino', 'conector',
            ],
            'Fibras',
        )
        exigir(trazado, ['ruta', 'latitude', 'longitude'], 'Trazado')
        exigir(puertos, ['hub_site', 'odf', 'puerto_odf'], 'Puertos')
        exigir(odfs, ['hub_site', 'sala', 'rack', 'odf', 'capacidad_puertos'], 'ODF')
        exigir(reservas, ['Enlace'], 'Reservas')
        exigir(tecnicos_normalizados, ['ruta'], 'Técnicos')
        exigir(sites, ['nombre'], 'Sites')
        columnas_tecnicas_nuevas = {
            'codigo_tramo', 'secuencia', 'origen_tipo', 'origen_codigo',
            'destino_tipo', 'destino_codigo',
        }
        tecnicos_nuevos = bool(
            set(tecnicos_normalizados.columns) & columnas_tecnicas_nuevas
        )
        if tecnicos_nuevos:
            exigir(
                tecnicos_normalizados,
                [
                    'ruta', 'codigo_tramo', 'secuencia',
                    'origen_tipo', 'origen_codigo',
                    'destino_tipo', 'destino_codigo',
                    'estado', 'distancia_m', 'capacidad', 'tipo_fibra',
                ],
                'Técnicos Fase 2',
            )
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
            'Fibras': set(fibras_normalizadas['ruta'].str.strip()),
            'Trazado': set(trazado['ruta'].str.strip()),
            'Técnicos': set(tecnicos_normalizados['ruta'].str.strip()),
            'Reservas': set(reservas['Enlace'].str.strip()) - {''},
        }
        for etiqueta, valores in relaciones_ruta.items():
            huerfanas = valores - rutas_catalogo
            if huerfanas:
                problemas.append(f'{etiqueta}: {len(huerfanas)} rutas no existen en el catálogo')

        tramos_por_ruta = {}
        capacidades_tramo = {}
        if tecnicos_nuevos:
            tipos_nodo = {
                'SITE', 'ODF', 'MUFA', 'CAMARA', 'POSTE',
                'CAJA_EMPALME', 'PUNTO', 'OTRO',
            }
            codigos_vistos = set()
            secuencias_vistas = set()
            filas_por_ruta = {}
            nombres_nodo = {}
            for numero, fila in tecnicos_normalizados.iterrows():
                ruta_nombre = str(fila.get('ruta', '')).strip()
                codigo = str(fila.get('codigo_tramo', '')).strip().upper()
                origen_tipo = normalizar_tipo_nodo(
                    fila.get('origen_tipo', '')
                )
                destino_tipo = normalizar_tipo_nodo(
                    fila.get('destino_tipo', '')
                )
                origen_codigo = str(
                    fila.get('origen_codigo', '')
                ).strip().upper()
                destino_codigo = str(
                    fila.get('destino_codigo', '')
                ).strip().upper()
                for tipo_nodo, codigo_nodo, campo_nombre in (
                    (origen_tipo, origen_codigo, 'origen_nombre'),
                    (destino_tipo, destino_codigo, 'destino_nombre'),
                ):
                    nombre_nodo = str(
                        fila.get(campo_nombre, '')
                    ).strip()
                    if not codigo_nodo or not nombre_nodo:
                        continue
                    clave_nodo = (
                        tipo_nodo,
                        codigo_nodo.casefold(),
                    )
                    nombre_anterior = nombres_nodo.get(clave_nodo)
                    if (
                        nombre_anterior
                        and nombre_anterior.casefold()
                        != nombre_nodo.casefold()
                    ):
                        problemas.append(
                            f'Técnicos fila {numero + 2}: nombres '
                            f'incoherentes para el nodo '
                            f'{tipo_nodo}/{codigo_nodo}'
                        )
                    else:
                        nombres_nodo[clave_nodo] = nombre_nodo
                try:
                    secuencia = int(str(fila.get('secuencia', '')).strip())
                except ValueError:
                    problemas.append(
                        f'Técnicos fila {numero + 2}: secuencia inválida'
                    )
                    continue
                if not codigo:
                    problemas.append(
                        f'Técnicos fila {numero + 2}: codigo_tramo vacío'
                    )
                if origen_tipo not in tipos_nodo or destino_tipo not in tipos_nodo:
                    problemas.append(
                        f'Técnicos fila {numero + 2}: tipo de nodo inválido'
                    )
                if not origen_codigo or not destino_codigo:
                    problemas.append(
                        f'Técnicos fila {numero + 2}: extremo sin código'
                    )
                if (
                    origen_tipo == destino_tipo
                    and origen_codigo.casefold() == destino_codigo.casefold()
                ):
                    problemas.append(
                        f'Técnicos fila {numero + 2}: extremos iguales'
                    )
                clave_codigo = (ruta_nombre.casefold(), codigo.casefold())
                clave_secuencia = (ruta_nombre.casefold(), secuencia)
                if clave_codigo in codigos_vistos:
                    problemas.append(
                        f'Técnicos: código duplicado {ruta_nombre}/{codigo}'
                    )
                if clave_secuencia in secuencias_vistas:
                    problemas.append(
                        f'Técnicos: secuencia duplicada '
                        f'{ruta_nombre}/{secuencia}'
                    )
                codigos_vistos.add(clave_codigo)
                secuencias_vistas.add(clave_secuencia)
                capacidad_texto = str(fila.get('capacidad', '')).strip()
                capacidad = capacidad_entera(capacidad_texto)
                if capacidad_texto and capacidad is None:
                    problemas.append(
                        f'Técnicos fila {numero + 2}: capacidad inválida'
                    )
                elif capacidad == 0:
                    problemas.append(
                        f'Técnicos fila {numero + 2}: capacidad debe ser '
                        'positiva o vacía'
                    )
                conteos = []
                for campo in (
                    'hilos_ocupados', 'hilos_reservados', 'hilos_libres'
                ):
                    texto = str(fila.get(campo, '')).strip()
                    if not texto:
                        conteos.append(0)
                        continue
                    try:
                        valor = int(texto)
                    except ValueError:
                        problemas.append(
                            f'Técnicos fila {numero + 2}: {campo} inválido'
                        )
                        valor = 0
                    if valor < 0:
                        problemas.append(
                            f'Técnicos fila {numero + 2}: {campo} negativo'
                        )
                    conteos.append(valor)
                if capacidad is not None and sum(conteos) > capacidad:
                    problemas.append(
                        f'Técnicos fila {numero + 2}: contadores exceden '
                        'la capacidad'
                    )
                filas_por_ruta.setdefault(ruta_nombre, []).append({
                    'secuencia': secuencia,
                    'codigo': codigo,
                    'origen': (origen_tipo, origen_codigo.casefold()),
                    'destino': (destino_tipo, destino_codigo.casefold()),
                })
                capacidades_tramo[
                    (ruta_nombre.casefold(), codigo.casefold())
                ] = capacidad

            for ruta_nombre, filas in filas_por_ruta.items():
                ordenadas = sorted(
                    filas, key=lambda fila: fila['secuencia']
                )
                secuencias = [
                    fila['secuencia'] for fila in ordenadas
                ]
                if secuencias != list(range(1, max(secuencias) + 1)):
                    problemas.append(
                        f'Técnicos {ruta_nombre}: secuencias no consecutivas'
                    )
                for anterior, siguiente in zip(
                    ordenadas, ordenadas[1:]
                ):
                    if anterior['destino'] != siguiente['origen']:
                        problemas.append(
                            f'Técnicos {ruta_nombre}: discontinuidad entre '
                            f"{anterior['codigo']} y {siguiente['codigo']}"
                        )
                tramos_por_ruta[ruta_nombre.casefold()] = [
                    fila['codigo'] for fila in ordenadas
                ]
        else:
            inconsistencias_legacy = []
            for numero, fila in tecnicos_normalizados.iterrows():
                ruta_nombre = str(fila.get('ruta', '')).strip()
                tramos_por_ruta[ruta_nombre.casefold()] = ['']
                capacidad = capacidad_entera(fila.get('capacidad', ''))
                capacidades_tramo[(ruta_nombre.casefold(), '')] = capacidad
                conteos = []
                for campo in ('hilos_ocupados', 'hilos_libres'):
                    texto = str(fila.get(campo, '')).strip()
                    if not texto:
                        conteos.append(0)
                        continue
                    try:
                        valor_decimal = float(texto.replace(',', '.'))
                        if (
                            valor_decimal < 0
                            or not valor_decimal.is_integer()
                        ):
                            raise ValueError
                        valor = int(valor_decimal)
                    except ValueError:
                        problemas.append(
                            f'Técnicos fila {numero + 2}: '
                            f'{campo} inválido'
                        )
                        valor = 0
                    conteos.append(valor)
                if capacidad and sum(conteos) > capacidad:
                    inconsistencias_legacy.append(
                        (
                            ruta_nombre,
                            capacidad,
                            conteos[0],
                            conteos[1],
                        )
                    )
            if inconsistencias_legacy:
                muestra = ', '.join(
                    nombre
                    for nombre, _, _, _ in inconsistencias_legacy[:10]
                )
                self.stdout.write(self.style.WARNING(
                    'ADVERTENCIA: Técnicos legacy contiene '
                    f'{len(inconsistencias_legacy)} ruta(s) cuyos contadores '
                    'ocupados+libres exceden la capacidad declarada: '
                    f'{muestra}. Se permite por compatibilidad, pero estos '
                    'contadores no demuestran disponibilidad extremo a '
                    'extremo.'
                ))

        estados_fibra = {
            'disponible': 'DISPONIBLE',
            'ocupado': 'OCUPADO',
            'reservado': 'RESERVADO',
            'sin informacion': 'SIN_INFORMACION',
            'sin información': 'SIN_INFORMACION',
            'malo': 'SIN_INFORMACION',
        }
        fibras_vistas = set()
        fibras_logicas_por_tramo = set()
        fibras_logicas_esperadas = set()
        datos_fibra_logica = {}
        for numero, fila in fibras_normalizadas.iterrows():
            ruta_nombre = str(fila.get('ruta', '')).strip()
            fibra = str(fila.get('fibra', '')).strip().upper()
            codigo_fibra = str(
                fila.get('codigo_fibra', '')
            ).strip().upper()
            estado = str(fila.get('estado', '')).strip().casefold()
            codigo = str(
                fila.get('codigo_tramo', '')
            ).strip().upper()
            codigos_ruta = tramos_por_ruta.get(
                ruta_nombre.casefold(), []
            )
            if estado not in estados_fibra:
                problemas.append(
                    f'Fibras fila {numero + 2}: estado inválido'
                )
            if not re.fullmatch(r'F[1-9]\d*', fibra):
                problemas.append(
                    f'Fibras fila {numero + 2}: Fibra debe usar F<n>'
                )
                continue
            if codigo_fibra and re.fullmatch(r'F[1-9]\d*', codigo_fibra):
                problemas.append(
                    f'Fibras fila {numero + 2}: Codigo Fibra debe ser una '
                    'identidad estable distinta de la posición F<n>'
                )
                continue
            if not codigo:
                if len(codigos_ruta) == 1:
                    codigo = codigos_ruta[0]
                elif len(codigos_ruta) > 1:
                    problemas.append(
                        f'Fibras fila {numero + 2}: Codigo Tramo '
                        'obligatorio para una ruta con varios tramos'
                    )
                    continue
            elif codigo.casefold() not in {
                valor.casefold() for valor in codigos_ruta
            }:
                problemas.append(
                    f'Fibras fila {numero + 2}: tramo inexistente'
                )
                continue
            clave = (
                ruta_nombre.casefold(), codigo.casefold(), fibra.casefold()
            )
            if clave in fibras_vistas:
                problemas.append(
                    f'Fibras fila {numero + 2}: hilo duplicado en el tramo'
                )
            fibras_vistas.add(clave)
            clave_logica = (
                ruta_nombre.casefold(),
                (codigo_fibra or fibra).casefold(),
            )
            clave_logica_tramo = (
                ruta_nombre.casefold(),
                codigo.casefold(),
                (codigo_fibra or fibra).casefold(),
            )
            if clave_logica_tramo in fibras_logicas_por_tramo:
                problemas.append(
                    f'Fibras fila {numero + 2}: Codigo Fibra repetido '
                    'en el mismo tramo'
                )
            fibras_logicas_por_tramo.add(clave_logica_tramo)
            fibras_logicas_esperadas.add(clave_logica)
            globales = {
                campo: str(fila.get(campo, '')).strip().casefold()
                for campo in (
                    'tipo_servicio', 'origen', 'destino', 'conector'
                )
            }
            anteriores = datos_fibra_logica.get(clave_logica)
            if anteriores and anteriores != globales:
                diferentes = sorted(
                    campo
                    for campo in globales
                    if anteriores.get(campo) != globales[campo]
                )
                problemas.append(
                    f'Fibras fila {numero + 2}: datos globales '
                    f'incoherentes para {codigo_fibra}: '
                    + ', '.join(diferentes)
                )
            else:
                datos_fibra_logica[clave_logica] = globales
            capacidad = capacidades_tramo.get(
                (ruta_nombre.casefold(), codigo.casefold())
            )
            if capacidad and int(fibra[1:]) > capacidad:
                problemas.append(
                    f'Fibras fila {numero + 2}: {fibra} excede '
                    f'la capacidad {capacidad}'
                )

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
            if rutas_path:
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
                FibraTramo,
                InventarioFibra,
                InventarioODF,
                InventarioTramo,
                Reserva,
                Ruta,
            )
            if Ruta.objects.count() != len(rutas_catalogo):
                raise CommandError('La cantidad cargada de rutas no coincide con el archivo.')
            if InventarioODF.objects.count() != len(odfs):
                raise CommandError('La cantidad cargada de ODF no coincide con el archivo.')
            if DetallePuertoODF.objects.count() != len(puertos):
                raise CommandError('La cantidad cargada de puertos no coincide con el archivo.')
            if CoordenadaRuta.objects.count() != len(trazado):
                raise CommandError('La cantidad cargada de coordenadas no coincide con el archivo.')
            if InventarioFibra.objects.count() != len(
                fibras_logicas_esperadas
            ):
                raise CommandError(
                    'La cantidad de fibras lógicas no coincide con el archivo.'
                )
            if FibraTramo.objects.count() != len(fibras):
                raise CommandError(
                    'La cantidad de fibras por tramo no coincide con el archivo.'
                )
            if Reserva.objects.count() != len(reservas):
                raise CommandError('La cantidad cargada de reservas no coincide con el archivo.')
            if (
                CoordenadaRuta.objects.filter(tipo_trazado__isnull=True).exists()
                or CoordenadaRuta.objects.filter(tipo_trazado='').exists()
            ):
                raise CommandError(
                    'Quedaron coordenadas sin tipo de trazado geográfico.'
                )
            for ruta_id in CoordenadaRuta.objects.values_list(
                'ruta_id', flat=True
            ).distinct():
                primera = (
                    CoordenadaRuta.objects.filter(ruta_id=ruta_id)
                    .order_by('orden', 'pk')
                    .first()
                )
                if primera and not primera.inicio_segmento:
                    raise CommandError(
                        f'La ruta {primera.ruta.nombre} no marca el inicio '
                        'de su primer segmento geográfico.'
                    )
            rutas_tecnicas = set(
                tecnicos_normalizados['ruta'].str.strip()
            ) - {''}
            faltantes_tecnicos = [
                nombre for nombre in rutas_tecnicas
                if not InventarioTramo.objects.filter(
                    ruta__nombre__iexact=nombre
                ).exists()
            ]
            if faltantes_tecnicos:
                raise CommandError(
                    f'Quedaron {len(faltantes_tecnicos)} rutas sin inventario '
                    'técnico después de cargar el archivo complementario.'
                )
            self.stdout.write(self.style.SUCCESS(
                'Carga completa V5, geografía e inventario técnico verificados.'
            ))

        self.stdout.write(self.style.SUCCESS('Validación V5 completada sin errores relacionales.'))
