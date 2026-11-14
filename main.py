# main.py
from fastapi import FastAPI
from config import client, INFLUX_BUCKET, INFLUX_ORG
from database.get_baliza_network import get_baliza_network

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Hello from API"}

@app.get("/baliza")
def get_baliza_data(start: str = "-24h", period: str = "1h"):
    data = get_baliza_network(start_date=start, period=period)
    return {"status": "success", "data_points_retrieved": len(data)}
