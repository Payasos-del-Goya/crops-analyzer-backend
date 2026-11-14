from config import client, INFLUX_BUCKET, INFLUX_ORG


def get_baliza_network(start_date: str = "-24h", end_date: str = "now()", period: str = "1h"):
    """
    Constructs and executes a Flux query for InfluxDB using global connection configs.
    """
    query = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: {start_date}, stop: {end_date})
          |> filter(fn: (r) => r["_measurement"] == "baliza_network")
          |> aggregateWindow(every: {period}, fn: mean, createEmpty: false)
          |> yield(name: "mean")
    '''

    query_api = client.query_api()
    result = query_api.query(query=query, org=INFLUX_ORG)

    return result
