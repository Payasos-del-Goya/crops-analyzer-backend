from sqlalchemy import text
from config import SessionLocal
from modelo_predicciones.modelo_europa import CULTIVOS

# Ids de la tabla cultivos que entran en el modelo de /recomendacion (18 herbáceos).
MODEL_CROP_IDS = sorted(crop_id for crop_id, _ in CULTIVOS.values())

def retrieve_crops_data():
    """
    Consulta los cultivos desde PostgreSQL y mapea todas las columnas del esquema.
    """
    db = SessionLocal()
    try:
        query = text("SELECT * FROM cultivos;")
        result = db.execute(query).fetchall()

        crops_list = []
        for row in result:
            crops_list.append({
                "id": row[0],
                "grupo": row[1],
                "cultivo": row[2],
                "referencia_fao56": row[3],
                "kc_ini": row[4],
                "kc_mid_min": row[5],
                "kc_mid_max": row[6],
                "kc_end_min": row[7],
                "kc_end_max": row[8],
                "altura_min_m": row[9],
                "altura_max_m": row[10],
                "zr_min_m": row[11],
                "zr_max_m": row[12],
                "p_agotamiento": row[13],
                "cee_umbral_min_ds_m": row[14],
                "cee_umbral_max_ds_m": row[15],
                "b_min_pct_por_ds_m": row[16],
                "b_max_pct_por_ds_m": row[17],
                "tolerancia_salinidad": row[18],
                "ky_min": row[19],
                "ky_max": row[20],
                "l_ini_d": row[21],
                "l_des_d": row[22],
                "l_med_d": row[23],
                "l_fin_d": row[24],
                "total_fases_d": row[25],
                "region_siembra_fases": row[26],
                "notas": row[27],
                "nombre_cientifico": row[28],
                "ciclo_vida": row[29],
                "origen_bloque_fao56": row[30],
                "fiabilidad_agro": row[31],
                "temp_aire_opt_min_c": row[32],
                "temp_aire_opt_max_c": row[33],
                "temp_aire_min_abs_c": row[34],
                "temp_aire_max_abs_c": row[35],
                "temp_letal_helada_c": row[36],
                "periodo_sensible_helada": row[37],
                "prob_helada_suelo_sensibilidad": row[38],
                "temp_suelo_siembra_min_c": row[39],
                "gdd_base_c": row[40],
                "gdd_acum_ciclo_min": row[41],
                "gdd_acum_ciclo_max": row[42],
                "horas_frio_min_h": row[43],
                "horas_frio_max_h": row[44],
                "luz_solar_tipo": row[45],
                "luz_solar_dli_min_mol_m2_d": row[46],
                "luz_solar_dli_opt_mol_m2_d": row[47],
                "fotoperiodo": row[48],
                "radiacion_mj_min_m2_d": row[49],
                "radiacion_mj_opt_m2_d": row[50],
                "hr_opt_min_pct": row[51],
                "hr_opt_max_pct": row[52],
                "hr_riesgo_enfermedad_pct": row[53],
                "hoja_mojada_horas_riesgo": row[54],
                "hoja_mojada_enfermedad_ref": row[55],
                "conductividad_us_umbral_cee_min": row[56],
                "conductividad_us_umbral_cee_max": row[57],
                "conductividad_us_cee_rend0": row[58],
                "conductividad_us_agua_riego_max": row[59],
                "conductividad_us_solucion_opt_min": row[60],
                "conductividad_us_solucion_opt_max": row[61],
                "et0_mm_dia_tipico_fase_media": row[62],
                "etc_mm_dia_pico": row[63],
                "smi_umbral_riego": row[64],
                "smi_15_umbral_riego": row[65],
                "smi_50_umbral_riego": row[66],
                "smi_ks50": row[67],
                "smi_sensor_control_cm": row[68],
                "taw_mm_arenoso": row[69],
                "taw_mm_franco": row[70],
                "taw_mm_arcilloso": row[71],
                "deficit_mm_umbral_arenoso": row[72],
                "deficit_mm_umbral_franco": row[73],
                "deficit_mm_umbral_arcilloso": row[74],
                "viento_ms_ref_u2": row[75],
                "viento_ms_danio": row[76],
                "viento_ms_polinizacion_max": row[77],
                "precip_anual_opt_min_mm": row[78],
                "precip_anual_opt_max_mm": row[79],
                "precip_anual_abs_min_mm": row[80],
                "precip_anual_abs_max_mm": row[81],
                "ph_opt_min": row[82],
                "ph_opt_max": row[83],
                "ph_abs_min": row[84],
                "ph_abs_max": row[85]
            })

        return crops_list
    finally:
        db.close()

def retrieve_crops_basic_info():
    """Solo los cultivos que sabe recomendar el modelo (modelo_predicciones/modelo_europa.py)."""
    db = SessionLocal()
    try:
        query = text("SELECT id, grupo, cultivo FROM cultivos WHERE id = ANY(:ids) ORDER BY id;")
        result = db.execute(query, {"ids": MODEL_CROP_IDS}).fetchall()

        crops_list = []
        for row in result:
            crops_list.append({
                "id": row[0],
                "grupo": row[1],
                "cultivo": row[2],
            })

        return crops_list
    finally:
        db.close()
