from collections import defaultdict

import django.db.models.deletion
from django.db import migrations, models
from django.db.models.functions import Coalesce, Lower


SQLITE_TRIGGERS = (
    """
    CREATE TRIGGER trg_fg_fibratramo_insert_integridad
    BEFORE INSERT ON inv_fibras_tramos
    BEGIN
      SELECT CASE WHEN NEW.fibra_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM inv_tramos t
        JOIN inv_fibras_detalle f ON f.id = NEW.fibra_id
        WHERE t.id = NEW.tramo_id AND t.ruta_id = f.ruta_id
      ) THEN RAISE(ABORT, 'FibraTramo cruza rutas') END;
      SELECT CASE WHEN (
        SELECT capacidad_hilos FROM inv_tramos WHERE id = NEW.tramo_id
      ) IS NOT NULL AND CAST(SUBSTR(NEW.numero_hilo, 2) AS INTEGER) > (
        SELECT capacidad_hilos FROM inv_tramos WHERE id = NEW.tramo_id
      ) THEN RAISE(ABORT, 'FibraTramo supera capacidad') END;
    END
    """,
    """
    CREATE TRIGGER trg_fg_fibratramo_update_integridad
    BEFORE UPDATE ON inv_fibras_tramos
    BEGIN
      SELECT CASE WHEN NEW.fibra_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM inv_tramos t
        JOIN inv_fibras_detalle f ON f.id = NEW.fibra_id
        WHERE t.id = NEW.tramo_id AND t.ruta_id = f.ruta_id
      ) THEN RAISE(ABORT, 'FibraTramo cruza rutas') END;
      SELECT CASE WHEN (
        SELECT capacidad_hilos FROM inv_tramos WHERE id = NEW.tramo_id
      ) IS NOT NULL AND CAST(SUBSTR(NEW.numero_hilo, 2) AS INTEGER) > (
        SELECT capacidad_hilos FROM inv_tramos WHERE id = NEW.tramo_id
      ) THEN RAISE(ABORT, 'FibraTramo supera capacidad') END;
    END
    """,
    """
    CREATE TRIGGER trg_fg_coordenada_insert_integridad
    BEFORE INSERT ON inv_rutas_coordenadas
    WHEN NEW.tramo_id IS NOT NULL
    BEGIN
      SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM inv_tramos t
        WHERE t.id = NEW.tramo_id
          AND t.ruta_id = NEW.ruta_id
          AND t.tramo_secuencia = NEW.tramo_secuencia
      ) THEN RAISE(ABORT, 'Coordenada y tramo incoherentes') END;
    END
    """,
    """
    CREATE TRIGGER trg_fg_coordenada_update_integridad
    BEFORE UPDATE ON inv_rutas_coordenadas
    WHEN NEW.tramo_id IS NOT NULL
    BEGIN
      SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM inv_tramos t
        WHERE t.id = NEW.tramo_id
          AND t.ruta_id = NEW.ruta_id
          AND t.tramo_secuencia = NEW.tramo_secuencia
      ) THEN RAISE(ABORT, 'Coordenada y tramo incoherentes') END;
    END
    """,
    """
    CREATE TRIGGER trg_fg_reserva_insert_integridad
    BEFORE INSERT ON inv_reservas_landmarks
    WHEN NEW.tramo_id IS NOT NULL
    BEGIN
      SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM inv_tramos t
        WHERE t.id = NEW.tramo_id AND t.ruta_id = NEW.ruta_id
      ) THEN RAISE(ABORT, 'Reserva y tramo cruzan rutas') END;
    END
    """,
    """
    CREATE TRIGGER trg_fg_reserva_update_integridad
    BEFORE UPDATE ON inv_reservas_landmarks
    WHEN NEW.tramo_id IS NOT NULL
    BEGIN
      SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM inv_tramos t
        WHERE t.id = NEW.tramo_id AND t.ruta_id = NEW.ruta_id
      ) THEN RAISE(ABORT, 'Reserva y tramo cruzan rutas') END;
    END
    """,
    """
    CREATE TRIGGER trg_fg_tramo_insert_integridad
    BEFORE INSERT ON inv_tramos
    BEGIN
      SELECT CASE WHEN NEW.origen_nodo_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM inv_tramos anterior
        WHERE anterior.ruta_id = NEW.ruta_id
          AND anterior.tramo_secuencia = NEW.tramo_secuencia - 1
          AND anterior.destino_nodo_id IS NOT NULL
          AND anterior.destino_nodo_id <> NEW.origen_nodo_id
      ) THEN RAISE(ABORT, 'Topologia de tramos no continua') END;
      SELECT CASE WHEN NEW.destino_nodo_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM inv_tramos siguiente
        WHERE siguiente.ruta_id = NEW.ruta_id
          AND siguiente.tramo_secuencia = NEW.tramo_secuencia + 1
          AND siguiente.origen_nodo_id IS NOT NULL
          AND siguiente.origen_nodo_id <> NEW.destino_nodo_id
      ) THEN RAISE(ABORT, 'Topologia de tramos no continua') END;
    END
    """,
    """
    CREATE TRIGGER trg_fg_tramo_update_integridad
    BEFORE UPDATE ON inv_tramos
    BEGIN
      SELECT CASE WHEN NEW.origen_nodo_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM inv_tramos anterior
        WHERE anterior.id <> NEW.id
          AND anterior.ruta_id = NEW.ruta_id
          AND anterior.tramo_secuencia = NEW.tramo_secuencia - 1
          AND anterior.destino_nodo_id IS NOT NULL
          AND anterior.destino_nodo_id <> NEW.origen_nodo_id
      ) THEN RAISE(ABORT, 'Topologia de tramos no continua') END;
      SELECT CASE WHEN NEW.destino_nodo_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM inv_tramos siguiente
        WHERE siguiente.id <> NEW.id
          AND siguiente.ruta_id = NEW.ruta_id
          AND siguiente.tramo_secuencia = NEW.tramo_secuencia + 1
          AND siguiente.origen_nodo_id IS NOT NULL
          AND siguiente.origen_nodo_id <> NEW.destino_nodo_id
      ) THEN RAISE(ABORT, 'Topologia de tramos no continua') END;
      SELECT CASE WHEN NEW.capacidad_hilos IS NOT NULL AND EXISTS (
        SELECT 1 FROM inv_fibras_tramos ft
        WHERE ft.tramo_id = NEW.id
          AND CAST(SUBSTR(ft.numero_hilo, 2) AS INTEGER) > NEW.capacidad_hilos
      ) THEN RAISE(ABORT, 'Capacidad deja fibras fuera') END;
      SELECT CASE WHEN NEW.ruta_id <> OLD.ruta_id AND EXISTS (
        SELECT 1
        FROM inv_fibras_tramos ft
        JOIN inv_fibras_detalle f ON f.id = ft.fibra_id
        WHERE ft.tramo_id = NEW.id AND f.ruta_id <> NEW.ruta_id
      ) THEN RAISE(ABORT, 'Mover tramo cruza fibras') END;
      SELECT CASE WHEN NEW.ruta_id <> OLD.ruta_id AND EXISTS (
        SELECT 1 FROM inv_rutas_coordenadas c
        WHERE c.tramo_id = NEW.id AND c.ruta_id <> NEW.ruta_id
      ) THEN RAISE(ABORT, 'Mover tramo cruza coordenadas') END;
      SELECT CASE WHEN NEW.ruta_id <> OLD.ruta_id AND EXISTS (
        SELECT 1 FROM inv_reservas_landmarks r
        WHERE r.tramo_id = NEW.id AND r.ruta_id <> NEW.ruta_id
      ) THEN RAISE(ABORT, 'Mover tramo cruza reservas') END;
    END
    """,
    """
    CREATE TRIGGER trg_fg_fibra_update_integridad
    BEFORE UPDATE OF ruta_id ON inv_fibras_detalle
    WHEN NEW.ruta_id <> OLD.ruta_id
    BEGIN
      SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM inv_fibras_tramos ft
        JOIN inv_tramos t ON t.id = ft.tramo_id
        WHERE ft.fibra_id = NEW.id AND t.ruta_id <> NEW.ruta_id
      ) THEN RAISE(ABORT, 'Mover fibra cruza tramos') END;
    END
    """,
)


POSTGRES_TRIGGERS = (
    """
    CREATE FUNCTION fg_validar_fibratramo() RETURNS trigger AS $$
    DECLARE capacidad integer;
    BEGIN
      IF NEW.numero_hilo !~ '^F[1-9][0-9]*$' THEN
        RAISE EXCEPTION 'Formato de hilo inválido' USING ERRCODE = '23514';
      END IF;
      IF NEW.fibra_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM inv_tramos t
        JOIN inv_fibras_detalle f ON f.id = NEW.fibra_id
        WHERE t.id = NEW.tramo_id AND t.ruta_id = f.ruta_id
      ) THEN
        RAISE EXCEPTION 'FibraTramo cruza rutas' USING ERRCODE = '23514';
      END IF;
      SELECT capacidad_hilos INTO capacidad
      FROM inv_tramos WHERE id = NEW.tramo_id;
      IF capacidad IS NOT NULL
         AND substring(NEW.numero_hilo FROM 2)::integer > capacidad THEN
        RAISE EXCEPTION 'FibraTramo supera capacidad' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER trg_fg_fibratramo_integridad
    BEFORE INSERT OR UPDATE ON inv_fibras_tramos
    FOR EACH ROW EXECUTE FUNCTION fg_validar_fibratramo()
    """,
    """
    CREATE FUNCTION fg_validar_coordenada() RETURNS trigger AS $$
    BEGIN
      IF NEW.tramo_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM inv_tramos t
        WHERE t.id = NEW.tramo_id
          AND t.ruta_id = NEW.ruta_id
          AND t.tramo_secuencia = NEW.tramo_secuencia
      ) THEN
        RAISE EXCEPTION 'Coordenada y tramo incoherentes' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER trg_fg_coordenada_integridad
    BEFORE INSERT OR UPDATE ON inv_rutas_coordenadas
    FOR EACH ROW EXECUTE FUNCTION fg_validar_coordenada()
    """,
    """
    CREATE FUNCTION fg_validar_reserva() RETURNS trigger AS $$
    BEGIN
      IF NEW.tramo_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM inv_tramos t
        WHERE t.id = NEW.tramo_id AND t.ruta_id = NEW.ruta_id
      ) THEN
        RAISE EXCEPTION 'Reserva y tramo cruzan rutas' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER trg_fg_reserva_integridad
    BEFORE INSERT OR UPDATE ON inv_reservas_landmarks
    FOR EACH ROW EXECUTE FUNCTION fg_validar_reserva()
    """,
    """
    CREATE FUNCTION fg_validar_tramo() RETURNS trigger AS $$
    BEGIN
      IF NEW.origen_nodo_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM inv_tramos anterior
        WHERE anterior.id <> COALESCE(NEW.id, -1)
          AND anterior.ruta_id = NEW.ruta_id
          AND anterior.tramo_secuencia = NEW.tramo_secuencia - 1
          AND anterior.destino_nodo_id IS NOT NULL
          AND anterior.destino_nodo_id <> NEW.origen_nodo_id
      ) THEN
        RAISE EXCEPTION 'Topología de tramos no continua' USING ERRCODE = '23514';
      END IF;
      IF NEW.destino_nodo_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM inv_tramos siguiente
        WHERE siguiente.id <> COALESCE(NEW.id, -1)
          AND siguiente.ruta_id = NEW.ruta_id
          AND siguiente.tramo_secuencia = NEW.tramo_secuencia + 1
          AND siguiente.origen_nodo_id IS NOT NULL
          AND siguiente.origen_nodo_id <> NEW.destino_nodo_id
      ) THEN
        RAISE EXCEPTION 'Topología de tramos no continua' USING ERRCODE = '23514';
      END IF;
      IF TG_OP = 'UPDATE' AND NEW.capacidad_hilos IS NOT NULL AND EXISTS (
        SELECT 1 FROM inv_fibras_tramos ft
        WHERE ft.tramo_id = NEW.id
          AND substring(ft.numero_hilo FROM 2)::integer > NEW.capacidad_hilos
      ) THEN
        RAISE EXCEPTION 'Capacidad deja fibras fuera' USING ERRCODE = '23514';
      END IF;
      IF TG_OP = 'UPDATE' AND NEW.ruta_id <> OLD.ruta_id AND (
        EXISTS (
          SELECT 1 FROM inv_fibras_tramos ft
          JOIN inv_fibras_detalle f ON f.id = ft.fibra_id
          WHERE ft.tramo_id = NEW.id AND f.ruta_id <> NEW.ruta_id
        )
        OR EXISTS (
          SELECT 1 FROM inv_rutas_coordenadas c
          WHERE c.tramo_id = NEW.id AND c.ruta_id <> NEW.ruta_id
        )
        OR EXISTS (
          SELECT 1 FROM inv_reservas_landmarks r
          WHERE r.tramo_id = NEW.id AND r.ruta_id <> NEW.ruta_id
        )
      ) THEN
        RAISE EXCEPTION 'Mover tramo cruza relaciones' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER trg_fg_tramo_integridad
    BEFORE INSERT OR UPDATE ON inv_tramos
    FOR EACH ROW EXECUTE FUNCTION fg_validar_tramo()
    """,
    """
    CREATE FUNCTION fg_validar_fibra_ruta() RETURNS trigger AS $$
    BEGIN
      IF NEW.ruta_id <> OLD.ruta_id AND EXISTS (
        SELECT 1 FROM inv_fibras_tramos ft
        JOIN inv_tramos t ON t.id = ft.tramo_id
        WHERE ft.fibra_id = NEW.id AND t.ruta_id <> NEW.ruta_id
      ) THEN
        RAISE EXCEPTION 'Mover fibra cruza tramos' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER trg_fg_fibra_ruta_integridad
    BEFORE UPDATE OF ruta_id ON inv_fibras_detalle
    FOR EACH ROW EXECUTE FUNCTION fg_validar_fibra_ruta()
    """,
)


SQLITE_TRIGGER_NAMES = (
    'trg_fg_fibratramo_insert_integridad',
    'trg_fg_fibratramo_update_integridad',
    'trg_fg_coordenada_insert_integridad',
    'trg_fg_coordenada_update_integridad',
    'trg_fg_reserva_insert_integridad',
    'trg_fg_reserva_update_integridad',
    'trg_fg_tramo_insert_integridad',
    'trg_fg_tramo_update_integridad',
    'trg_fg_fibra_update_integridad',
)


def _coincide(nodo, texto):
    buscado = str(texto or '').strip().casefold()
    if not buscado or nodo is None:
        return False
    return buscado in {
        str(nodo.codigo or '').strip().casefold(),
        str(nodo.nombre or '').strip().casefold(),
    }


def normalizar_datos(apps, schema_editor):
    NodoRed = apps.get_model('mapas', 'NodoRed')
    InventarioODF = apps.get_model('mapas', 'InventarioODF')
    HubSite = apps.get_model('mapas', 'HubSite')
    InventarioTramo = apps.get_model('mapas', 'InventarioTramo')
    InventarioFibra = apps.get_model('mapas', 'InventarioFibra')
    FibraTramo = apps.get_model('mapas', 'FibraTramo')
    Ruta = apps.get_model('mapas', 'Ruta')
    alias = schema_editor.connection.alias

    odfs = {
        item.odf.strip().casefold(): item
        for item in InventarioODF.objects.using(alias).all()
        if item.odf and item.odf.strip()
    }
    sites = {
        item.nombre.strip().casefold(): item
        for item in HubSite.objects.using(alias).all()
        if item.nombre and item.nombre.strip()
    }
    nodos_actualizar = []
    for nodo in NodoRed.objects.using(alias).all():
        if nodo.tipo == 'ODF':
            objeto = odfs.get(str(nodo.codigo or '').strip().casefold())
            if objeto:
                nodo.odf_obj_id = objeto.pk
                nodo.codigo = objeto.odf.strip().upper()
                nodo.nombre = objeto.odf.strip()
                nodos_actualizar.append(nodo)
        elif nodo.tipo == 'SITE':
            objeto = sites.get(str(nodo.codigo or '').strip().casefold())
            if objeto:
                nodo.hub_site_obj_id = objeto.pk
                nodo.codigo = objeto.nombre.strip().upper()
                nodo.nombre = objeto.nombre.strip()
                nodos_actualizar.append(nodo)
    if nodos_actualizar:
        NodoRed.objects.using(alias).bulk_update(
            nodos_actualizar,
            ['odf_obj', 'hub_site_obj', 'codigo', 'nombre'],
            batch_size=500,
        )

    tramos_por_ruta = defaultdict(list)
    tramos_actualizar = []
    for tramo in (
        InventarioTramo.objects.using(alias)
        .select_related('origen_nodo', 'destino_nodo')
        .order_by('ruta_id', 'tramo_secuencia', 'pk')
    ):
        tramos_por_ruta[tramo.ruta_id].append(tramo)
        if tramo.capacidad_hilos is not None:
            tramo.capacidad = f'{tramo.capacidad_hilos} Hilos'
        if tramo.origen_nodo_id:
            tramo.origen = tramo.origen_nodo.codigo
        if tramo.destino_nodo_id:
            tramo.destino = tramo.destino_nodo.codigo
        tramos_actualizar.append(tramo)
    if tramos_actualizar:
        InventarioTramo.objects.using(alias).bulk_update(
            tramos_actualizar,
            ['capacidad', 'origen', 'destino'],
            batch_size=500,
        )

    rutas_actualizar = []
    for ruta in Ruta.objects.using(alias).all():
        tramos = tramos_por_ruta.get(ruta.pk, ())
        if not tramos:
            continue
        primero = tramos[0].origen_nodo
        ultimo = tramos[-1].destino_nodo
        cambio = False
        if (
            ruta.odf_origen_id is None
            and primero is not None
            and primero.odf_obj_id
        ):
            ruta.odf_origen_id = primero.odf_obj_id
            cambio = True
        if (
            ruta.odf_destino_id is None
            and ultimo is not None
            and ultimo.odf_obj_id
        ):
            ruta.odf_destino_id = ultimo.odf_obj_id
            cambio = True
        if cambio:
            rutas_actualizar.append(ruta)
    if rutas_actualizar:
        Ruta.objects.using(alias).bulk_update(
            rutas_actualizar,
            ['odf_origen', 'odf_destino'],
            batch_size=500,
        )

    requeridos = {
        ruta_id: {tramo.pk for tramo in tramos}
        for ruta_id, tramos in tramos_por_ruta.items()
    }
    cobertura = defaultdict(set)
    for fibra_id, tramo_id in (
        FibraTramo.objects.using(alias)
        .exclude(fibra_id__isnull=True)
        .values_list('fibra_id', 'tramo_id')
    ):
        cobertura[fibra_id].add(tramo_id)

    fibras_actualizar = []
    for fibra in InventarioFibra.objects.using(alias).all():
        tramos = tramos_por_ruta.get(fibra.ruta_id, ())
        if (
            not tramos
            or cobertura.get(fibra.pk, set()) != requeridos[fibra.ruta_id]
        ):
            continue
        origen = tramos[0].origen_nodo
        destino = tramos[-1].destino_nodo
        if (
            origen is None
            or destino is None
            or origen.pk == destino.pk
        ):
            continue
        cambio = False
        if fibra.origen_nodo_id is None and _coincide(origen, fibra.origen_odf):
            fibra.origen_nodo_id = origen.pk
            cambio = True
        if fibra.destino_nodo_id is None and _coincide(destino, fibra.destino):
            fibra.destino_nodo_id = destino.pk
            cambio = True
        if cambio:
            fibras_actualizar.append(fibra)
    if fibras_actualizar:
        InventarioFibra.objects.using(alias).bulk_update(
            fibras_actualizar,
            ['origen_nodo', 'destino_nodo'],
            batch_size=1000,
        )


def crear_triggers(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    sentencias = (
        SQLITE_TRIGGERS if vendor == 'sqlite'
        else POSTGRES_TRIGGERS if vendor == 'postgresql'
        else ()
    )
    with schema_editor.connection.cursor() as cursor:
        for sentencia in sentencias:
            cursor.execute(sentencia)


def eliminar_triggers(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    with schema_editor.connection.cursor() as cursor:
        if vendor == 'sqlite':
            for nombre in SQLITE_TRIGGER_NAMES:
                cursor.execute(f'DROP TRIGGER IF EXISTS {nombre}')
        elif vendor == 'postgresql':
            for tabla, trigger in (
                ('inv_fibras_tramos', 'trg_fg_fibratramo_integridad'),
                ('inv_rutas_coordenadas', 'trg_fg_coordenada_integridad'),
                ('inv_reservas_landmarks', 'trg_fg_reserva_integridad'),
                ('inv_tramos', 'trg_fg_tramo_integridad'),
                ('inv_fibras_detalle', 'trg_fg_fibra_ruta_integridad'),
            ):
                cursor.execute(f'DROP TRIGGER IF EXISTS {trigger} ON {tabla}')
            for funcion in (
                'fg_validar_fibratramo',
                'fg_validar_coordenada',
                'fg_validar_reserva',
                'fg_validar_tramo',
                'fg_validar_fibra_ruta',
            ):
                cursor.execute(f'DROP FUNCTION IF EXISTS {funcion}()')


class Migration(migrations.Migration):

    dependencies = [
        ('mapas', '0032_inventariofibra_estado_desconocido'),
    ]

    operations = [
        migrations.AddField(
            model_name='nodored',
            name='hub_site_obj',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='nodo_red',
                to='mapas.hubsite',
                verbose_name='Site canónico',
            ),
        ),
        migrations.AddField(
            model_name='nodored',
            name='odf_obj',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='nodo_red',
                to='mapas.inventarioodf',
                verbose_name='ODF canónico',
            ),
        ),
        migrations.AddConstraint(
            model_name='fibratramo',
            constraint=models.CheckConstraint(
                condition=models.Q(numero_hilo__regex=r'^F[1-9][0-9]*$'),
                name='ck_fibra_tramo_formato',
            ),
        ),
        migrations.AddConstraint(
            model_name='hubsite',
            constraint=models.UniqueConstraint(
                Lower('nombre'),
                name='uq_hub_site_nombre_ci',
            ),
        ),
        migrations.AddConstraint(
            model_name='inventariotramo',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(capacidad_hilos__isnull=True)
                    | models.Q(
                        capacidad_hilos__gte=(
                            Coalesce('hilos_ocupados', 0)
                            + Coalesce('hilos_reservados', 0)
                            + Coalesce('hilos_libres', 0)
                        )
                    )
                ),
                name='ck_tramo_contadores_en_capacidad',
            ),
        ),
        migrations.AddConstraint(
            model_name='nodored',
            constraint=models.CheckConstraint(
                condition=models.Q(odf_obj__isnull=True) | models.Q(tipo='ODF'),
                name='ck_nodo_odf_tipo',
            ),
        ),
        migrations.AddConstraint(
            model_name='nodored',
            constraint=models.CheckConstraint(
                condition=models.Q(hub_site_obj__isnull=True) | models.Q(tipo='SITE'),
                name='ck_nodo_site_tipo',
            ),
        ),
        migrations.AddConstraint(
            model_name='nodored',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(odf_obj__isnull=True)
                    | models.Q(hub_site_obj__isnull=True)
                ),
                name='ck_nodo_canonico_unico',
            ),
        ),
        migrations.AddConstraint(
            model_name='ruta',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(capacidad_declarada_hilos__isnull=True)
                    | models.Q(
                        capacidad_declarada_hilos__gte=(
                            Coalesce('hilos_ocupados_declarados', 0)
                            + Coalesce('hilos_reservados_declarados', 0)
                            + Coalesce('hilos_libres_declarados', 0)
                        )
                    )
                ),
                name='ck_ruta_contadores_en_capacidad',
            ),
        ),
        migrations.RunPython(normalizar_datos, migrations.RunPython.noop),
        migrations.RunPython(crear_triggers, eliminar_triggers),
    ]
