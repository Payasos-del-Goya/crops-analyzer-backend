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

# Measurement que escribe Icinga2 para el host check de las balizas
# (check_command del template "fieldshift-baliza"). state: 0 = UP, 1 = DOWN.
ICINGA_MEASUREMENT = "fieldshift-baliza"
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
          |> filter(fn: (r) => r["_measurement"] == "{ICINGA_MEASUREMENT}"
              and not exists r["service"]
              and (r["_field"] == "state" or r["_field"] == "reachable"){device_filter})
          |> last()
    '''
    try:
        return client.query_api().query(query=query, org=INFLUX_ORG)
    except ApiException as e:
        logger.warning("No se puede leer el bucket de Icinga (%s): %s", INFLUX_BUCKET_ICINGA, e.status)
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
    for table in icinga_tables or []:
        for r in table.records:
            d = device(r.values.get("hostname"))
            info = d["icinga"] or {"estado": None, "alcanzable": None, "comprobado": None}
            if r.get_field() == "state":
                info["estado"] = "UP" if r.get_value() == 0 else "DOWN"
                info["comprobado"] = r.get_time()
            else:
                info["alcanzable"] = bool(r.get_value())
            d["icinga"] = info

    result = []
    for d in sorted(devices.values(), key=lambda x: x["dispositivo"]):
        d["suelo"] = sorted(d["suelo"].values(), key=lambda s: str(s["profundidad_cm"]))
        result.append(d)
    return result, icinga_tables is not None
