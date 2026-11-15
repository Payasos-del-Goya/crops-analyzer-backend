"""
Sistema de reglas: cuánto se acercan las condiciones de un grupo de balizas a
los valores ideales de cada cultivo (tabla `cultivos`, FAO-56 + EcoCrop).

Cada regla compara una condición medida con el rango del cultivo y da una
puntuación de 0 a 1:

- Rangos con óptimo y límites absolutos (temperatura, pH, humedad relativa,
  lluvia): 1 dentro del óptimo, baja en línea recta hasta 0 en el límite
  absoluto (trapecio, como EcoCrop).
- Mínimos que hay que alcanzar (horas de frío, grados-día, luz): 1 al llegar y
  baja hasta 0 en la mitad del mínimo.
- Salinidad: rendimiento relativo de FAO-29, 100 − b·(CEe − umbral).
- Helada: 1 si la mínima queda 2 °C por encima de la letal, 0 si 2 °C por debajo.

La temperatura, la helada, los grados-día, la luz, la humedad y el viento se
miran en la temporada del cultivo: la ventana de días seguidos (duración del
ciclo; 210 días en perennes y 120 en anuales si la tabla no la trae) en la que
la temperatura le va mejor dentro del último año, como hace EcoCrop. Así un
cereal de invierno no sale mal por el calor de julio.

El porcentaje final es 70 % la media ponderada de las reglas y 30 % la peor
regla (ley del mínimo: un cultivo que se hiela no puede salir al 80 %). Una
regla cuenta solo si el cultivo tiene ese valor ideal en la tabla. Si lo tiene
pero no hay dato (ni de las balizas ni de Meteomatics), va a `sin_datos` y
`cobertura` dice qué parte del peso de sus reglas se ha podido evaluar.
"""
import math
from typing import Optional

import numpy as np

# Regla -> peso en la media.
WEIGHTS = {
    "temperatura": 3,
    "helada": 2,
    "horas_frio": 2,
    "grados_dia": 1,
    "luz": 1,
    "humedad_relativa": 1,
    "riesgo_hongos": 1,
    "ph": 2,
    "salinidad": 2,
    "humedad_suelo": 1,
    "viento": 1,
    "precipitacion": 1,
}
WEIGHT_MEAN = 0.7
WEIGHT_MIN = 0.3

DEFAULT_SEASON_PERENNIAL = 210
DEFAULT_SEASON_ANNUAL = 120
MIN_SEASON_COVERAGE = 0.5  # fracción de días con dato para evaluar una ventana

# Contenido de agua (% vol) en capacidad de campo y punto de marchitez por
# textura, para pasar humedad_vol de las sondas a SMI (0 = PMP, 1 = CC).
SOIL_WATER = {
    "arenoso": (12.0, 5.0),
    "franco": (27.0, 12.0),
    "arcilloso": (40.0, 22.0),
}
WIND_10M_TO_2M = 0.748  # FAO-56 ec. 47: u2 = u10 · 4,87 / ln(67,8·10 − 5,42)


def _f(value) -> Optional[float]:
    """Decimal/None/NaN de PostgreSQL o numpy -> float o None."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


def trapezoid(value, opt_min, opt_max, abs_min=None, abs_max=None, margin=None):
    """1 dentro de [opt_min, opt_max], 0 fuera de [abs_min, abs_max], lineal entre medias.
    Si falta un límite absoluto se usa opt ± margin (o sin límite por ese lado)."""
    if value is None or (opt_min is None and opt_max is None):
        return None
    if opt_min is not None and value < opt_min:
        low = abs_min if abs_min is not None else (opt_min - margin if margin else None)
        if low is None:
            return 1.0
        if value <= low or opt_min <= low:
            return 0.0
        return (value - low) / (opt_min - low)
    if opt_max is not None and value > opt_max:
        high = abs_max if abs_max is not None else (opt_max + margin if margin else None)
        if high is None:
            return 1.0
        if value >= high or high <= opt_max:
            return 0.0
        return (high - value) / (high - opt_max)
    return 1.0


def at_least(value, minimum):
    """1 si value >= minimum, 0 si value <= minimum / 2, lineal entre medias."""
    if value is None or minimum is None or minimum <= 0:
        return None
    return float(min(1.0, max(0.0, (value - minimum / 2) / (minimum / 2))))


def _range_text(low, high, unit):
    if low is not None and high is not None:
        return f"{low:g}–{high:g} {unit}".strip()
    if low is not None:
        return f"≥ {low:g} {unit}".strip()
    if high is not None:
        return f"≤ {high:g} {unit}".strip()
    return None


def season_length(crop: dict) -> int:
    days = _f(crop.get("total_fases_d"))
    if days and days > 0:
        return int(min(365, days))
    cycle = (crop.get("ciclo_vida") or "").lower()
    if "perenne" in cycle and "cultivo anual" not in cycle:
        return DEFAULT_SEASON_PERENNIAL
    return DEFAULT_SEASON_ANNUAL


def _window_means(daily_scores: np.ndarray, length: int):
    """Media de cada ventana circular de `length` días (NaN si poca cobertura)."""
    n = len(daily_scores)
    length = min(length, n)
    valid = ~np.isnan(daily_scores)
    values = np.where(valid, daily_scores, 0.0)
    # Circular: el año se repite para que haya ventanas que crucen el 1 de enero.
    values = np.concatenate([values, values[:length]])
    valid = np.concatenate([valid, valid[:length]]).astype(float)
    csum = np.concatenate([[0.0], np.cumsum(values)])
    ccount = np.concatenate([[0.0], np.cumsum(valid)])
    sums = csum[length:length + n] - csum[:n]
    counts = ccount[length:length + n] - ccount[:n]
    with np.errstate(invalid="ignore", divide="ignore"):
        means = sums / counts
    means[counts < MIN_SEASON_COVERAGE * length] = np.nan
    return means


def _season_slice(series: np.ndarray, start: int, length: int) -> np.ndarray:
    idx = (np.arange(min(length, len(series))) + start) % len(series)
    return series[idx]


def _nanmean(values: np.ndarray) -> Optional[float]:
    values = values[~np.isnan(values)]
    return float(values.mean()) if len(values) else None


def evaluate_crop(crop: dict, conditions: dict) -> dict:
    """
    Puntúa un cultivo con las condiciones de un grupo de balizas.

    conditions (de graph.idoneidad.build_conditions):
      dias: [fecha] (365), temp_media/temp_min/hr/dli/viento_ms: np.array(365) con NaN,
      precip_anual_mm, horas_frio, ph, conductividad_us, smi: float o None,
      fuentes: {condición: texto}.
    """
    rules = []
    missing = []  # reglas que el cultivo define pero sin dato de las balizas ni de Meteomatics

    def add(name, score, value, ideal, source, note=None):
        if score is None:
            missing.append(name)
            return
        rule = {
            "regla": name,
            "puntuacion": round(100 * float(score), 1),
            "peso": WEIGHTS[name],
            "valor": value,
            "ideal": ideal,
            "fuente": source,
        }
        if note:
            rule["nota"] = note
        rules.append(rule)

    sources = conditions.get("fuentes", {})
    days = conditions["dias"]
    temp = conditions["temp_media"]

    t_opt_min, t_opt_max = _f(crop.get("temp_aire_opt_min_c")), _f(crop.get("temp_aire_opt_max_c"))
    t_abs_min, t_abs_max = _f(crop.get("temp_aire_min_abs_c")), _f(crop.get("temp_aire_max_abs_c"))
    length = season_length(crop)

    # --- Temporada: ventana con mejor temperatura -----------------------------------
    season = None
    if t_opt_min is not None or t_opt_max is not None:
        daily = np.array([
            np.nan if np.isnan(t) else trapezoid(t, t_opt_min, t_opt_max, t_abs_min, t_abs_max, margin=10)
            for t in temp
        ], dtype=float)
        means = _window_means(daily, length)
        if np.all(np.isnan(means)):
            missing.append("temperatura")
        else:
            start = int(np.nanargmax(means))
            season = (start, length)
            season_temp = _nanmean(_season_slice(temp, start, length))
            add("temperatura", means[start], round(season_temp, 1),
                _range_text(t_opt_min, t_opt_max, "°C"), sources.get("temp_media"),
                note=f"media de la temporada de {length} días con mejor temperatura")

    def in_season(series):
        if season is None:
            return series
        return _season_slice(series, *season)

    # --- Helada en la temporada -------------------------------------------------------
    lethal = _f(crop.get("temp_letal_helada_c"))
    season_min = in_season(conditions["temp_min"])
    season_min = season_min[~np.isnan(season_min)]
    if lethal is not None:
        t_min = float(season_min.min()) if len(season_min) else None
        score = None if t_min is None else min(1.0, max(0.0, (t_min - (lethal - 2)) / 4))
        add("helada", score, None if t_min is None else round(t_min, 1), f"> {lethal + 2:g} °C (letal {lethal:g} °C)", sources.get("temp_min"),
            note="mínima de la temporada")

    # --- Horas de frío (año) ----------------------------------------------------------
    chill_min = _f(crop.get("horas_frio_min_h"))
    chill = conditions.get("horas_frio")
    if chill_min:
        add("horas_frio", at_least(chill, chill_min), chill,
            _range_text(chill_min, _f(crop.get("horas_frio_max_h")), "h"), sources.get("horas_frio"))

    # --- Grados-día en la temporada ---------------------------------------------------
    base, gdd_min = _f(crop.get("gdd_base_c")), _f(crop.get("gdd_acum_ciclo_min"))
    if base is not None and gdd_min:
        season_t = in_season(temp)
        valid = season_t[~np.isnan(season_t)]
        # Días sin dato: se extrapola con la media de los que hay.
        gdd = float(np.clip(valid - base, 0, None).mean() * len(season_t)) if len(valid) else None
        add("grados_dia", at_least(gdd, gdd_min), None if gdd is None else round(gdd),
            _range_text(gdd_min, _f(crop.get("gdd_acum_ciclo_max")), "°C·día"), sources.get("temp_media"))

    # --- Luz (DLI) en la temporada ----------------------------------------------------
    dli_min, dli_opt = _f(crop.get("luz_solar_dli_min_mol_m2_d")), _f(crop.get("luz_solar_dli_opt_mol_m2_d"))
    dli = _nanmean(in_season(conditions["dli"]))
    if dli_min or dli_opt:
        if dli is None:
            score = None
        elif dli_min and dli_opt and dli_opt > dli_min:
            # 0 en dli_min/2, 0,5 en dli_min, 1 en el óptimo.
            score = 1.0 if dli >= dli_opt else (
                0.5 + 0.5 * (dli - dli_min) / (dli_opt - dli_min) if dli >= dli_min
                else 0.5 * at_least(dli, dli_min))
        else:
            score = at_least(dli, dli_opt or dli_min)
        add("luz", score, None if dli is None else round(dli, 1), _range_text(dli_min, dli_opt, "mol/m²/día"), sources.get("dli"))

    # --- Humedad relativa y riesgo de hongos en la temporada -------------------------
    hr_series = in_season(conditions["hr"])
    hr = _nanmean(hr_series)
    hr_min, hr_max = _f(crop.get("hr_opt_min_pct")), _f(crop.get("hr_opt_max_pct"))
    if hr_min is not None or hr_max is not None:
        add("humedad_relativa", trapezoid(hr, hr_min, hr_max, margin=25), None if hr is None else round(hr, 1),
            _range_text(hr_min, hr_max, "%"), sources.get("hr"))
    risk = _f(crop.get("hr_riesgo_enfermedad_pct"))
    if risk is not None:
        valid = hr_series[~np.isnan(hr_series)]
        share = float((valid >= risk).mean()) if len(valid) else None
        # 0 días: 1; un 30 % de los días o más: 0.
        add("riesgo_hongos", None if share is None else max(0.0, 1 - share / 0.3),
            None if share is None else round(100 * share, 1),
            f"días con HR ≥ {risk:g} %: 0 %", sources.get("hr"), note="% de días de la temporada")

    # --- Suelo: pH, salinidad, humedad -----------------------------------------------
    ph = conditions.get("ph")
    if _f(crop.get("ph_opt_min")) is not None or _f(crop.get("ph_opt_max")) is not None:
        add("ph", trapezoid(ph, _f(crop.get("ph_opt_min")), _f(crop.get("ph_opt_max")),
                            _f(crop.get("ph_abs_min")), _f(crop.get("ph_abs_max")), margin=1),
            None if ph is None else round(ph, 2),
            _range_text(_f(crop.get("ph_opt_min")), _f(crop.get("ph_opt_max")), ""), sources.get("ph"))

    ce = conditions.get("conductividad_us")
    ce_min, ce_max = _f(crop.get("conductividad_us_umbral_cee_min")), _f(crop.get("conductividad_us_umbral_cee_max"))
    b_min, b_max = _f(crop.get("b_min_pct_por_ds_m")), _f(crop.get("b_max_pct_por_ds_m"))
    if (ce_min or ce_max) and (b_min or b_max):
        threshold = float(np.mean([v for v in (ce_min, ce_max) if v]))
        slope = float(np.mean([v for v in (b_min, b_max) if v]))
        # FAO-29: rendimiento = 100 − b·(CEe − umbral), b en % por dS/m.
        yield_pct = None if ce is None else 100 - slope * max(0.0, ce - threshold) / 1000
        add("salinidad", None if ce is None else max(0.0, min(1.0, yield_pct / 100)),
            None if ce is None else round(ce),
            f"≤ {threshold:g} µS/cm (CEe)", sources.get("conductividad_us"),
            note="lectura del sensor tratada como CEe (aproximación)")

    smi = conditions.get("smi")
    smi_irrigate = _f(crop.get("smi_umbral_riego"))
    if smi_irrigate:
        if smi is None:
            score = None
        elif smi > 1.1:  # por encima de capacidad de campo: encharcado
            score = max(0.0, 1 - (smi - 1.1) / 0.3)
        else:
            score = min(1.0, max(0.0, smi / smi_irrigate))  # Ks lineal (FAO-56 ec. 84)
        add("humedad_suelo", score, None if smi is None else round(smi, 2), f"SMI {smi_irrigate:g}–1.1", sources.get("smi"),
            note="SMI: 0 = punto de marchitez, 1 = capacidad de campo")

    # --- Viento en la temporada ------------------------------------------------------
    damage = _f(crop.get("viento_ms_danio"))
    wind = in_season(conditions["viento_ms"])
    valid = wind[~np.isnan(wind)]
    if damage is not None:
        share = float((valid >= damage).mean()) if len(valid) else None
        # 0 días: 1; un 10 % de los días o más: 0.
        add("viento", None if share is None else max(0.0, 1 - share / 0.1),
            None if share is None else round(100 * share, 1),
            f"días con viento medio ≥ {damage:g} m/s: 0 %", sources.get("viento_ms"),
            note="% de días de la temporada")

    # --- Lluvia anual ----------------------------------------------------------------
    precip = conditions.get("precip_anual_mm")
    p_min, p_max = _f(crop.get("precip_anual_opt_min_mm")), _f(crop.get("precip_anual_opt_max_mm"))
    if (p_min is not None or p_max is not None) and not conditions.get("regadio"):
        add("precipitacion", trapezoid(precip, p_min, p_max, _f(crop.get("precip_anual_abs_min_mm")),
                                       _f(crop.get("precip_anual_abs_max_mm"))),
            None if precip is None else round(precip), _range_text(p_min, p_max, "mm/año"), sources.get("precip_anual_mm"))

    # --- Resultado ---------------------------------------------------------------------
    if rules:
        total_weight = sum(r["peso"] for r in rules)
        mean = sum(r["puntuacion"] * r["peso"] for r in rules) / total_weight
        worst = min(rules, key=lambda r: r["puntuacion"])
        percent = WEIGHT_MEAN * mean + WEIGHT_MIN * worst["puntuacion"]
        # Parte del peso de las reglas que el cultivo define que se ha podido evaluar.
        coverage = 100 * total_weight / (total_weight + sum(WEIGHTS[m] for m in missing))
    else:
        worst, percent, coverage = None, None, 0.0

    result = {
        "id": crop.get("id"),
        "cultivo": crop.get("cultivo"),
        "grupo": crop.get("grupo"),
        "porcentaje": None if percent is None else round(percent, 1),
        "cobertura": round(coverage, 1),
        "limitante": None if worst is None else worst["regla"],
        "sin_datos": missing,
        "temporada": None,
        "reglas": sorted(rules, key=lambda r: r["puntuacion"]),
    }
    if season is not None:
        start, length = season
        result["temporada"] = {
            "inicio": days[start].strftime("%m-%d"),
            "fin": days[(start + length - 1) % len(days)].strftime("%m-%d"),
            "dias": length,
        }
    return result


def smi_from_volumetric(humedad_vol: Optional[float], texture: Optional[str]) -> Optional[float]:
    """humedad volumétrica (%) -> SMI con los valores típicos de la textura (franco por defecto)."""
    if humedad_vol is None:
        return None
    fc, wp = SOIL_WATER.get(texture or "franco", SOIL_WATER["franco"])
    return max(0.0, (humedad_vol - wp) / (fc - wp))
