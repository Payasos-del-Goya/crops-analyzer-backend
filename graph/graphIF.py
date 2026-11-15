import math
import re
from collections import defaultdict

from config import influxdb_client, bucket_campo, INFLUX_ORG
from endpoints.balizas import retrieve_balizas_basic_info
from graph.graphManager import GraphManager
from modelo_predicciones.model_IF import recomendacion_avanzada

# Parámetros que usa el modelo de rendimiento, en este orden. Son también los
# que comparan SKATER para decidir qué balizas se parecen.
PARAMETER_KEYS = ["temp_aire", "hr", "humedad_vol", "light_hours", "ph"]
# Campos de InfluxDB de donde salen (light_hours se calcula a partir de luz_lux).
INFLUX_FIELDS = {"temp_aire", "hr", "humedad_vol", "ph", "luz_lux"}
LIGHT_LUX_THRESHOLD = 400  # una hora cuenta como "de luz" si su media supera esto

# Se interpolan en Flux: solo rangos relativos simples.
_START_RE = re.compile(r"^-\d+[smhd]$")


def get_balizas_data(cliente_id: str, start: str = "-24h"):
    """
    Coordenadas y parámetros medios de cada baliza del cliente.

    Una sola consulta a InfluxDB (suelo + atmósfera) con medias horarias:
    - temp_aire, hr, humedad_vol, ph: media de las medias horarias (y de todas
      las profundidades, si hay varias).
    - light_hours: horas con luz al día = fracción de horas con media de
      luz_lux > 400, escalada a 24 h (el modelo espera horas de sol diarias).

    Devuelve {id: {"coordinates": (lat, lon), "parameters": {clave: valor|None}}}
    para todas las balizas registradas, tengan o no datos recientes.
    """
    if not _START_RE.match(start):
        raise ValueError("start debe ser un rango relativo como -6h o -24h")

    fields = " or ".join(f'r["_field"] == "{f}"' for f in sorted(INFLUX_FIELDS))
    query = f'''
        from(bucket: "{bucket_campo(cliente_id)}")
          |> range(start: {start})
          |> filter(fn: (r) => (r["_measurement"] == "suelo" or r["_measurement"] == "atmosfera") and ({fields}))
          |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
    '''
    tables = influxdb_client.query_api().query(query=query, org=INFLUX_ORG)

    hourly = defaultdict(lambda: defaultdict(list))  # dispositivo -> campo -> [medias]
    for table in tables:
        for record in table.records:
            value = record.get_value()
            if value is None:
                continue
            hourly[record.values.get("dispositivo")][record.get_field()].append(float(value))

    data = {}
    for baliza in retrieve_balizas_basic_info(cliente_id):
        measurements = hourly.get(baliza["id"], {})
        parameters = {key: None for key in PARAMETER_KEYS}
        for field, values in measurements.items():
            if not values:
                continue
            if field == "luz_lux":
                lit = sum(1 for v in values if v > LIGHT_LUX_THRESHOLD)
                parameters["light_hours"] = 24.0 * lit / len(values)
            elif field in parameters:
                parameters[field] = sum(values) / len(values)

        data[baliza["id"]] = {
            # PostgreSQL devuelve Decimal: a float para numpy/JSON.
            "coordinates": (float(baliza["latitud"]), float(baliza["longitud"])),
            "parameters": parameters,
        }
    return data


def order_data(data):
    """
    Prepara la estructura que espera GraphManager, ordenada por id de baliza.

    Returns:
        - baliza_ids: lista de ids en el orden de las filas
        - coordinates: lista de (lat, lon)
        - parameter_list: lista de tuplas con PARAMETER_KEYS (NaN si falta)
        - parameter_keys: PARAMETER_KEYS
    """
    baliza_ids = sorted(data)
    coordinates = [tuple(data[b]["coordinates"]) for b in baliza_ids]
    parameter_list = [
        tuple(math.nan if data[b]["parameters"].get(key) is None else data[b]["parameters"][key]
              for key in PARAMETER_KEYS)
        for b in baliza_ids
    ]
    return baliza_ids, coordinates, parameter_list, list(PARAMETER_KEYS)


def get_predictions(cliente_id: str, n: int, start: str = "-24h", k: int = 2):
    """
    Divide las balizas del cliente en n zonas contiguas y parecidas (SKATER) y
    recomienda cultivos para cada zona con el modelo de rendimiento.

    Returns:
        - baliza_zones: {id_baliza: zona}
        - zones: {zona: {"parametros": {clave: media|None}, "balizas": [...],
                         "recomendacion": [(cultivo, t/ha), ...]}}
    """
    if int(n) < 1:
        raise ValueError("n debe ser al menos 1")

    baliza_data = get_balizas_data(cliente_id, start=start)
    if not baliza_data:
        return {}, {}
    baliza_ids, coordinates, parameter_list, parameter_keys = order_data(baliza_data)

    positions = GraphManager.normalize_coordinates(coordinates)
    graph = GraphManager.create_graph(positions, k=k)

    labels = GraphManager.skate_clusting(graph, positions, parameter_list, parameter_keys, n)
    zone_means = GraphManager.zone_mean(labels, parameter_list)

    baliza_zones = dict(zip(baliza_ids, labels))
    zones = {}
    for label, means in zone_means.items():
        params = dict(zip(parameter_keys, means))
        recomendacion = recomendacion_avanzada(
            params["temp_aire"], params["hr"], params["humedad_vol"], params["light_hours"], params["ph"],
        )
        zones[label] = {
            "parametros": params,
            "balizas": [b for b, z in baliza_zones.items() if z == label],
            "recomendacion": [(crop, float(yield_t_ha)) for crop, yield_t_ha in recomendacion],
        }

    return baliza_zones, zones
