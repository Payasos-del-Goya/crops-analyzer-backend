"""
Modelo de recomendación entrenado con datos reales de Europa.

Datos de entrenamiento (modelo_predicciones/entrenar_modelo_europa.ipynb):
rendimientos y superficies por región NUTS2 y año de Eurostat (apro_cpshr,
2010-2024) con el clima de ese año de NASA POWER en la región. Dos modelos de
gradient boosting, los dos con el cultivo y nueve variables de clima:

- cuota: qué parte de la tierra dedican a cada cultivo las regiones con ese
  clima (superficie del cultivo / superficie de los cultivos del modelo). Es
  lo que hacen de verdad los agricultores, que ya cuentan con los costes, la
  rotación y el mercado.
- rendimiento: t/ha esperadas (relativas a la mediana europea del cultivo).

El backend calcula las mismas nueve variables con el último año de la zona
(balizas + Meteomatics). El orden lo decide graph/recomendacion.py: la cuota,
corregida por lo que el cultivo rinde y se paga aquí frente a lo habitual.

El .joblib sale del notebook. Si no está o se guardó con otra versión de
scikit-learn y no carga, el modelo se entrena aquí al arrancar con el mismo
conjunto de datos (modelo_predicciones/datos/) y los mismos parámetros.
"""
import logging
import threading
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_DIR = Path(__file__).resolve().parent
MODEL_PATH = _DIR / "modelo_cultivos_europa.joblib"
DATA_PATH = _DIR / "datos" / "entrenamiento_eurostat_power.csv.gz"

# id de la tabla `cultivos` y nombre de cada código de cultivo de Eurostat (apro_cpshr).
CULTIVOS = {
    "C1110": (1, "Trigo blando de invierno"), "C1120": (2, "Trigo duro"), "C1300": (3, "Cebada"),
    "C1410": (4, "Avena"), "C1500": (5, "Maíz grano"), "C1700": (7, "Sorgo grano"), "C2000": (8, "Arroz"),
    "C1200": (58, "Centeno"), "C1600": (59, "Triticale"), "R1000": (20, "Patata"),
    "R2000": (19, "Remolacha azucarera"), "I1110": (16, "Colza"), "I1120": (15, "Girasol"),
    "I1130": (65, "Soja"), "I3000": (68, "Tabaco"), "P1200": (11, "Haba seca"),
    "I1140": (67, "Lino"), "G3000": (61, "Maíz forrajero (ensilado)"),
}
VARIABLES = ["t_anual", "t_mes_frio", "t_mes_calido", "t_primavera", "lluvia_anual_mm", "lluvia_abr_sep_mm",
             "radiacion_mj_dia", "radiacion_abr_sep_mj_dia", "hr_media"]
MIN_DAYS_PER_MONTH = 15  # días con dato para dar la media de un mes

_bundle: Optional[dict] = None
_lock = threading.Lock()


def train(data) -> dict:
    """Los dos modelos a partir del conjunto de datos (DataFrame). Igual que el notebook."""
    import sklearn
    from sklearn.ensemble import HistGradientBoostingRegressor

    codes = sorted(CULTIVOS)
    data = data.assign(cultivo_cod=data["cultivo"].map(codes.index))
    columns = ["cultivo_cod"] + VARIABLES
    # Cuota de superficie de cada cultivo en su región y año (suman 1 entre los cultivos del modelo).
    share = data["area_kha"] / data.groupby(["region", "anio"])["area_kha"].transform("sum")
    land_share = HistGradientBoostingRegressor(categorical_features=[0], max_iter=400, learning_rate=0.06, random_state=0)
    land_share.fit(data[columns], share)

    with_yield = data[data["rendimiento_t_ha"].notna()]
    median = with_yield.groupby("cultivo")["rendimiento_t_ha"].median()
    relative = with_yield["rendimiento_t_ha"] / with_yield["cultivo"].map(median)
    crop_yield = HistGradientBoostingRegressor(categorical_features=[0], max_iter=400, learning_rate=0.06, random_state=0)
    crop_yield.fit(with_yield[columns], relative)

    meta = {
        "columnas": columns, "variables": VARIABLES, "codigos": codes,
        "cultivos": {c: {"id": CULTIVOS[c][0], "nombre": CULTIVOS[c][1], "mediana_t_ha": float(median[c])} for c in codes},
        "rangos_entrenamiento": {v: [float(data[v].quantile(0.01)), float(data[v].quantile(0.99))] for v in VARIABLES},
        "sklearn": sklearn.__version__,
        "datos": "Eurostat apro_cpshr 2010-2024 (NUTS2) + NASA POWER mensual",
    }
    return {"cuota": land_share, "rendimiento": crop_yield, "meta": meta}


def _load() -> dict:
    import joblib
    import pandas as pd

    if MODEL_PATH.exists():
        try:
            bundle = joblib.load(MODEL_PATH)
            # Una predicción de prueba: un modelo de otra versión puede cargar y fallar al usarlo.
            probe = pd.DataFrame([[0] + [0.0] * len(VARIABLES)], columns=bundle["meta"]["columnas"])
            bundle["cuota"].predict(probe)
            bundle["rendimiento"].predict(probe)
            return bundle
        except Exception as e:  # versión distinta de scikit-learn, archivo roto...
            logger.warning("No se puede usar %s (%s): se entrena con %s", MODEL_PATH.name, e, DATA_PATH.name)
    bundle = train(pd.read_csv(DATA_PATH))
    bundle["meta"]["entrenado_al_arrancar"] = True
    return bundle


def model() -> dict:
    global _bundle
    with _lock:
        if _bundle is None:
            _bundle = _load()
        return _bundle


def climate_variables(days: list, temp: np.ndarray, hr: np.ndarray, precip: np.ndarray,
                      radiation_mj: np.ndarray) -> Optional[dict]:
    """
    Las nueve variables del modelo a partir de las series diarias del último año
    (misma definición que en el entrenamiento, que usa medias mensuales).
    None si a algún mes le faltan datos.
    """
    months = np.array([d.month for d in days])

    def monthly(series):
        means = []
        for month in range(1, 13):
            values = series[months == month]
            values = values[~np.isnan(values)]
            if len(values) < MIN_DAYS_PER_MONTH:
                return None
            means.append(float(values.mean()))
        return means

    t, h, p, r = monthly(temp), monthly(hr), monthly(precip), monthly(radiation_mj)
    if t is None or h is None or p is None or r is None:
        return None
    month_days = [31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    rain = [p[m] * month_days[m] for m in range(12)]  # media diaria del mes × días del mes
    return {
        "t_anual": sum(t) / 12, "t_mes_frio": min(t), "t_mes_calido": max(t), "t_primavera": sum(t[2:6]) / 4,
        "lluvia_anual_mm": sum(rain), "lluvia_abr_sep_mm": sum(rain[3:9]),
        "radiacion_mj_dia": sum(r) / 12, "radiacion_abr_sep_mj_dia": sum(r[3:9]) / 6,
        "hr_media": sum(h) / 12,
    }


def predict(variables: dict) -> list:
    """[{id, cultivo, cuota (0-1, suman 1), rendimiento_t_ha, rendimiento_relativo}] de cada cultivo del modelo."""
    import pandas as pd

    bundle = model()
    meta = bundle["meta"]
    rows = [[code] + [variables[v] for v in meta["variables"]] for code in range(len(meta["codigos"]))]
    frame = pd.DataFrame(rows, columns=meta["columnas"])
    share = np.clip(bundle["cuota"].predict(frame), 0.0, None)
    share = share / share.sum() if share.sum() > 0 else share
    relative = bundle["rendimiento"].predict(frame)
    result = []
    for i, code in enumerate(meta["codigos"]):
        crop = meta["cultivos"][code]
        result.append({
            "id": crop["id"], "cultivo": crop["nombre"],
            "cuota": float(share[i]),
            "rendimiento_t_ha": float(max(0.0, relative[i]) * crop["mediana_t_ha"]),
            # Frente a la mediana europea del cultivo: 1 = lo habitual.
            "rendimiento_relativo": float(max(0.0, relative[i])),
        })
    return result


def out_of_range(variables: dict) -> list:
    """Variables fuera de lo visto al entrenar (percentiles 1-99): ahí el modelo extrapola."""
    ranges = model()["meta"]["rangos_entrenamiento"]
    return [v for v, (low, high) in ranges.items() if not low <= variables[v] <= high]
