# config.py
import os
import re
import influxdb_client
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

INFLUX_URL = os.getenv("INFLUX_URL", "http://10.42.0.1:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "Vy-MgkoZUOHyMlMmMU9W74-6p4Z6Q-HcofcpCwve_sUAmtrRPIjL0DHAtNXwbCmowg3hvwnpU3AjoLvzWw7zqg==")
INFLUX_ORG = os.getenv("INFLUX_ORG", "fieldshift")
# Cada cliente tiene sus buckets: <cliente>_campo y <cliente>_estado (c001_campo...).
# El token de arriba es de solo lectura de los de c001 a c004: para un cliente
# nuevo hay que darle lectura de sus buckets.
# Resultados de los checks de Icinga2 (UP/DOWN de cada baliza). Si el token
# principal no puede leer este bucket, se puede dar uno aparte.
INFLUX_BUCKET_ICINGA = os.getenv("INFLUX_BUCKET_ICINGA", "icinga")
# Token de solo lectura del bucket `icinga` (creado para este backend).
INFLUX_ICINGA_TOKEN = os.getenv("INFLUX_ICINGA_TOKEN", "KUllDYecIlYYmJEGrp-pUFdqCff1cvFmfepETBYrizBPQn3IhdUfQeWrSj4vvfVs8GAGJLLGCywr5FdgL79fMg==")

# Firma de las sesiones del panel (cookie crops_session). Demo: fija aquí.
SESSION_SECRET = os.getenv("SESSION_SECRET", "fieldshift-demo-2b7f0c9e5a4d4e1f8c3a6b2d9e7f1a05")
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "168"))

# Cliente para el bucket de Icinga (el principal si no hay token propio).
icinga_influx_client = (
    influxdb_client.InfluxDBClient(url=INFLUX_URL, token=INFLUX_ICINGA_TOKEN, org=INFLUX_ORG)
    if INFLUX_ICINGA_TOKEN
    else None
)

_CLIENTE_RE = re.compile(r"^c[0-9]{3}$")


def _cliente(cliente_id: str) -> str:
    # Se interpola en consultas Flux: solo ids con el formato de la tabla clientes.
    if not _CLIENTE_RE.match(cliente_id or ""):
        raise ValueError(f"cliente no válido: {cliente_id!r}")
    return cliente_id


def bucket_campo(cliente_id: str) -> str:
    return f"{_cliente(cliente_id)}_campo"


def bucket_estado(cliente_id: str) -> str:
    return f"{_cliente(cliente_id)}_estado"


# Initialize a single global client instance to be imported elsewhere
influxdb_client = influxdb_client.InfluxDBClient(
    url=INFLUX_URL,
    token=INFLUX_TOKEN,
    org=INFLUX_ORG
)

# Meteomatics: clima de lo que las balizas no miden (lluvia, viento, radiación,
# horas de frío) y del año completo. Cuenta del hackathon, válida hasta el
# 22-11-2026, límite de 1000 peticiones al día (el backend las cachea).
METEOMATICS_URL = os.getenv("METEOMATICS_URL", "https://api.meteomatics.com")
METEOMATICS_USER = os.getenv("METEOMATICS_USER", "hackathon_nasa_space_apps_bernal_guillermo")
METEOMATICS_PASSWORD = os.getenv("METEOMATICS_PASSWORD", "cf6S79u5GR8SyqT23y5J")

# País de cada cliente (código de Eurostat) para los precios y rendimientos de
# los cultivos. La tabla `clientes` no lo guarda.
CLIENTE_PAIS = {"c001": "ES", "c002": "ES", "c003": "IE", "c004": "NO"}
PAIS_POR_DEFECTO = "ES"

POSTGRES_USER = os.getenv("POSTGRES_USER", "fieldshift_app")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "xCsS8yLRVs8fKLyUnFrnaFJw")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "fieldshift-gw.local")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "fieldshift")

DATABASE_URL = f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
# Create SQLAlchemy engine and session maker
pg_engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)
