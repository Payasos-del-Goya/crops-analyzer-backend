"""
Precios y rendimientos medios de cada cultivo por país, de Eurostat.

API pública de Eurostat (sin clave), dos tablas anuales:
- apri_ap_crpouta: precio de venta del agricultor, en € por 100 kg.
- apro_cpsh1: superficie, producción y rendimiento (t/ha) de cada cultivo.

Se pide cada tabla una vez con todos los países y se guarda en memoria un día
(Eurostat las actualiza unas pocas veces al año). De cada serie se usa la media
de los últimos años con dato; si el país no publica ese cultivo se usa la
mediana de los países europeos que sí, y se dice en `fuente`.
"""
import json
import logging
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

EUROSTAT_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
PRICES_DATASET = "apri_ap_crpouta"
PRODUCTION_DATASET = "apro_cpsh1"

YEARS_BACK = 6          # años que se piden
YEARS_AVERAGED = 3      # últimos años con dato que se promedian
MIN_COUNTRIES = 3       # países mínimos para dar una mediana europea
MIN_OPEN_AIR_SHARE = 0.2  # parte mínima de la superficie al aire libre para usar su rendimiento
CACHE_SECONDS = 24 * 3600
TIMEOUT_SECONDS = 30

# Códigos de `geo` que son agregados, no países: fuera de la mediana.
_AGGREGATES = {"EU", "EU27_2020", "EU28", "EA", "EA19", "EA20"}
# Precios que no vienen por 100 kg.
_PRICE_PER_TONNE = {"02400000"}  # remolacha azucarera: por 1000 kg

_cache: dict = {}
_lock = threading.Lock()


class EurostatError(RuntimeError):
    pass


def _get(dataset: str, **filters) -> dict:
    query = urllib.parse.urlencode({"format": "JSON", "lang": "EN", **filters}, doseq=True)
    try:
        with urllib.request.urlopen(f"{EUROSTAT_URL}/{dataset}?{query}", timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        raise EurostatError(f"Eurostat HTTP {e.code} en {dataset}") from e
    except (OSError, ValueError) as e:  # URLError, socket.timeout o JSON roto
        raise EurostatError(f"Eurostat no responde ({dataset}): {e}") from e


def _rows(data: dict):
    """(dict dimensión -> código, valor) de una respuesta JSON-stat."""
    dims = data["id"]
    sizes = data["size"]
    codes = []
    for dim in dims:
        index = data["dimension"][dim]["category"]["index"]
        codes.append(sorted(index, key=index.get))
    for flat, value in data["value"].items():
        if value is None:
            continue
        flat = int(flat)
        position = []
        for size in reversed(sizes):
            flat, i = divmod(flat, size)
            position.append(i)
        position.reverse()
        yield {dim: codes[d][i] for d, (dim, i) in enumerate(zip(dims, position))}, float(value)


def _load() -> dict:
    """{"precios": {(producto, país): {año: €/t}}, "rendimientos": {(cultivo, país): {año: t/ha}}}"""
    since = datetime.now(timezone.utc).year - YEARS_BACK
    prices = {}
    for row, value in _rows(_get(PRICES_DATASET, currency="EUR", sinceTimePeriod=since)):
        if value <= 0:
            continue
        per_tonne = value if row["prod_veg"] in _PRICE_PER_TONNE else value * 10
        prices.setdefault((row["prod_veg"], row["geo"]), {})[int(row["time"])] = per_tonne

    raw = {}
    for row, value in _rows(_get(PRODUCTION_DATASET, sinceTimePeriod=since,
                                 strucpro=["YLD_HUMD_EU_T_HA", "HPRD_HUMD_EU_THS_T", "AR_THS_HA"])):
        raw.setdefault((row["crops"], row["geo"], int(row["time"])), {})[row["strucpro"]] = value
    # Cultivos con serie aparte "bajo cristal" (V3100S, V3200S, V2300S, V3600S, S0000S).
    under_glass = {crop[:-1] for crop, _, _ in raw if crop.endswith("S")}
    yields = {}
    for (crop, geo, year), values in raw.items():
        area, production = values.get("AR_THS_HA"), values.get("HPRD_HUMD_EU_THS_T")
        if crop in under_glass:
            # Tomate, pepino, lechuga, pimiento y fresa traen aparte lo cultivado
            # bajo cristal (código + "S"), con rendimientos varias veces mayores:
            # se resta para quedarse con el rendimiento al aire libre, que es el
            # que vale para una finca con balizas. Si casi todo es invernadero
            # (o el país no da el desglose ese año) no hay rendimiento de ese país
            # y se usará la mediana de los que sí.
            glass = raw.get((crop + "S", geo, year), {})
            glass_area, glass_production = glass.get("AR_THS_HA"), glass.get("HPRD_HUMD_EU_THS_T")
            if not area or not production or glass_area is None or glass_production is None:
                continue
            if area - glass_area < MIN_OPEN_AIR_SHARE * area:
                continue
            value = (production - glass_production) / (area - glass_area)
        else:
            # Muchos frutales no traen rendimiento: producción / superficie.
            value = values.get("YLD_HUMD_EU_T_HA") or (production / area if area and production else None)
        if value and value > 0:
            yields.setdefault((crop, geo), {})[year] = value
    return {"precios": prices, "rendimientos": yields}


def _tables() -> dict:
    now = time.time()
    with _lock:
        cached = _cache.get("tables")
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]
    tables = _load()
    with _lock:
        _cache["tables"] = (now, tables)
    return tables


def _recent_mean(series: dict) -> Optional[tuple]:
    years = sorted(series)[-YEARS_AVERAGED:]
    if not years:
        return None
    return sum(series[y] for y in years) / len(years), years[0], years[-1]


def _lookup(table: dict, codes: list, country: str) -> Optional[dict]:
    """Media reciente del primer código con dato en el país; si no, mediana europea."""
    for code in codes:
        found = _recent_mean(table.get((code, country), {}))
        if found:
            value, first, last = found
            period = str(first) if first == last else f"{first}–{last}"
            return {"valor": value, "codigo": code, "fuente": f"Eurostat {country} {period}"}
    for code in codes:
        median = _european_median(table, code)
        if median is not None:
            return {"valor": median[0], "codigo": code,
                    "fuente": f"Eurostat, mediana de {median[1]} países (sin dato de {country})"}
    return None


def _european_median(table: dict, code: str) -> Optional[tuple]:
    """(mediana de las medias recientes de cada país, nº de países) o None si hay pocos."""
    means = [m[0] for (c, geo), series in table.items()
             if c == code and geo not in _AGGREGATES and (m := _recent_mean(series))]
    return (statistics.median(means), len(means)) if len(means) >= MIN_COUNTRIES else None


def get_price(country: str, price_codes: list) -> Optional[dict]:
    """
    Precio de venta de un cultivo en un país: {valor €/t, codigo, fuente,
    europeo: mediana europea del mismo producto en €/t (o None)}. Lanza EurostatError.
    """
    prices = _tables()["precios"]
    price = _lookup(prices, price_codes, country)
    if price is None:
        return None
    median = _european_median(prices, price["codigo"])
    return {**price, "europeo": None if median is None else median[0]}


def get_market(country: str, price_codes: list, yield_codes: list) -> dict:
    """
    {"precio": {valor €/t, codigo, fuente} | None, "rendimiento": {valor t/ha, ...} | None}
    para un cultivo en un país (código de Eurostat: ES, IE, NO...). Lanza EurostatError.
    """
    tables = _tables()
    return {
        "precio": _lookup(tables["precios"], price_codes, country) if price_codes else None,
        "rendimiento": _lookup(tables["rendimientos"], yield_codes, country) if yield_codes else None,
    }
