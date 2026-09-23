def analizar_trazas(x_actual, y_actual, x_ref, y_ref, perfil_umbral):
    """
    Motor matemático puramente en Python para cruzar una traza actual contra una de referencia
    y diagnosticar Atenuación o Cortes de Fibra basándose en los umbrales configurados.
    
    Argumentos:
    - x_actual, y_actual: Arrays de distancia y dB de la prueba reciente.
    - x_ref, y_ref: Arrays de distancia y dB de la referencia.
    - perfil_umbral: Instancia del modelo PerfilUmbral con las tolerancias de la ruta.
    
    Retorna:
    - lista de alarmas encontradas [{tipo, severidad, distancia_m, desviacion_db}]
    """
    alarmas = []
    
    # Tolerancias desde el perfil
    minor_min = perfil_umbral.atenuacion_minor_min if perfil_umbral else 1.0
    major_min = perfil_umbral.atenuacion_major_min if perfil_umbral else 3.0
    crit_min = perfil_umbral.atenuacion_critical_min if perfil_umbral else 5.0
    corte_nivel = perfil_umbral.nivel_minimo_corte_db if perfil_umbral else -15.0
    fin_fibra_ref = max(x_ref) if len(x_ref) > 0 else 0
    
    # Asumimos que x_actual y x_ref están relativamente alineados o interpolamos.
    # Por simplicidad en este MVP, iteraremos sobre los puntos actuales y buscaremos el más cercano en ref.
    
    corte_detectado = False
    
    for i in range(len(x_actual)):
        dist_m = x_actual[i]
        db_actual = y_actual[i]
        
        # --- REGLA 1: CORTE DE FIBRA ---
        # Si la luz cae abruptamente por debajo de la línea horizontal de corte, 
        # y estamos antes del "fin de fibra" histórico... es un corte!
        if db_actual < corte_nivel and dist_m < (fin_fibra_ref - 50): # 50m de margen
            if not corte_detectado:
                alarmas.append({
                    'tipo_falla': 'Corte de Fibra',
                    'severidad': 'Critical',
                    'distancia_falla_m': dist_m,
                    'desviacion_db': None
                })
                corte_detectado = True
            continue # Si hay corte, ignoramos la atenuación subsiguiente
        
        # --- REGLA 2: ATENUACIÓN ---
        # Si no hay corte, comparamos contra la referencia para ver atenuaciones puntuales
        # Buscar el valor db en la referencia a esta misma distancia
        # (Idealmente interpolación lineal, pero buscaremos índice por proximidad)
        idx_ref = min(range(len(x_ref)), key=lambda j: abs(x_ref[j] - dist_m))
        if abs(x_ref[idx_ref] - dist_m) < 20: # Si la ref está a menos de 20m de este punto
            db_ref = y_ref[idx_ref]
            desviacion = db_ref - db_actual # Cuanto MÁS abajo esté actual, mayor atenuación
            
            if desviacion > minor_min and not corte_detectado:
                # Determinar severidad
                sev = 'Minor'
                if desviacion > crit_min:
                    sev = 'Critical'
                elif desviacion > major_min:
                    sev = 'Major'
                
                # Agrupar alarmas cercanas (debounce)
                if not any(a['tipo_falla'] == 'Atenuación' and abs(a['distancia_falla_m'] - dist_m) < 100 for a in alarmas):
                    alarmas.append({
                        'tipo_falla': 'Atenuación',
                        'severidad': sev,
                        'distancia_falla_m': dist_m,
                        'desviacion_db': round(desviacion, 2)
                    })
                
    return alarmas
