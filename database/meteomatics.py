"""
Clima de Meteomatics para lo que no sale de InfluxDB.

Las balizas no miden lluvia anual, viento ni radiación global, y su histórico
(meses) no cubre un año entero, que es lo que hace falta para las horas de frío,
las heladas o la temporada de cultivo. Para cada punto se piden los últimos
365 días en dos peticiones (una diaria con varios parámetros y otra horaria con
la temperatura, para las horas de frío), con todos los puntos en la misma
petición. El resultado se guarda en memoria unas horas: la cuenta tiene un
límite de 1000 peticiones al día.

También se guarda en disco (cache/meteomatics.json, fuera de git): sobrevive a
los reinicios del backend y, si Meteomatics no responde, se usa el último año
que se descargó de cada punto en vez de quedarse sin recomendación.
"""
import base64
import json
import logging
import threading
import time
import urllib.error
from pathlib import Path
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from config import METEOMATICS_PASSWORD, METEOMATICS_URL, METEOMATICS_USER

logger = logging.getLogger(__name__)

# Parámetro de Meteomatics -> clave propia. Los "_24h" se fechan al final del
# periodo: el valor del día D a las 00:00Z es el del día D-1.
DAILY_PARAMETERS = {
    "t_mean_2m_24h:C": "temp_media",
    "t_min_2m_24h:C": "temp_min",
    "t_max_2m_24h:C": "temp_max",
    "precip_24h:mm": "precip_mm",
    "wind_speed_mean_10m_24h:ms": "viento_ms",
    "wind_gusts_10m_24h:ms": "racha_ms",
    "global_rad_24h:J": "radiacion_j",
    "relative_humidity_mean_2m_24h:p": "hr",
    "soil_moisture_index_-15cm:idx": "smi",
    "evapotranspiration_24h:mm": "et0_mm",
}
HOURLY_PARAMETER = "t_2m:C"

CACHE_SECONDS = 6 * 3600
ROUND_DEGREES = 2
TIMEOUT_SECONDS = 30

DISK_PATH = Path(__file__).resolve().parent.parent / "cache" / "meteomatics.json"

_cache: dict = {}
_lock = threading.Lock()
_disk: Optional[dict] = None  # "lat,lon" -> {"fin", "guardado", "clima"}; se carga al primer uso


class MeteomaticsError(RuntimeError):
    pass


def _get(path: str) -> str:
    request = urllib.request.Request(f"{METEOMATICS_URL}/{path}")
    credentials = base64.b64encode(f"{METEOMATICS_USER}:{METEOMATICS_PASSWORD}".encode()).decode()
    request.add_header("Authorization", f"Basic {credentials}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read().decode()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        raise MeteomaticsError(f"Meteomatics HTTP {e.code}: {detail}") from e
    except OSError as e:  # URLError, y socket.timeout si tarda en responder
        raise MeteomaticsError(f"Meteomatics no responde: {e}") from e


def _points(coordinates: list[tuple[float, float]]) -> str:
    return "+".join(f"{lat:.2f},{lon:.2f}" for lat, lon in coordinates)


def _parse(text: str):
    """Filas (lat, lon, fecha, parametro, valor) de la respuesta JSON de Meteomatics."""
    for series in json.loads(text).get("data", []):
        for point in series["coordinates"]:
            for item in point["dates"]:
                value = item["value"]
                # -999 y similares: sin dato
                value = None if value is None or value <= -999 else float(value)
                when = datetime.fromisoformat(item["date"].replace("Z", "+00:00"))
                yield point["lat"], point["lon"], when, series["parameter"], value


def _nearest(coordinates, lat, lon) -> int:
    # Meteomatics devuelve las coordenadas redondeadas: se asigna al punto pedido más cercano.
    return min(range(len(coordinates)),
               key=lambda i: (coordinates[i][0] - lat) ** 2 + (coordinates[i][1] - lon) ** 2)


def _fetch(points: list[tuple[float, float]], end: date) -> list[dict]:
    first = end - timedelta(days=365)
    days = [first + timedelta(days=i) for i in range(365)]
    index = {d: i for i, d in enumerate(days)}
    result = [{"dias": days, "diario": {k: [None] * 365 for k in DAILY_PARAMETERS.values()}, "horas_frio": 0}
              for _ in points]
    locations = _points(points)
    # Diario: el valor de las 00:00Z del día D+1 es el del día D.
    daily_path = (f"{first + timedelta(days=1)}T00:00:00Z--{end}T00:00:00Z:P1D/"
                  f"{','.join(DAILY_PARAMETERS)}/{locations}/json")
    # Horario: solo la temperatura, para las horas de frío (< 7,2 °C).
    hourly_path = f"{first}T00:00:00Z--{end}T00:00:00Z:PT1H/{HOURLY_PARAMETER}/{locations}/json"

    # Meteomatics tarda varios segundos en cada una: las dos a la vez.
    with ThreadPoolExecutor(max_workers=2) as pool:
        daily_text, hourly_text = pool.map(_get, [daily_path, hourly_path])

    for lat, lon, valid, parameter, value in _parse(daily_text):
        i = index.get(valid.date() - timedelta(days=1))
        if i is not None and parameter in DAILY_PARAMETERS:
            result[_nearest(points, lat, lon)]["diario"][DAILY_PARAMETERS[parameter]][i] = value
    for lat, lon, valid, _, value in _parse(hourly_text):
        if value is not None and value < 7.2 and valid.date() < end:
            result[_nearest(points, lat, lon)]["horas_frio"] += 1
    return result


def _key(point) -> str:
    return f"{point[0]:.2f},{point[1]:.2f}"


def _disk_entries() -> dict:
    """Lo guardado en disco (con _lock tomado)."""
    global _disk
    if _disk is None:
        _disk = {}
        try:
            for key, entry in json.loads(DISK_PATH.read_text()).items():
                entry["clima"]["dias"] = [date.fromisoformat(d) for d in entry["clima"]["dias"]]
                _disk[key] = entry
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.warning("No se puede leer %s (%s): se empieza de cero", DISK_PATH, e)
    return _disk


def _save_disk() -> None:
    """Escribe la caché de disco (con _lock tomado). Si no se puede, solo avisa."""
    data = {key: {**entry, "clima": {**entry["clima"], "dias": [d.isoformat() for d in entry["clima"]["dias"]]}}
            for key, entry in _disk.items()}
    try:
        DISK_PATH.parent.mkdir(exist_ok=True)
        tmp = DISK_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(DISK_PATH)
    except OSError as e:
        logger.warning("No se puede guardar %s: %s", DISK_PATH, e)


def get_climate_year(coordinates: list[tuple[float, float]], end: Optional[date] = None) -> list[dict]:
    """
    Último año de clima de cada punto (lat, lon), en el mismo orden.

    Devuelve por punto {"dias": [fecha, ...], "diario": {clave: [valor|None, ...]},
    "horas_frio": horas < 7,2 °C en los 365 días}. Lanza MeteomaticsError si falla.

    Los puntos se redondean a 0,01° (~1 km, la resolución del clima) y se
    guardan por separado: dos zonas de la misma finca comparten la petición.
    """
    end = end or datetime.now(timezone.utc).date()  # último día completo: end - 1
    points = [(round(lat, ROUND_DEGREES), round(lon, ROUND_DEGREES)) for lat, lon in coordinates]
    now = time.time()
    with _lock:
        disk = _disk_entries()
        missing = set()
        for p in points:
            if (p, end) in _cache and now - _cache[(p, end)][0] < CACHE_SECONDS:
                continue
            saved = disk.get(_key(p))
            if saved and saved["fin"] == end.isoformat() and now - saved["guardado"] < CACHE_SECONDS:
                _cache[(p, end)] = (saved["guardado"], saved["clima"])  # descargado antes de reiniciar
                continue
            missing.add(p)
        missing = sorted(missing)
    if missing:
        try:
            fetched = _fetch(missing, end)
        except MeteomaticsError as e:
            with _lock:
                saved = [disk.get(_key(p)) for p in missing]
            if not all(saved):
                raise
            # Sin Meteomatics: el último año descargado de cada punto (no se guarda en _cache
            # para volver a intentarlo en la siguiente consulta).
            logger.warning("%s: se usa el clima guardado en disco (descargado hasta %s)",
                           e, min(s["fin"] for s in saved))
            fallback = {p: s["clima"] for p, s in zip(missing, saved)}
            with _lock:
                return [fallback[p] if p in fallback else _cache[(p, end)][1] for p in points]
        with _lock:
            for point, climate in zip(missing, fetched):
                _cache[(point, end)] = (now, climate)
                disk[_key(point)] = {"fin": end.isoformat(), "guardado": now, "clima": climate}
            _save_disk()
    with _lock:
        return [_cache[(p, end)][1] for p in points]
