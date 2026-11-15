"""
Idoneidad de cultivos por grupos de balizas con el sistema de reglas.

1. Las balizas elegidas (todas las del cliente si no se dice) se enlazan con el
   mismo grafo que las predicciones (kNN + árbol de expansión mínima,
   GraphManager.create_graph) y se parten en n zonas contiguas y parecidas con
   SKATER. Con n = 1 forman un solo grupo.
2. Para cada zona se juntan sus datos de InfluxDB (series horarias medias de sus
   balizas) y el último año de Meteomatics en el centro de la zona. Cada día se
   usa el dato de las balizas si lo hay y, si no, el de Meteomatics; la lluvia,
   el viento y las horas de frío salen siempre de Meteomatics.
3. Cada cultivo de la tabla `cultivos` se puntúa con las reglas de
   modelo_predicciones/reglas_idoneidad.py.
4. A cada cultivo se le añade el ingreso por hectárea (precio × rendimiento
   medios del país, de Eurostat; modelo_predicciones/rentabilidad.py) y la
   lista se puede ordenar por idoneidad o por ingreso esperado.
"""
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from config import CLIENTE_PAIS, PAIS_POR_DEFECTO
from database.eurostat import EurostatError
from database.get_zone_series import get_zone_series
from database.meteomatics import MeteomaticsError, get_climate_year
from endpoints.balizas import retrieve_balizas_basic_info
from endpoints.crops import retrieve_crops_data
from graph.graphIF import get_balizas_data, order_data
from graph.graphManager import GraphManager
from modelo_predicciones.modelo_europa import wetness
from modelo_predicciones.reglas_idoneidad import SOIL_WATER, WIND_10M_TO_2M, evaluate_crop, smi_from_volumetric
from modelo_predicciones.rentabilidad import crop_economics

logger = logging.getLogger(__name__)

# Días recientes para la humedad del suelo de Meteomatics (si no hay sondas).
SMI_RECENT_DAYS = 30
# Con orden="ingreso": % mínimo de idoneidad para que un cultivo entre en la lista.
DEFAULT_MIN_PERCENT_BY_INCOME = 50.0


def split_zones(cliente_id: str, balizas: list[dict], n: int, start: str, k: int = 2) -> list[list[str]]:
    """Grupos de ids de baliza: SKATER sobre el grafo de las balizas elegidas."""
    ids = [b["id"] for b in balizas]
    n = max(1, min(int(n), len(ids)))
    if n == 1:
        return [ids]

    data = get_balizas_data(cliente_id, start=start)
    data = {b: data[b] for b in ids if b in data}
    baliza_ids, coordinates, parameter_list, parameter_keys = order_data(data)
    positions = GraphManager.normalize_coordinates(coordinates)
    graph = GraphManager.create_graph(positions, k=k)
    labels = GraphManager.skate_clusting(graph, positions, parameter_list, parameter_keys, n)

    zones: dict[int, list[str]] = {}
    for baliza, label in zip(baliza_ids, labels):
        zones.setdefault(label, []).append(baliza)
    return [zones[label] for label in sorted(zones)]


def _source(influx_days: int, meteo_days: int) -> Optional[str]:
    if influx_days and meteo_days:
        return f"influx {influx_days} d + meteomatics {meteo_days} d"
    if influx_days:
        return f"influx {influx_days} d"
    return f"meteomatics {meteo_days} d" if meteo_days else None


def build_conditions(series: dict, climate: Optional[dict], texture: Optional[str], regadio: bool) -> dict:
    """Junta InfluxDB y Meteomatics en las condiciones que esperan las reglas."""
    if climate is not None:
        days = climate["dias"]
        meteo = climate["diario"]
    else:
        # Sin Meteomatics: el mismo año, solo con los días que tengan las balizas.
        today = datetime.now(timezone.utc).date()
        days = [today - timedelta(days=365 - i) for i in range(365)]
        meteo = {}

    def merged(influx_key, meteo_key=None, convert=lambda v: v):
        values, from_influx, from_meteo = [], 0, 0
        meteo_values = meteo.get(meteo_key or influx_key, [None] * len(days))
        for i, day in enumerate(days):
            value = series["diario"].get(day, {}).get(influx_key)
            if value is not None:
                from_influx += 1
            elif meteo_values[i] is not None:
                value = convert(meteo_values[i])
                from_meteo += 1
            values.append(np.nan if value is None else value)
        return np.array(values, dtype=float), _source(from_influx, from_meteo)

    conditions = {"dias": days, "regadio": regadio, "fuentes": {}}
    for key, meteo_key, convert in (
        ("temp_media", None, lambda v: v),
        ("temp_min", None, lambda v: v),
        ("hr", None, lambda v: v),
        # Radiación global (J/m²/día) -> DLI: 1 MJ ≈ 2,2 mol de PAR (igual que la tabla de cultivos).
        ("dli", "radiacion_j", lambda v: v / 1e6 * 2.2),
    ):
        conditions[key], conditions["fuentes"][key] = merged(key, meteo_key, convert)

    wind = meteo.get("viento_ms", [None] * len(days))
    conditions["viento_ms"] = np.array([np.nan if v is None else v * WIND_10M_TO_2M for v in wind], dtype=float)
    conditions["fuentes"]["viento_ms"] = "meteomatics (a 2 m)" if climate is not None else None

    precip = [v for v in meteo.get("precip_mm", []) if v is not None]
    conditions["precip_anual_mm"] = sum(precip) * 365 / len(precip) if len(precip) > 300 else None
    conditions["fuentes"]["precip_anual_mm"] = "meteomatics" if conditions["precip_anual_mm"] is not None else None
    # Series diarias de lluvia y radiación (solo Meteomatics) para el modelo entrenado.
    nan_series = [None] * len(days)
    conditions["precip_mm"] = np.array([np.nan if v is None else v for v in meteo.get("precip_mm", nan_series)], dtype=float)
    conditions["radiacion_mj"] = np.array([np.nan if v is None else v / 1e6 for v in meteo.get("radiacion_j", nan_series)], dtype=float)
    # Humedad del suelo diaria como fracción de saturación (escala del modelo entrenado): la de las
    # sondas si ese día hay dato y, si no, la de Meteomatics (SMI -> % volumétrico con la textura).
    field_capacity, wilting_point = SOIL_WATER.get(texture or "franco", SOIL_WATER["franco"])
    soil_moisture, from_probes = [], 0
    for i, day in enumerate(days):
        volumetric = series["diario"].get(day, {}).get("humedad_vol")
        if volumetric is not None:
            from_probes += 1
        else:
            smi = meteo.get("smi", nan_series)[i]
            volumetric = None if smi is None else wilting_point + smi * (field_capacity - wilting_point)
        value = wetness(volumetric, texture)
        soil_moisture.append(np.nan if value is None else value)
    conditions["humedad_suelo"] = np.array(soil_moisture, dtype=float)
    conditions["fuentes"]["humedad_suelo"] = _source(from_probes, int(np.sum(~np.isnan(conditions["humedad_suelo"]))) - from_probes)
    conditions["horas_frio"] = climate["horas_frio"] if climate is not None else None
    conditions["fuentes"]["horas_frio"] = "meteomatics" if climate is not None else None

    soil = series["suelo"]
    conditions["ph"] = soil.get("ph")
    conditions["fuentes"]["ph"] = "influx"
    conditions["conductividad_us"] = soil.get("conductividad")
    conditions["fuentes"]["conductividad_us"] = "influx"
    conditions["smi"] = smi_from_volumetric(soil.get("humedad_vol"), texture)
    conditions["fuentes"]["smi"] = f"influx (humedad_vol, suelo {texture or 'franco'})"
    if conditions["smi"] is None:
        recent = [v for v in meteo.get("smi", [])[-SMI_RECENT_DAYS:] if v is not None]
        if recent:
            conditions["smi"] = sum(recent) / len(recent)
            conditions["fuentes"]["smi"] = "meteomatics (-15 cm)"
    return conditions


def _summary(conditions: dict) -> dict:
    def mean(key):
        values = conditions[key][~np.isnan(conditions[key])]
        return round(float(values.mean()), 1) if len(values) else None

    temp_min = conditions["temp_min"][~np.isnan(conditions["temp_min"])]
    return {
        "temp_media_anual_c": mean("temp_media"),
        "temp_min_anual_c": round(float(temp_min.min()), 1) if len(temp_min) else None,
        "hr_media_pct": mean("hr"),
        "dli_medio_mol_m2_d": mean("dli"),
        "viento_medio_2m_ms": mean("viento_ms"),
        "precip_anual_mm": None if conditions["precip_anual_mm"] is None else round(conditions["precip_anual_mm"]),
        "horas_frio": conditions["horas_frio"],
        "ph": None if conditions["ph"] is None else round(conditions["ph"], 2),
        "conductividad_us": None if conditions["conductividad_us"] is None else round(conditions["conductividad_us"]),
        "smi": None if conditions["smi"] is None else round(conditions["smi"], 2),
        "fuentes": conditions["fuentes"],
    }


def country_of(cliente_id: str, pais: Optional[str] = None) -> str:
    """Código de país de Eurostat: el pedido o el del cliente."""
    country = (pais or CLIENTE_PAIS.get(cliente_id, PAIS_POR_DEFECTO)).upper()
    if not country.isalpha() or len(country) != 2:
        raise ValueError("pais debe ser un código de dos letras (ES, IE, NO...)")
    return country


def zone_conditions(cliente_id: str, n: int, balizas: Optional[list[str]], start: str, start_zonas: str,
                    regadio: bool) -> tuple:
    """
    Parte las balizas en zonas y junta las condiciones de cada una.

    Returns (zonas, balizas registradas por id, meteomatics_ok), con cada zona
    {"balizas": [ids], "centro": {latitud, longitud}, "textura": str|None,
     "series": get_zone_series, "conditions": build_conditions}.
    meteomatics_ok es None si no hay balizas.
    """
    registered = {b["id"]: b for b in retrieve_balizas_basic_info(cliente_id)}
    if balizas:
        unknown = sorted(set(balizas) - set(registered))
        if unknown:
            raise ValueError(f"balizas que no son del cliente: {', '.join(unknown)}")
        chosen = [registered[b] for b in sorted(set(balizas))]
    else:
        chosen = list(registered.values())
    chosen = [b for b in chosen if b["latitud"] is not None and b["longitud"] is not None]
    if not chosen:
        return [], registered, None

    groups = split_zones(cliente_id, chosen, n, start_zonas)
    centers = []
    for ids in groups:
        lats = [float(registered[b]["latitud"]) for b in ids]
        lons = [float(registered[b]["longitud"]) for b in ids]
        centers.append((sum(lats) / len(lats), sum(lons) / len(lons)))

    try:
        climates = get_climate_year(centers)
        meteomatics_ok = True
    except MeteomaticsError as e:
        logger.warning("Sin datos de Meteomatics: %s", e)
        climates = [None] * len(groups)
        meteomatics_ok = False

    zones = []
    for ids, center, climate in zip(groups, centers, climates):
        textures = Counter(registered[b]["textura_suelo"] for b in ids if registered[b]["textura_suelo"])
        texture = textures.most_common(1)[0][0] if textures else None
        series = get_zone_series(cliente_id, ids, start=start)
        zones.append({
            "balizas": ids,
            "centro": {"latitud": round(center[0], 6), "longitud": round(center[1], 6)},
            "textura": texture,
            "series": series,
            "conditions": build_conditions(series, climate, texture, regadio),
        })
    return zones, registered, meteomatics_ok


def get_suitability(cliente_id: str, n: int = 1, balizas: Optional[list[str]] = None,
                    start: str = "-365d", start_zonas: str = "-24h", cultivos: Optional[list[int]] = None,
                    top: Optional[int] = 10, regadio: bool = False, orden: str = "idoneidad",
                    min_porcentaje: Optional[float] = None, pais: Optional[str] = None) -> dict:
    """
    % de acercamiento de cada zona a los cultivos ideales.

    orden: "idoneidad" (por % de encaje) o "ingreso" (por ingreso esperado en
    €/ha; los cultivos sin precio en Eurostat van al final). min_porcentaje deja
    fuera los cultivos que encajan menos que eso (por defecto 50 con
    orden="ingreso": que un cultivo se pague bien no sirve si no se da).
    pais: código de Eurostat (ES, IE, NO...) para precios y rendimientos; por
    defecto, el del cliente.

    Returns {"meteomatics_disponible": bool, "eurostat_disponible": bool, "pais",
    "orden", "zonas": [{zona, balizas, centro, textura_suelo, condiciones,
    cultivos_actuales, cultivos}]}.
    """
    if orden not in ("idoneidad", "ingreso"):
        raise ValueError("orden debe ser idoneidad o ingreso")
    if min_porcentaje is None:
        min_porcentaje = DEFAULT_MIN_PERCENT_BY_INCOME if orden == "ingreso" else 0.0
    country = country_of(cliente_id, pais)
    eurostat = {"ok": True}

    def with_economics(score: dict) -> dict:
        economics = None
        if eurostat["ok"]:
            try:
                economics = crop_economics(score["id"], score["porcentaje"], country)
            except EurostatError as e:
                logger.warning("Sin datos de Eurostat: %s", e)
                eurostat["ok"] = False
        return {**score, "economia": economics}

    def income(score: dict) -> float:
        economics = score["economia"]
        return -1.0 if not economics or economics["ingreso_esperado_eur_ha"] is None else economics["ingreso_esperado_eur_ha"]

    zones, registered, meteomatics_ok = zone_conditions(cliente_id, n, balizas, start, start_zonas, regadio)
    if not zones:
        return {"meteomatics_disponible": None, "eurostat_disponible": None, "pais": country,
                "orden": orden, "zonas": []}

    all_crops = retrieve_crops_data()
    crops_by_id = {c["id"]: c for c in all_crops}
    crops = [c for c in all_crops if c["id"] in set(cultivos)] if cultivos else all_crops

    result = []
    for label, zone in enumerate(zones):
        ids, texture, series, conditions = zone["balizas"], zone["textura"], zone["series"], zone["conditions"]
        scores = [with_economics(evaluate_crop(crop, conditions)) for crop in crops]
        scores.sort(key=lambda s: -1 if s["porcentaje"] is None else s["porcentaje"], reverse=True)

        # Cómo le va a lo que ya está plantado en esas balizas.
        planted = Counter(registered[b]["cultivo_id"] for b in ids if registered[b]["cultivo_id"])
        current = []
        for crop_id, count in planted.most_common():
            score = next((s for s in scores if s["id"] == crop_id), None)
            if score is None and crop_id in crops_by_id:
                score = with_economics(evaluate_crop(crops_by_id[crop_id], conditions))
            if score is not None:
                current.append({"id": crop_id, "cultivo": score["cultivo"], "balizas": count,
                                "porcentaje": score["porcentaje"], "limitante": score["limitante"],
                                "economia": score["economia"]})

        # La lista recomendada: lo que encaja lo bastante, en el orden pedido
        # (sort es estable: a igual ingreso manda la idoneidad).
        ranked = [s for s in scores if (s["porcentaje"] or 0) >= min_porcentaje]
        if orden == "ingreso":
            ranked.sort(key=income, reverse=True)

        result.append({
            "zona": label,
            "balizas": ids,
            "centro": zone["centro"],
            "textura_suelo": texture,
            "horas_influx": series["horas"],
            "condiciones": _summary(conditions),
            "cultivos_actuales": current,
            "cultivos": ranked[:top] if top else ranked,
        })

    return {"meteomatics_disponible": meteomatics_ok, "eurostat_disponible": eurostat["ok"],
            "pais": country, "orden": orden, "zonas": result}
