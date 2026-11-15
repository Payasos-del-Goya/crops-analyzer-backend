import re
from typing import Optional

from config import influxdb_client, INFLUX_BUCKET, INFLUX_BUCKET_ESTADO, INFLUX_ORG

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

    result = []
    for d in sorted(devices.values(), key=lambda x: x["dispositivo"]):
        d["suelo"] = sorted(d["suelo"].values(), key=lambda s: str(s["profundidad_cm"]))
        result.append(d)
    return result
