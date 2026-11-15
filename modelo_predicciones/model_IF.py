import math
from pathlib import Path

import joblib
import pandas as pd

# 1. Cargar el modelo y los metadatos (ruta relativa a este archivo, no al
#    directorio desde el que se arranque uvicorn)
_DIR = Path(__file__).resolve().parent
modelo_cargado = joblib.load(_DIR / 'modelo_rendimiento_cultivos.joblib')
meta = joblib.load(_DIR / 'metadatos_modelo.joblib')


def _num(value):
    # None (parámetro sin dato) -> NaN numérico; con None la columna sería object.
    return math.nan if value is None else float(value)

# 2. Definir función de recomendación usando únicamente los archivos exportados
def recomendar_con_modelo_exportado(temperatura, humedad_amb, humedad_tierra, horas_luz, ph):
    resultados = []
    for crop in meta['cultivos_disponibles']:
        data_dict = {
            meta['column_mapping']['Temperature']: _num(temperatura),
            meta['column_mapping']['Humidity']: _num(humedad_amb),
            meta['column_mapping']['Soil_Moisture']: _num(humedad_tierra),
            meta['column_mapping']['Sunlight_Hours']: _num(horas_luz),
            meta['column_mapping']['Soil_pH']: _num(ph),
        }

        for c in meta['cultivos_disponibles']:
            col_dummy = f"{meta['target_crop']}_{c}"
            data_dict[col_dummy] = 1 if c == crop else 0

        input_df = pd.DataFrame([data_dict])[meta['model_columns']]
        pred_yield = modelo_cargado.predict(input_df)[0]
        resultados.append((crop, pred_yield))

    resultados.sort(key=lambda x: x[1], reverse=True)
    return resultados

def recomendacion_avanzada(temperatura, humedad_amb, humedad_tierra, horas_luz, ph, cultivo_especifico=None):
    # Obtener todas las predicciones para cada cultivo disponible
    todas_predicciones = recomendar_con_modelo_exportado(temperatura, humedad_amb, humedad_tierra, horas_luz, ph)

    # Caso 1: El usuario quiere saber la predicción de un cultivo específico
    if cultivo_especifico:
        # Normalizar el formato del texto ingresado
        cultivo_buscado = cultivo_especifico.strip().capitalize()
        prediccion = next((prod for crop, prod in todas_predicciones if crop.capitalize() == cultivo_buscado), None)

        if prediccion is not None:
            #print(f"--- PREDICCIÓN INDIVIDUAL ---")
            #print(f"La productividad estimada para el cultivo '{cultivo_buscado}' es de: {prediccion:.2f} toneladas por hectárea.\n")
            return (cultivo_buscado, prediccion)
        else:
            #print(f"El cultivo '{cultivo_especifico}' no se encuentra en el dataset.")
            #print(f"Cultivos disponibles: {meta['cultivos_disponibles']}\n")
            return None

    # Caso 2: El usuario quiere el ranking de los mejores cultivos (Top 10)
    else:
        #print(f"--- TOP MEJORES CULTIVOS ---")
        for rank, (crop, yield_val) in enumerate(todas_predicciones[:10], start=1):
            #print(f"{rank:2d}. {crop:<12} -> Rendimiento estimado: {yield_val:.2f} t/ha")
            pass
        #print()
        return todas_predicciones[:10]
