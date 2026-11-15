# main.py
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException
from config import influxdb_client, INFLUX_BUCKET, INFLUX_ORG, SessionLocal
from database.get_baliza_network import get_baliza_network
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from endpoints.balizas import create_baliza, retrieve_balizas_basic_info
from endpoints.crops import retrieve_crops_basic_info, retrieve_crops_data

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class BalizaCreate(BaseModel):
    id: str
    latitud: float
    longitud: float
    cultivo_id: int
    label: Optional[str] = None

@app.get("/")
def read_root():
    return {"message": "Hello from API"}

@app.get("/baliza")
def get_baliza_data(start: str = "-30m", period: str = "5m", dispositivo: str = None):
    # 1. Obtenemos las tablas de InfluxDB
    tables = get_baliza_network(start_date=start, period=period, dispositivo=dispositivo)

    # 2. Recorremos los registros para estructurarlos en una lista limpia
    data_points = []
    for table in tables:
        for record in table.records:
            data_points.append({
                "time": record.get_time(),
                "measurement": record.get_measurement(),
                "field": record.get_field(),
                "value": record.get_value(),
                "dispositivo": record.values.get("dispositivo"),
                "profundidad_cm": record.values.get("profundidad_cm"),
                "finca": record.values.get("finca"),
                "zona": record.values.get("zona"),
                "tipo": record.values.get("tipo")
            })

    return {
        "status": "success",
        "data_points_retrieved": len(data_points),
        "data": data_points
    }


@app.get("/crops")
def get_crops_data():
    crops_list = retrieve_crops_data()

    return {
        "status": "success",
        "count": len(crops_list),
        "crops": crops_list
    }

@app.get("/crops_info")
def get_crops_info():
    crops_list = retrieve_crops_basic_info()

    return {
        "status": "success",
        "count": len(crops_list),
        "crops": crops_list
    }

@app.get("/balizas")
def get_balizas_info():
    balizas_list = retrieve_balizas_basic_info()

    return {
        "status": "success",
        "count": len(balizas_list),
        "crops": balizas_list
    }

@app.post("/balizas")
def add_baliza(baliza: BalizaCreate):
    try:
        create_baliza(
            id=baliza.id,
            latitud=baliza.latitud,
            longitud=baliza.longitud,
            cultivo_id=baliza.cultivo_id,
            label=baliza.label,
        )
        return {
            "status": "success",
            "message": f"Baliza '{baliza.id}' creada correctamente.",
            "data": baliza
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error al crear la baliza: {str(e)}")
