# config.py
import os
import influxdb_client
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Optional: Load from a .env file if you use python-dotenv
# from dotenv import load_dotenv
# load_dotenv()

INFLUX_URL = os.getenv("INFLUX_URL", "http://10.42.0.1:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "rY1fmYQWDkTofiYVpqCu21jkgAgTz2JfoZcwnNCfe824Uz1vWqr9z2P-fjBRsRNo9pXtey6XesRCH3SrIOIasw==")
INFLUX_ORG = os.getenv("INFLUX_ORG", "fieldshift")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "c001_campo")

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
