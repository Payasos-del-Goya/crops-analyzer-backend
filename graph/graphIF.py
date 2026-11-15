from endpoints.balizas import retrieve_balizas_basic_info
from database.get_baliza_network import get_baliza_network
from graph.graphManager import GraphManager
from modelo_predicciones.model_IF import recomendacion_avanzada

def get_balizas_data(start: str = "-10m", end: str = "now()", period: str = "1h", client_id: str = "c001"):
    """
    Retrieves basic information about balizas and their corresponding network data.
    """
    # Retrieve basic information about balizas
    balizas_info = retrieve_balizas_basic_info(client_id)

    # For each baliza, retrieve its network data
    baliza_output = {}
    for baliza in balizas_info:
        dispositivo = baliza['id']
        tables = get_baliza_network(client_id, start, end, period, dispositivo=dispositivo)
        for table in tables:
            values = []
            fields = []
            times = []
            for record in table.records:
                values.append(record.get_value())
                fields.append(record.get_field())
                times.append(record.get_time())

            measurements = {}
            for field, value in zip(fields, values):
                if field not in measurements:
                    measurements[field] = []
                measurements[field].append(value)

            mean_values = {}
            for field, vals in measurements.items():
                if field == "luz_lux":
                    light_hours = sum(1 for value in vals if value > 400) #TODO : only valid if period == 1h
                    mean_values["light_hours"] = light_hours
                else:
                    mean_values[field] = sum(vals) / len(vals)

        baliza_output[dispositivo] = {
            "coordinates": (baliza['latitud'], baliza['longitud']),
            "parameters": mean_values
        }

    return baliza_output

def order_data(data):
    """
    Prepare the data structure expected by GraphManager.

    Returns a tuple with:
        - baliza_index: dict mapping baliza id -> index in [0, n)
        - coordinates: list of (x, y) tuples in the same order as the indices
        - parameter_list: list of parameter tuples in the same order as the indices
        - parameter_keys: ordered keys from the mean_values dictionary
    """
    ordered_balizas = sorted(data.items(), key=lambda item: item[0])

    baliza_index = {}
    coordinates = []
    parameter_list = []
    parameter_keys = []

    for index, (baliza_id, baliza_data) in enumerate(ordered_balizas):
        baliza_index[baliza_id] = index
        coordinates.append(tuple(baliza_data["coordinates"]))

        params = baliza_data["parameters"]
        if not parameter_keys:
            parameter_keys = list(params.keys())

        parameter_list.append(tuple(params[key] for key in parameter_keys))

    return baliza_index, coordinates, parameter_list, parameter_keys

def extract_zone_parameters(zone_means, parameter_keys, label=0):
    """
    Extract the values of the main agronomic parameters from the zone means list,
    following the order defined by parameter_keys.
    """
    ordered_vars = zone_means.get(label, [])
    if not ordered_vars:
        return {
            "temp_aire": None,
            "hr": None,
            "humedad_vol": None,
            "light_hours": None,
            "ph": None,
        }

    index_by_key = {key: index for index, key in enumerate(parameter_keys)}
    extracted = {}
    for name in ["temp_aire", "hr", "humedad_vol", "light_hours", "ph"]:
        if name in index_by_key:
            value_index = index_by_key[name]
            extracted[name] = ordered_vars[value_index] if value_index < len(ordered_vars) else None
        else:
            extracted[name] = None

    return extracted


def get_predictions(n, client_id):

    #Step 1: Retrieve baliza data
    baliza_data = get_balizas_data(client_id=client_id)
    baliza_index, coordinates, parameter_list, parameter_keys = order_data(baliza_data)

    #Step 2: Normalize coordinates and create graph
    normCoords = GraphManager.normalize_coordinates(coordinates)
    graph = GraphManager.create_graph(normCoords, k=4)

    #Step 3: Perform clustering and calculate zone means
    labels = GraphManager.skate_clusting(graph, normCoords, parameter_list, parameter_keys, n)
    zone_means = GraphManager.zone_mean(labels, parameter_list)

    zone_recomendation = {}
    for label in range(len(zone_means)):
        zone_parameters = extract_zone_parameters(zone_means, parameter_keys, label)
        temp_aire = zone_parameters["temp_aire"]
        hr = zone_parameters["hr"]
        humedad_vol = zone_parameters["humedad_vol"]
        light_hours = zone_parameters["light_hours"]
        ph = zone_parameters["ph"]
        zone_recomendation[label] = recomendacion_avanzada(temp_aire, hr, humedad_vol, light_hours, ph)


    baliza_zones = {}
    for baliza_id, label in zip(baliza_index, labels):
        baliza_zones[baliza_id] = label

    return baliza_zones, zone_recomendation


