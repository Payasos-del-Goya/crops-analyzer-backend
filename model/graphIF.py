from endpoints.balizas import retrieve_balizas_basic_info
from database.get_baliza_network import get_baliza_network
from model.graphManager import GraphManager

def get_balizas_data(start: str = "-1w", end: str = "now()", period: str = "1h"):
    """
    Retrieves basic information about balizas and their corresponding network data.
    """
    # Retrieve basic information about balizas
    balizas_info = retrieve_balizas_basic_info()

    # For each baliza, retrieve its network data
    baliza_output = {}
    for baliza in balizas_info:
        dispositivo = baliza['id']
        tables = get_baliza_network(start, end, period, dispositivo=dispositivo)
        for table in tables:
            values = []
            fields = []
            for record in table.records:
                values.append(record.get_value())
                fields.append(record.get_field())
            measurements = {}
            for field, value in zip(fields, values):
                if field not in measurements:
                    measurements[field] = []
                measurements[field].append(value)
            mean_values = {field: sum(vals) / len(vals) for field, vals in measurements.items()}

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

def get_predictions(n):

    #Step 1: Retrieve baliza data
    baliza_data = get_balizas_data()
    baliza_index, coordinates, parameter_list, parameter_keys = order_data(baliza_data)

    #Step 2: Normalize coordinates and create graph
    normCoords = GraphManager.normalize_coordinates(coordinates)
    graph = GraphManager.create_graph(normCoords, k=2)

    #Step 3: Perform clustering and calculate zone means
    labels = GraphManager.skate_clusting(graph, parameter_list, parameter_keys, n)
    zone_means = GraphManager.zone_mean(labels, parameter_list)

    #acceder al modelo

    baliza_zones = {}
    for baliza_id, label in zip(baliza_index, labels):
        baliza_zones[baliza_id] = label

    return baliza_zones


