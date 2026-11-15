"""
Calendario de siembra: cuándo se siembra y se cosecha cada cultivo en una zona.

Datos reales del calendario de cultivos de GGCMI Phase 3 (Jägermeyr et al.,
2021, CC-BY 4.0): día medio de siembra y de madurez por cultivo en celdas de
0,5°, en secano (modelo_predicciones/preparar_calendario.py). Con eso y la
fecha de la consulta se dice qué cultivos toca sembrar ahora.

Límites: una sola fecha media por celda (muchas son el día 1 del mes), no
distingue variedades (la cebada viene como de primavera en casi toda Europa) y
no hay calendario de avena, triticale, tabaco, haba seca ni lino.
"""
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

DATA_PATH = Path(__file__).resolve().parent / "datos" / "calendario_siembra_ggcmi.csv.gz"
SOURCE = "GGCMI Phase 3 crop calendar (secano)"

# id de la tabla `cultivos` -> cultivo de GGCMI. El trigo duro usa el de trigo de
# invierno y el maíz forrajero el de maíz grano (se siembran en las mismas fechas).
GGCMI_CROP = {1: "wwh", 2: "wwh", 3: "bar", 58: "rye", 5: "mai", 61: "mai", 20: "pot", 16: "rap",
              15: "sun", 65: "soy", 7: "sor", 19: "sgb", 8: "ri1"}

# Un cultivo está "en época" desde DAYS_BEFORE días antes de su fecha de siembra
# (hay que preparar el terreno y comprar la semilla) hasta DAYS_AFTER días después.
DAYS_BEFORE = 60
DAYS_AFTER = 15

_table: Optional[dict] = None
_lock = threading.Lock()


def _load() -> dict:
    import pandas as pd

    data = pd.read_csv(DATA_PATH)
    return {(row.cultivo, row.lat, row.lon): (int(row.siembra_dia), int(row.madurez_dia))
            for row in data.itertuples(index=False)}


def _cell(value: float) -> float:
    """Centro de la celda de 0,5° que contiene la coordenada (…, -0.25, 0.25, 0.75, …)."""
    return (int(value // 0.5)) * 0.5 + 0.25


def _month_day(day_of_year: int) -> str:
    return (date(2025, 1, 1) + timedelta(days=day_of_year - 1)).strftime("%m-%d")


def sowing(crop_id, latitude: float, longitude: float, today: date) -> Optional[dict]:
    """
    {"siembra": "MM-DD", "cosecha": "MM-DD", "dias_para_siembra": días hasta la
    próxima siembra (negativo si empezó hace poco), "en_epoca": bool, "fuente"}
    o None si no hay calendario de ese cultivo en esa zona.
    """
    global _table
    crop = GGCMI_CROP.get(crop_id)
    if crop is None:
        return None
    with _lock:
        if _table is None:
            _table = _load()
    days = _table.get((crop, _cell(latitude), _cell(longitude)))
    if days is None:
        return None
    planting, maturity = days
    until = (planting - today.timetuple().tm_yday) % 365  # 0..364 días hasta la próxima siembra
    if until > 365 - DAYS_AFTER:
        until -= 365  # la siembra empezó hace menos de DAYS_AFTER días
    return {
        "siembra": _month_day(planting),
        "cosecha": _month_day(maturity),
        "dias_para_siembra": until,
        "en_epoca": until <= DAYS_BEFORE,
        "fuente": SOURCE,
    }
