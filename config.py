# config.py
import os
import influxdb_client

# Optional: Load from a .env file if you use python-dotenv
# from dotenv import load_dotenv
# load_dotenv()

INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "YOUR_API_TOKEN_HERE")
INFLUX_ORG = os.getenv("INFLUX_ORG", "YOUR_ORG_NAME")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "YOUR_BUCKET_NAME")

# Initialize a single global client instance to be imported elsewhere
client = influxdb_client.InfluxDBClient(
    url=INFLUX_URL,
    token=INFLUX_TOKEN,
    org=INFLUX_ORG
)
