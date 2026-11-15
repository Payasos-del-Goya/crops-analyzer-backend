from config import influxdb_client, INFLUX_BUCKET, INFLUX_ORG

def get_baliza_network(start_date: str = "-24h", end_date: str = "now()", period: str = "1h", dispositivo: str = None):
    """
    Constructs and executes a Flux query for InfluxDB using global connection configs.
    """
    # Filtro opcional por dispositivo si se pasa como parámetro (ej: "b02")
    device_filter = f' and r["dispositivo"] == "{dispositivo}"' if dispositivo else ""

    query = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: {start_date}, stop: {end_date})
          |> filter(fn: (r) => r["_measurement"] == "suelo"{device_filter})
          |> aggregateWindow(every: {period}, fn: mean, createEmpty: false)
          |> yield(name: "mean")
    '''

    query_api = influxdb_client.query_api()
    result = query_api.query(query=query, org=INFLUX_ORG)

    return result
