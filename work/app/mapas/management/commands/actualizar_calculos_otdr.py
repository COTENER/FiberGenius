# mapas/management/commands/actualizar_calculos_otdr.py

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from collections import Counter

class Command(BaseCommand):
    help = 'Calcula la pérdida estimada (link budget) y actualiza el estado para cada ruta (Node) en mon_otdr_eventos_geo.'

    # 1. DICCIONARIO MODIFICADO: Se retiraron Switch y First Connector
    LOSS_FACTORS = {
        'km': 0.25,
        'Splice': 0.05,
        'Reflection': 0.2,
        # 'First Connector': 0.25,
        # 'Switch': 0.5,            
    }

    # 2. NUEVA CONSTANTE DE PÉRDIDA
    CONSTANTE_PERDIDA_FIJA = 0.7

    def add_arguments(self, parser):
        parser.add_argument('--ruta', type=str, help='Nombre exacto de la ruta a procesar')

    def handle(self, *args, **options):
        ruta_kwargs = options.get('ruta')
        msg = f"Iniciando el cálculo del presupuesto de potencia{' para ' + ruta_kwargs if ruta_kwargs else ' para todas las rutas'}..."
        self.stdout.write(msg)

        try:
            # Usamos transaction.atomic para asegurar integridad
            with transaction.atomic():
                with connection.cursor() as cursor:
                    # 1. Obtener todas las rutas únicas que tienen datos georreferenciados
                    if ruta_kwargs:
                        cursor.execute("SELECT DISTINCT Node FROM mon_otdr_eventos_geo WHERE Node = %s", [ruta_kwargs])
                    else:
                        cursor.execute("SELECT DISTINCT Node FROM mon_otdr_eventos_geo WHERE Node IS NOT NULL AND Node != ''")
                    
                    node_names = [row[0] for row in cursor.fetchall()]

                    self.stdout.write(f"Se encontraron {len(node_names)} rutas únicas para procesar.")

                    for node_name in node_names:
                        self.stdout.write(f"\n----- Procesando ruta (Node): {node_name} -----")

                        # 2. Obtener todos los eventos de esa ruta
                        cursor.execute("""
                            SELECT id, event_type, optical_distance_m, cumulative_loss_db, real_cumulative_loss_db
                            FROM mon_otdr_eventos_geo 
                            WHERE Node = %s
                        """, [node_name])
                        
                        columnas = [col[0] for col in cursor.description]
                        eventos_de_la_ruta = [dict(zip(columnas, row)) for row in cursor.fetchall()]
                        
                        # 3. Buscar el evento 'Fiber End'
                        fiber_end_events = [ev for ev in eventos_de_la_ruta if ev['event_type'] == 'Fiber End']

                        if not fiber_end_events:
                            self.stdout.write(self.style.WARNING(f"  -> ADVERTENCIA: La ruta '{node_name}' no tiene un evento 'Fiber End'. Se omitirá."))
                            continue
                        
                        # Tomamos el último Fiber End
                        fiber_end_event = fiber_end_events[-1]
                        
                        distancia_total_m = fiber_end_event.get('optical_distance_m') or 0
                        
                        # Preferimos usar la 'real_cumulative_loss_db' calculada en el paso 3
                        perdida_medida_total = fiber_end_event.get('real_cumulative_loss_db')
                        if perdida_medida_total is None:
                             perdida_medida_total = fiber_end_event.get('cumulative_loss_db')

                        # --- CÁLCULOS ---

                        # A. Pérdida por distancia (Fibra)
                        distancia_total_km = distancia_total_m / 1000
                        perdida_por_distancia = distancia_total_km * self.LOSS_FACTORS['km']
                        self.stdout.write(f"  -> Distancia: {distancia_total_km:.3f} km, Pérdida por fibra: {perdida_por_distancia:.4f} dB")

                        # B. Pérdida por eventos puntuales (Solo Splice y Reflection ahora)
                        perdida_total_de_eventos = 0
                        tipos_de_evento_en_ruta = [ev['event_type'] for ev in eventos_de_la_ruta if ev['event_type'] != 'Fiber End']
                        event_counts = Counter(tipos_de_evento_en_ruta)

                        for event_type, factor in self.LOSS_FACTORS.items():
                            if event_type == 'km': continue 
                            
                            count = event_counts.get(event_type, 0)
                            if count > 0:
                                perdida_del_tipo = count * factor
                                perdida_total_de_eventos += perdida_del_tipo
                                self.stdout.write(f"  -> Tipo: {event_type}, Cantidad: {count}, Pérdida: {perdida_del_tipo:.4f} dB")
                        
                        # C. Pérdida Estimada Total (Budget)
                        # FÓRMULA: (Km * 0.25) + (Eventos * Factor) + CONSTANTE (0.7)
                        perdida_estimada_calculada = perdida_por_distancia + perdida_total_de_eventos + self.CONSTANTE_PERDIDA_FIJA
                        
                        self.stdout.write(f"  -> Constante agregada: +{self.CONSTANTE_PERDIDA_FIJA} dB")
                        self.stdout.write(f"  => Pérdida Estimada Total (Budget): {perdida_estimada_calculada:.4f} dB")

                        # D. Comparación y Status
                        nuevo_status = 'pass'
                        
                        if perdida_medida_total is not None:
                            perdida_medida_total = float(perdida_medida_total)
                            diferencia_db = perdida_medida_total - perdida_estimada_calculada
                            
                            self.stdout.write(f"  -> Pérdida Real (Medida/Ajustada): {perdida_medida_total:.4f} dB")
                            self.stdout.write(f"  -> Diferencia (Real - Estimada): {diferencia_db:.4f} dB")
                            
                            # Criterio: Si la pérdida real excede la estimada por más de 1 dB -> FAIL
                            if diferencia_db > 1.0:
                                nuevo_status = 'fail'
                        else:
                            self.stdout.write(self.style.WARNING("  -> ADVERTENCIA: No hay valor de pérdida medida disponible. Se asume 'pass'."))

                        self.stdout.write(f"  => Nuevo Status: {nuevo_status.upper()}")

                        # 4. Actualizar la Base de Datos
                        cursor.execute("""
                            UPDATE mon_otdr_eventos_geo
                            SET cumulative_loss_db_calculated = %s, event_test_status = %s
                            WHERE id = %s
                        """, [
                            round(perdida_estimada_calculada, 4), 
                            nuevo_status, 
                            fiber_end_event['id']
                        ])
                        self.stdout.write(f"  -> Base de datos actualizada para la ruta '{node_name}'.")

            self.stdout.write(self.style.SUCCESS("\n¡Cálculos finales completados exitosamente!"))

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"\nHa ocurrido un error crítico durante el cálculo: {e}"))