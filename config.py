# config.py
import os
import influxdb_client
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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

POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "your_password")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "your_db_name")

DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

# Create SQLAlchemy engine and session maker
pg_engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)
