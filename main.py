# main.py
from fastapi import FastAPI
from config import client, INFLUX_BUCKET, INFLUX_ORG, SessionLocal
from database.get_baliza_network import get_baliza_network
from sqlalchemy.orm import Session
from sqlalchemy import text

app = FastAPI()

# Dependency to get PostgreSQL session safely per request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/")
def read_root():
    return {"message": "Hello from API"}

@app.get("/baliza")
def get_baliza_data(start: str = "-24h", period: str = "1h"):
    data = get_baliza_network(start_date=start, period=period)
    return {"status": "success", "data_points_retrieved": len(data)}

@app.get("/pg-check")
def check_postgres(db: Session = Depends(get_db)):
    """Example route using PostgreSQL session"""
    result = db.execute(text("SELECT version();")).fetchone()
    return {"source": "postgresql", "db_version": result[0]}
