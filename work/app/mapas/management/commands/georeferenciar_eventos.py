# mapas/management/commands/georeferenciar_eventos.py
from django.core.management.base import BaseCommand
from django.db import DatabaseError, connection
import os
import math
import glob
import pandas as pd
import numpy as np
from defusedxml import ElementTree as ET
import logging

logger = logging.getLogger('mapas')

# =========================
# Utilidades
# =========================
R_EARTH = 6371000.0

def ensure_dir_for_path(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)

def haversine(lon1, lat1, lon2, lat2):
    lo1, la1, lo2, la2 = map(math.radians, (lon1, lat1, lon2, lat2))
    dlon, dlat = lo2-lo1, la2-la1
    a = math.sin(dlat/2)**2 + math.cos(la1)*math.cos(la2)*math.sin(dlon/2)**2
    return R_EARTH * 2 * math.asin(math.sqrt(a))

def slerp_latlon(lat1, lon1, lat2, lon2, t: float):
    la1, lo1, la2, lo2 = map(math.radians, (lat1, lon1, lat2, lon2))
    d = 2*math.asin(math.sqrt(
        math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2))
    if d < 1e-12: return lat1, lon1
    A, B = math.sin((1-t)*d)/math.sin(d), math.sin(t*d)/math.sin(d)
    x = A*math.cos(la1)*math.cos(lo1) + B*math.cos(la2)*math.cos(lo2)
    y = A*math.cos(la1)*math.sin(lo1) + B*math.cos(la2)*math.sin(lo2)
    z = A*math.sin(la1)             + B*math.sin(la2)
    return math.degrees(math.atan2(z, math.sqrt(x*x+y*y))), math.degrees(math.atan2(y, x))

def load_kml_route(kml_path: str):
    tree = ET.parse(kml_path)
    root = tree.getroot()
    ns = ''
    if root.tag.startswith('{'):
        ns = root.tag.split('}')[0] + '}'
    coords_text = None
    for el in root.iter(f'{ns}coordinates'):
        coords_text = el.text; break
    if coords_text is None:
        for el in root.iter('coordinates'):
            coords_text = el.text; break
    if not coords_text:
        raise ValueError(f"No se encontieron coordenadas en {kml_path}")
    points = []
    for tok in coords_text.strip().split():
        parts = tok.split(',')
        if len(parts) >= 2:
            points.append((float(parts[1]), float(parts[0])))
    geo_cum = [0.0]
    seg_len = []
    for i in range(1, len(points)):
        d = haversine(points[i-1][1], points[i-1][0], points[i][1], points[i][0])
        seg_len.append(d)
        geo_cum.append(geo_cum[-1]+d)
    return points, geo_cum, seg_len

def interpolate_on_route(points, geo_cum, seg_len, s_target: float, extrapolate: bool=True):
    route_len = geo_cum[-1]
    if s_target <= 0: return points[0]
    if s_target >= route_len:
        if not extrapolate: return points[-1]
        over = s_target - route_len
        if len(points) >= 2:
            d = seg_len[-1] if seg_len[-1] > 0 else 1.0
            t = over / d
            return slerp_latlon(points[-2][0], points[-2][1], points[-1][0], points[-1][1], 1+t)
        return points[-1]
    for i in range(1, len(geo_cum)):
        if s_target <= geo_cum[i]:
            d = seg_len[i-1]
            t = (s_target - geo_cum[i-1]) / d if d > 0 else 0
            return slerp_latlon(points[i-1][0], points[i-1][1], points[i][0], points[i][1], t)
    return points[-1]

def detect_col(cols, patterns):
    for c in cols:
        for p in patterns:
            if p.lower() in c.lower(): return c
    return None

def load_table(path: str, sheet=None):
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.xls', '.xlsx'):
        return pd.read_excel(path, sheet_name=sheet)
    return pd.read_csv(path)

def guess_units_to_m(series: pd.Series):
    mx = series.max()
    if mx < 200: return series * 1000
    return series

def build_calibration(df_calib: pd.DataFrame):
    dcol = detect_col(df_calib.columns, ['distance', 'distancia', 'dist'])
    lcol = detect_col(df_calib.columns, ['lat', 'latitude', 'latitud'])
    Lcol = detect_col(df_calib.columns, ['lon', 'long', 'longitude', 'longitud'])
    if not(dcol and lcol and Lcol):
        raise ValueError(f"Calibration columns not found in {list(df_calib.columns)}")
    anchors = df_calib[[dcol, lcol, Lcol]].copy()
    anchors.columns = ['distance_m', 'lat', 'lon']
    anchors['distance_m'] = guess_units_to_m(anchors['distance_m'].astype(float))
    anchors = anchors.sort_values('distance_m').reset_index(drop=True)
    return anchors

def optical_to_geo_piecewise(anchors: pd.DataFrame, d: float):
    if d <= anchors['distance_m'].iloc[0]:
        return anchors['lat'].iloc[0], anchors['lon'].iloc[0]
    if d >= anchors['distance_m'].iloc[-1]:
        return anchors['lat'].iloc[-1], anchors['lon'].iloc[-1]
    for i in range(1, len(anchors)):
        if d <= anchors['distance_m'].iloc[i]:
            d0 = anchors['distance_m'].iloc[i-1]
            d1 = anchors['distance_m'].iloc[i]
            t = (d - d0) / (d1 - d0) if d1 != d0 else 0
            lat = anchors['lat'].iloc[i-1] + t*(anchors['lat'].iloc[i] - anchors['lat'].iloc[i-1])
            lon = anchors['lon'].iloc[i-1] + t*(anchors['lon'].iloc[i] - anchors['lon'].iloc[i-1])
            return lat, lon
    return anchors['lat'].iloc[-1], anchors['lon'].iloc[-1]

def load_events(df_events: pd.DataFrame, cut_at_fiber_end: bool=True):
    out = df_events.copy()
    if 'distance_m' not in out.columns:
        raise ValueError(f"No distance_m col in {list(out.columns)}")
        
    out['optical_distance_m'] = guess_units_to_m(out['distance_m'].astype(float))
    
    if cut_at_fiber_end and 'event_type' in out.columns:
        # Aseguramos que 'event_type' sea string para evitar errores con nulos
        out['event_type'] = out['event_type'].astype(str)
        fe = out[out['event_type'].str.lower().str.contains('fiber end|fiberend', na=False)]
        if len(fe): 
            out = out.loc[:fe.index[-1]]
            
    return out

# =========================
# DB Helpers (usando django.db.connection)
# =========================

def fetch_events_from_db(Node: str):
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM mon_otdr_eventos WHERE Node = %s ORDER BY distance_m", (Node,))
        columns = [col[0] for col in cursor.description]
        return pd.DataFrame([dict(zip(columns, row)) for row in cursor.fetchall()])

def ensure_output_table_exists():
    sql = """
    CREATE TABLE IF NOT EXISTS mon_otdr_eventos_geo (
        id BIGSERIAL PRIMARY KEY,
        prueba_id INT NULL,
        Node VARCHAR(255) NOT NULL,
        event_id VARCHAR(255) NOT NULL,
        event_type VARCHAR(255) NULL,
        optical_distance_m DOUBLE NULL,
        geo_distance_m_from_calibration DOUBLE NULL,
        segment_index INT NULL,
        segment_fraction DOUBLE NULL,
        latitude DOUBLE NULL,
        longitude DOUBLE NULL,
        loss_db DOUBLE NULL,
        reflectance_db DOUBLE NULL,
        event_test_status VARCHAR(255) NULL,
        section_loss_db DOUBLE NULL,
        cumulative_loss_db DOUBLE NULL,
        cumulative_loss_db_calculated DECIMAL(8,3) NULL,
        real_cumulative_loss_db DECIMAL(8,3) NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uniq_enlace_event UNIQUE (Node, event_id)
    );
    """
    with connection.cursor() as cur:
        cur.execute(sql)
        # Asegurar que columnas añadidas en versiones posteriores existan
        for col, col_type in [
            ("prueba_id", "INT NULL"),
            ("geo_distance_m_from_calibration", "DOUBLE NULL"),
            ("segment_index", "INT NULL"),
            ("segment_fraction", "DOUBLE NULL"),
            ("loss_db", "DOUBLE NULL"),
            ("reflectance_db", "DOUBLE NULL"),
            ("event_test_status", "VARCHAR(255) NULL"),
            ("section_loss_db", "DECIMAL(8,3) NULL"),
            ("cumulative_loss_db", "DECIMAL(8,3) NULL"),
            ("cumulative_loss_db_calculated", "DECIMAL(8,3) NULL"),
            ("real_cumulative_loss_db", "DECIMAL(8,3) NULL")
        ]:
            try:
                # Nota: IF NOT EXISTS en ALTER TABLE requiere MySQL 8.0.1+ o MariaDB 10.2.12+
                # Usamos try-except para mayor compatibilidad si ya existe
                cur.execute(f"ALTER TABLE mon_otdr_eventos_geo ADD COLUMN {col} {col_type}")
            except DatabaseError:
                logger.debug(
                    "No se añadió la columna %s; probablemente ya existe",
                    col,
                    exc_info=True,
                )

def replace_measurement_for_enlace(Node: str, df: pd.DataFrame):
    ensure_output_table_exists()
    cols = ["prueba_id", "Node","event_id","event_type","optical_distance_m",
            "geo_distance_m_from_calibration","segment_index","segment_fraction",
            "latitude","longitude","loss_db","reflectance_db","event_test_status",
            "section_loss_db","cumulative_loss_db", "cumulative_loss_db_calculated",
            "real_cumulative_loss_db"]
    
    delete_sql = "DELETE FROM mon_otdr_eventos_geo WHERE Node = %s"
    insert_sql = f"INSERT INTO mon_otdr_eventos_geo ({','.join(cols)}) VALUES ({','.join(['%s']*len(cols))}) ON CONFLICT (Node, event_id) DO NOTHING"

    def safe(val):
        if pd.isna(val): return None
        return val

    with connection.cursor() as cur:
        cur.execute(delete_sql, (Node,))
        data = []
        for _, r in df.iterrows():
            data.append((
                safe(r.get("prueba_id")),
                Node, 
                str(safe(r.get("event_id",""))), 
                str(safe(r.get("event_type",""))),
                safe(r.get("optical_distance_m")), 
                safe(r.get("geo_distance_m_from_calibration")),
                safe(r.get("segment_index")), 
                safe(r.get("segment_fraction")), 
                safe(r.get("latitude")), 
                safe(r.get("longitude")),
                safe(r.get("loss_db")), 
                safe(r.get("reflectance_db")), 
                safe(r.get("event_test_status")),
                safe(r.get("section_loss_db")), 
                safe(r.get("cumulative_loss_db")),
                safe(r.get("cumulative_loss_db_calculated")), 
                safe(r.get("real_cumulative_loss_db"))
            ))
        if data:
            cur.executemany(insert_sql, data)

def find_single_file(directory, patterns):
    for p in patterns:
        found = glob.glob(os.path.join(directory, p))
        if found: return found[0]
    return None

def process_directory(dir_path, Node, calib_sheet, cut_at_fiber_end, no_extrapolate, write_csv, write_kml_flag):
    kml_file = find_single_file(dir_path, ['*.kml', '*.KML'])
    calib_file = find_single_file(dir_path, ['*calibra*.*', '*calib*.*', '*Calibra*.*'])

    if not kml_file:
        logger.warning(f"  [SKIP] No KML found in {dir_path}")
        return False

    points, geo_cum, seg_len = load_kml_route(kml_file)

    df_events = fetch_events_from_db(Node)
    if df_events.empty:
        logger.warning(f"  [SKIP] No events in DB for Node={Node}")
        return False
    
    events = load_events(df_events, cut_at_fiber_end=cut_at_fiber_end)

    if calib_file:
        df_calib = load_table(calib_file, sheet=calib_sheet)
        anchors = build_calibration(df_calib)
        geo_fn = lambda d: optical_to_geo_piecewise(anchors, d)
    else:
        route_optical_len = events['optical_distance_m'].max()
        route_geo_len = geo_cum[-1]
        scale = route_geo_len / route_optical_len if route_optical_len > 0 else 1.0
        geo_fn = lambda d: interpolate_on_route(
            points, geo_cum, seg_len, d * scale, extrapolate=not no_extrapolate)

    lats, lons, gdists, segs, fracs = [], [], [], [], []
    cumulative_loss = 0.0
    real_cumulative_list = []

    for _, ev in events.iterrows():
        od = ev['optical_distance_m']
        lat_i, lon_i = geo_fn(od)
        lats.append(lat_i); lons.append(lon_i)
        
        loss_val = ev.get('loss_db', 0) or 0
        cumulative_loss += float(loss_val)
        real_cumulative_list.append(round(cumulative_loss, 3))

        if calib_file:
            gdists.append(None); segs.append(None); fracs.append(None)
        else:
            route_optical_len_calc = events['optical_distance_m'].max()
            route_geo_len_calc = geo_cum[-1]
            scale_calc = route_geo_len_calc / route_optical_len_calc if route_optical_len_calc > 0 else 1.0
            s = od * scale_calc
            gdists.append(s)
            seg_i = 0
            for j in range(1, len(geo_cum)):
                if s <= geo_cum[j]: seg_i = j-1; break
            segs.append(seg_i)
            d = seg_len[seg_i] if seg_i < len(seg_len) else 1
            fracs.append((s - geo_cum[seg_i]) / d if d else 0)

    events['latitude'] = lats
    events['longitude'] = lons
    events['geo_distance_m_from_calibration'] = gdists
    events['segment_index'] = segs
    events['segment_fraction'] = fracs
    events['real_cumulative_loss_db'] = real_cumulative_list

    replace_measurement_for_enlace(Node, events)
    logger.info(f"  [OK] {Node}: {len(events)} events geo-referenced")
    return True

# =========================
# Comando Django
# =========================
class Command(BaseCommand):
    help = 'Georreferencia eventos OTDR'

    def add_arguments(self, parser):
        parser.add_argument('--root', type=str, required=True, help='Root directory with subdirectories per enlace')
        parser.add_argument('--recursive', action='store_true', default=False, help='Process subdirectories recursively')
        parser.add_argument('--ruta', type=str, help='Nombre exacto de la ruta a procesar')

    def handle(self, *args, **options):
        root = options['root']
        recursive = options.get('recursive', False)
        ruta_kwargs = options.get('ruta')

        if not os.path.isdir(root):
            self.stderr.write(self.style.ERROR(f"Root directory not found: {root}"))
            return

        self.stdout.write(f"Scanning root: {root}")

        dirs_to_process = []
        if recursive:
            for dirpath, dirnames, filenames in os.walk(root):
                kml_files = [f for f in filenames if f.lower().endswith('.kml')]
                if kml_files:
                    dirs_to_process.append(dirpath)
        else:
            for name in sorted(os.listdir(root)):
                full = os.path.join(root, name)
                if os.path.isdir(full):
                    dirs_to_process.append(full)

        self.stdout.write(f"Found {len(dirs_to_process)} directories to process.")

        ok = 0
        for d in dirs_to_process:
            Node = os.path.basename(d)
            if ruta_kwargs and Node != ruta_kwargs:
                continue
            self.stdout.write(f"\nProcessing: {Node}")
            try:
                if process_directory(d, Node,
                                     calib_sheet=None,
                                     cut_at_fiber_end=True,
                                     no_extrapolate=False,
                                     write_csv=False,
                                     write_kml_flag=False):
                    ok += 1
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"  [ERROR] {Node}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"\nDone. {ok}/{len(dirs_to_process)} directories processed."))
