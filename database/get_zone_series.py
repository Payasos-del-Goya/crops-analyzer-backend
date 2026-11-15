"""
Series de un grupo de balizas en InfluxDB para comparar con los cultivos.

Para cada campo, la media horaria de todas las balizas del grupo (y de todas las
profundidades). Con eso se sacan series diarias (temperatura media y mínima,
humedad relativa, luz) y medias del periodo para el suelo (pH, conductividad,
humedad volumétrica).
"""
import re
from collections import defaultdict
from datetime import timedelta

from config import INFLUX_ORG, bucket_campo, influxdb_client

FIELDS = {
    "atmosfera": ["temp_aire", "hr", "luz_lux"],
    "suelo": ["humedad_vol", "ph", "conductividad"],
}
# Un día cuenta como medido si tiene al menos estas horas con dato.
MIN_HOURS_PER_DAY = 20
# lux de luz solar -> PPFD (µmol m⁻² s⁻¹): 1 klx ≈ 18,5 µmol (sol directo).
LUX_TO_PPFD = 0.0185

_START_RE = re.compile(r"^-\d+[smhd]$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def get_zone_series(cliente_id: str, balizas: list[str], start: str = "-365d") -> dict:
    """
    {"diario": {fecha: {"temp_media", "temp_min", "hr", "dli"}},
     "suelo": {"ph", "conductividad", "humedad_vol"}: media del periodo o None,
     "horas": nº de horas con dato}
    """
    if not _START_RE.match(start):
        raise ValueError("start debe ser un rango relativo como -90d o -365d")
    if not balizas or not all(_ID_RE.match(b) for b in balizas):
        raise ValueError("balizas no válidas")

    devices = " or ".join(f'r["dispositivo"] == "{b}"' for b in balizas)
    fields = " or ".join(
        f'(r["_measurement"] == "{m}" and r["_field"] == "{f}")' for m, fs in FIELDS.items() for f in fs
    )
    # Todas las series de un campo juntas (balizas y profundidades) y media por hora:
    # la media del grupo. Agrupar por hora en vez de por campo es mucho más lento.
    query = f'''
        from(bucket: "{bucket_campo(cliente_id)}")
          |> range(start: {start})
          |> filter(fn: (r) => ({fields}) and ({devices}))
          |> group(columns: ["_field"])
          |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
    '''
    tables = influxdb_client.query_api().query(query=query, org=INFLUX_ORG)

    hourly = defaultdict(dict)  # campo -> {hora: valor}
    for table in tables:
        for record in table.records:
            value = record.get_value()
            if value is None:
                continue
            # aggregateWindow fecha cada hora con su final: la hora empieza 1 h antes.
            hourly[record.get_field()][record.get_time() - timedelta(hours=1)] = float(value)

    def per_day(field):
        days = defaultdict(list)
        for t, v in hourly.get(field, {}).items():
            days[t.date()].append(v)
        return {d: vs for d, vs in days.items() if len(vs) >= MIN_HOURS_PER_DAY}

    daily = defaultdict(dict)
    for d, vs in per_day("temp_aire").items():
        daily[d]["temp_media"] = sum(vs) / len(vs)
        daily[d]["temp_min"] = min(vs)
    for d, vs in per_day("hr").items():
        daily[d]["hr"] = sum(vs) / len(vs)
    for d, vs in per_day("luz_lux").items():
        # DLI (mol m⁻² d⁻¹) = PPFD medio × 86400 s / 10⁶
        daily[d]["dli"] = sum(vs) / len(vs) * LUX_TO_PPFD * 86400 / 1e6

    def mean(field):
        values = list(hourly.get(field, {}).values())
        return sum(values) / len(values) if values else None

    return {
        "diario": dict(daily),
        "suelo": {f: mean(f) for f in FIELDS["suelo"]},
        "horas": max((len(v) for v in hourly.values()), default=0),
    }
