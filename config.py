# config.py
import os
import influxdb_client
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dotenv import load_dotenv

# Variables de un fichero .env junto al código (ver .env.example).
# Las del entorno tienen prioridad sobre las del fichero.
load_dotenv()

INFLUX_URL = os.getenv("INFLUX_URL", "http://10.42.0.1:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "rY1fmYQWDkTofiYVpqCu21jkgAgTz2JfoZcwnNCfe824Uz1vWqr9z2P-fjBRsRNo9pXtey6XesRCH3SrIOIasw==")
INFLUX_ORG = os.getenv("INFLUX_ORG", "fieldshift")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "c001_campo")
INFLUX_BUCKET_ESTADO = os.getenv("INFLUX_BUCKET_ESTADO", "c001_estado")
# Resultados de los checks de Icinga2 (UP/DOWN de cada baliza). Si el token
# principal no puede leer este bucket, se puede dar uno aparte.
INFLUX_BUCKET_ICINGA = os.getenv("INFLUX_BUCKET_ICINGA", "icinga")
# Token de solo lectura del bucket `icinga` (creado para este backend).
INFLUX_ICINGA_TOKEN = os.getenv("INFLUX_ICINGA_TOKEN", "KUllDYecIlYYmJEGrp-pUFdqCff1cvFmfepETBYrizBPQn3IhdUfQeWrSj4vvfVs8GAGJLLGCywr5FdgL79fMg==")

# Cliente al que se asignan las balizas creadas por POST /balizas (demo).
DEMO_CLIENTE_ID = os.getenv("DEMO_CLIENTE_ID", "c001")

# Cliente para el bucket de Icinga (el principal si no hay token propio).
icinga_influx_client = (
    influxdb_client.InfluxDBClient(url=INFLUX_URL, token=INFLUX_ICINGA_TOKEN, org=INFLUX_ORG)
    if INFLUX_ICINGA_TOKEN
    else None
)

# Initialize a single global client instance to be imported elsewhere
influxdb_client = influxdb_client.InfluxDBClient(
    url=INFLUX_URL,
    token=INFLUX_TOKEN,
    org=INFLUX_ORG
)

POSTGRES_USER = os.getenv("POSTGRES_USER", "fieldshift_app")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "xCsS8yLRVs8fKLyUnFrnaFJw")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "fieldshift-gw.local")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "fieldshift")

DATABASE_URL = f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
# Create SQLAlchemy engine and session maker
pg_engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)
