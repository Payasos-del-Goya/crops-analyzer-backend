# main.py
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Response, WebSocket, WebSocketDisconnect
from auth import (
    SESSION_COOKIE,
    authenticate,
    cliente_nombre,
    create_token,
    current_session,
    read_token,
    token_from,
)
from config import SESSION_HOURS, SessionLocal
from database.get_baliza_network import get_baliza_network
from database.get_latest_readings import get_latest_readings
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from endpoints.balizas import create_baliza, retrieve_balizas_basic_info
from endpoints.crops import retrieve_crops_basic_info, retrieve_crops_data
from graph.idoneidad import get_suitability
from realtime import MonitorHub

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class LoginRequest(BaseModel):
    usuario: str
    password: str

class BalizaCreate(BaseModel):
    id: str
    latitud: float
    longitud: float
    cultivo_id: int
    label: Optional[str] = None

@app.get("/")
def read_root():
    return {"message": "Hello from API"}

@app.post("/login")
def login(body: LoginRequest, response: Response):
    """Comprueba usuario y contraseña y deja la sesión en la cookie crops_session."""
    user = authenticate(body.usuario, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    token = create_token(user["usuario"], user["cliente_id"])
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_HOURS * 3600,
        httponly=True, samesite="lax", path="/",
    )
    return {"status": "success", **user, "token": token}


@app.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "success"}


@app.get("/me")
def me(session: dict = Depends(current_session)):
    return {**session, "nombre": cliente_nombre(session["cliente_id"])}


@app.get("/baliza")
def get_baliza_data(start: str = "-30m", period: str = "5m", dispositivo: str = None,
                    session: dict = Depends(current_session)):
    # 1. Obtenemos las tablas de InfluxDB
    tables = get_baliza_network(session["cliente_id"], start_date=start, period=period, dispositivo=dispositivo)

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


def monitor_snapshot(cliente_id: str, start: str = "-24h", dispositivo: Optional[str] = None) -> dict:
    dispositivos, icinga_ok = get_latest_readings(cliente_id, start=start, dispositivo=dispositivo)
    return {
        "status": "success",
        "count": len(dispositivos),
        # False si el backend no puede leer el estado de Icinga (falta token).
        "icinga_disponible": icinga_ok,
        "dispositivos": dispositivos,
    }


@app.get("/monitor")
def get_monitor(start: str = "-24h", dispositivo: Optional[str] = None,
                session: dict = Depends(current_session)):
    """Última lectura (suelo, atmósfera y estado) de cada baliza del cliente en InfluxDB."""
    try:
        return monitor_snapshot(session["cliente_id"], start=start, dispositivo=dispositivo)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# Un hub por cliente: cada uno consulta solo sus buckets y lo envía a sus navegadores.
monitor_hubs: dict[str, MonitorHub] = {}


def monitor_hub(cliente_id: str) -> MonitorHub:
    if cliente_id not in monitor_hubs:
        monitor_hubs[cliente_id] = MonitorHub(lambda: monitor_snapshot(cliente_id))
    return monitor_hubs[cliente_id]


@app.websocket("/ws/monitor")
async def ws_monitor(ws: WebSocket):
    """Lo mismo que GET /monitor, pero empujado al cliente cada vez que cambia."""
    session = read_token(token_from(ws.headers, ws.cookies) or ws.query_params.get("token"))
    if session is None:
        await ws.close(code=4401)  # sin sesión: se rechaza el handshake
        return
    hub = monitor_hub(session["cliente_id"])
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()  # solo para detectar la desconexión
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)


@app.get("/idoneidad")
def get_idoneidad(n: int = 1, balizas: Optional[str] = None, start: str = "-365d",
                  start_zonas: str = "-24h", cultivos: Optional[str] = None, top: int = 10,
                  regadio: bool = False, orden: str = "idoneidad",
                  min_porcentaje: Optional[float] = None, pais: Optional[str] = None,
                  session: dict = Depends(current_session)):
    """
    % de acercamiento de un conjunto de balizas a las condiciones ideales de cada
    cultivo, con un sistema de reglas (graph/idoneidad.py).

    - balizas: ids separados por comas (por defecto, todas las del cliente).
    - n: zonas en que se parten con SKATER sobre el grafo de balizas (1 = un grupo).
    - start: histórico de InfluxDB que se usa; lo que falte del último año sale de Meteomatics.
    - start_zonas: periodo con el que SKATER compara las balizas para hacer las zonas.
    - cultivos: ids de cultivo separados por comas (por defecto, todos).
    - top: cultivos por zona en la respuesta (0 = todos).
    - regadio: si es True la lluvia anual no cuenta.
    - orden: "idoneidad" (por defecto) o "ingreso": por ingreso esperado en €/ha
      (precio × rendimiento medios del país, de Eurostat, × % de idoneidad).
    - min_porcentaje: deja fuera los cultivos que encajan menos (50 por defecto con orden=ingreso).
    - pais: código de Eurostat para precios y rendimientos (por defecto, el del cliente).
    """
    try:
        baliza_ids = [b.strip() for b in balizas.split(",") if b.strip()] if balizas else None
        cultivo_ids = [int(c) for c in cultivos.split(",") if c.strip()] if cultivos else None
        if n < 1:
            raise ValueError("n debe ser al menos 1")
        data = get_suitability(session["cliente_id"], n=n, balizas=baliza_ids, start=start,
                               start_zonas=start_zonas, cultivos=cultivo_ids, top=top, regadio=regadio,
                               orden=orden, min_porcentaje=min_porcentaje, pais=pais)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"status": "success", **data}


@app.get("/crops")
def get_crops_data(session: dict = Depends(current_session)):
    crops_list = retrieve_crops_data()

    return {
        "status": "success",
        "count": len(crops_list),
        "crops": crops_list
    }

@app.get("/crops_info")
def get_crops_info(session: dict = Depends(current_session)):
    crops_list = retrieve_crops_basic_info()

    return {
        "status": "success",
        "count": len(crops_list),
        "crops": crops_list
    }

@app.get("/balizas")
def get_balizas_info(session: dict = Depends(current_session)):
    balizas_list = retrieve_balizas_basic_info(session["cliente_id"])

    return {
        "status": "success",
        "count": len(balizas_list),
        "balizas": balizas_list,
        "crops": balizas_list,  # clave antigua, por compatibilidad
    }

@app.post("/balizas")
def add_baliza(baliza: BalizaCreate, session: dict = Depends(current_session)):
    try:
        create_baliza(
            cliente_id=session["cliente_id"],
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
