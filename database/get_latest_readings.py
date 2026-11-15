import re
from typing import Optional

import logging

from influxdb_client.rest import ApiException

from config import (
    influxdb_client,
    icinga_influx_client,
    INFLUX_BUCKET,
    INFLUX_BUCKET_ESTADO,
    INFLUX_BUCKET_ICINGA,
    INFLUX_ORG,
)

logger = logging.getLogger(__name__)
_icinga_warned = False

# El host check de las balizas lo escribe Icinga2 con measurement = su
# check_command, que ha cambiado (fieldshift-baliza → fieldshift-baliza-vida).
# Para no depender del nombre se toma el host check más reciente de cada host
# (puntos sin tag `service`). state: 0 = UP, 1 = DOWN.
# Solo cuentan resultados recientes: los hosts que ya no existen en Icinga
# (p. ej. los antiguos b01..b03) dejan de escribir y no deben salir como DOWN.
ICINGA_MAX_AGE = "-15m"

# Solo se aceptan rangos relativos simples (-30m, -24h, -7d) y nombres de
# dispositivo alfanuméricos, porque se interpolan en la consulta Flux.
_START_RE = re.compile(r"^-\d+[smhd]$")
_DISPOSITIVO_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _query_last(bucket: str, measurements: list[str], start: str, dispositivo: Optional[str]):
    measurement_filter = " or ".join(f'r["_measurement"] == "{m}"' for m in measurements)
    device_filter = f' and r["dispositivo"] == "{dispositivo}"' if dispositivo else ""
    query = f'''
        from(bucket: "{bucket}")
          |> range(start: {start})
          |> filter(fn: (r) => ({measurement_filter}){device_filter})
          |> last()
    '''
    return influxdb_client.query_api().query(query=query, org=INFLUX_ORG)


def _query_icinga(dispositivo: Optional[str]):
    """Último estado de host de cada baliza según Icinga2, o None si no hay acceso."""
    client = icinga_influx_client or influxdb_client
    device_filter = f' and r["hostname"] == "{dispositivo}"' if dispositivo else ""
    query = f'''
        from(bucket: "{INFLUX_BUCKET_ICINGA}")
          |> range(start: {ICINGA_MAX_AGE})
          |> filter(fn: (r) => not exists r["service"]
              and (r["_field"] == "state" or r["_field"] == "reachable"){device_filter})
          |> last()
    '''
    try:
        return client.query_api().query(query=query, org=INFLUX_ORG)
    except ApiException as e:
        global _icinga_warned
        if not _icinga_warned:  # una vez, no en cada consulta del WebSocket
            _icinga_warned = True
            logger.warning(
                "Sin acceso al bucket '%s' de InfluxDB (HTTP %s): el panel no mostrará "
                "UP/DOWN. Revisa INFLUX_ICINGA_TOKEN en config.py (token de solo lectura de ese bucket)",
                INFLUX_BUCKET_ICINGA, e.status,
            )
        return None


def get_latest_readings(start: str = "-24h", dispositivo: Optional[str] = None):
    """
    Última lectura de cada baliza: suelo y atmósfera (bucket de campo) y
    estado de la placa (bucket de estado). Devuelve una lista por dispositivo.
    """
    if not _START_RE.match(start):
        raise ValueError("start debe ser un rango relativo como -30m, -24h o -7d")
    if dispositivo and not _DISPOSITIVO_RE.match(dispositivo):
        raise ValueError("dispositivo no válido")

    devices: dict[str, dict] = {}

    def device(name: str) -> dict:
        return devices.setdefault(name, {
            "dispositivo": name,
            "tipo": None,
            "ultima_lectura": None,
            "ultimo_estado": None,
            "simulado": False,
            # {"estado": "UP"|"DOWN", "alcanzable": bool, "comprobado": time} o None si Icinga no la vigila
            "icinga": None,
            "atmosfera": {},
            "suelo": {},
            "estado": {},
        })

    # Puede haber varias series por campo (p. ej. con y sin tag `simulado`):
    # nos quedamos con la más reciente.
    times: dict[tuple, object] = {}

    def keep(key: tuple, target: dict, field: str, value, time) -> None:
        if key not in times or time > times[key]:
            times[key] = time
            target[field] = value

    for table in _query_last(INFLUX_BUCKET, ["suelo", "atmosfera"], start, dispositivo):
        for r in table.records:
            d = device(r.values.get("dispositivo"))
            t = r.get_time()
            if d["ultima_lectura"] is None or t > d["ultima_lectura"]:
                d["ultima_lectura"] = t

            if r.get_measurement() == "suelo":
                prof = r.values.get("profundidad_cm")
                sonda = d["suelo"].setdefault(prof, {
                    "profundidad_cm": int(prof) if prof and prof.isdigit() else prof,
                    "tipo": r.values.get("tipo"),
                })
                keep((d["dispositivo"], "suelo", prof, r.get_field()), sonda, r.get_field(), r.get_value(), t)
                if r.values.get("tipo") == "simulado":
                    d["simulado"] = True
            else:
                keep((d["dispositivo"], "atmosfera", r.get_field()), d["atmosfera"], r.get_field(), r.get_value(), t)
                if r.values.get("simulado") == "true":
                    d["simulado"] = True

    for table in _query_last(INFLUX_BUCKET_ESTADO, ["dispositivo"], start, dispositivo):
        for r in table.records:
            d = device(r.values.get("dispositivo"))
            t = r.get_time()
            d["tipo"] = r.values.get("tipo") or d["tipo"]
            if d["ultimo_estado"] is None or t > d["ultimo_estado"]:
                d["ultimo_estado"] = t
            keep((d["dispositivo"], "estado", r.get_field()), d["estado"], r.get_field(), r.get_value(), t)

    icinga_tables = _query_icinga(dispositivo)
    # Por host: el measurement (check) con el resultado de `state` más reciente.
    latest: dict[str, tuple] = {}
    records = [r for table in icinga_tables or [] for r in table.records]
    for r in records:
        if r.get_field() != "state":
            continue
        host = r.values.get("hostname")
        if host not in latest or r.get_time() > latest[host][0]:
            latest[host] = (r.get_time(), r.get_measurement(), r.get_value())

    for host, (time, measurement, state) in latest.items():
        d = devices.get(host)
        if d is None:  # hosts de Icinga que no son balizas (p. ej. la Pi)
            continue
        reachable = next(
            (r.get_value() for r in records
             if r.values.get("hostname") == host
             and r.get_measurement() == measurement
             and r.get_field() == "reachable"),
            None,
        )
        d["icinga"] = {
            "estado": "UP" if state == 0 else "DOWN",
            "alcanzable": None if reachable is None else bool(reachable),
            "comprobado": time,
        }

    result = []
    for d in sorted(devices.values(), key=lambda x: x["dispositivo"]):
        d["suelo"] = sorted(d["suelo"].values(), key=lambda s: str(s["profundidad_cm"]))
        result.append(d)
    return result, icinga_tables is not None
