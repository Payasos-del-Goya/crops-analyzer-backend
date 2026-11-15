from typing import Optional

from sqlalchemy import text
from config import SessionLocal, DEMO_CLIENTE_ID

def retrieve_balizas_basic_info():
    db = SessionLocal()
    try:
        query = text("SELECT id, latitud, longitud, cultivo_id, label FROM balizas;")
        result = db.execute(query).fetchall()

        balizas_list = []
        for row in result:
            balizas_list.append({
                "id": row[0],
                "latitud": row[1],
                "longitud": row[2],
                "cultivo_id": row[3],
                "label": row[4],
            })

        return balizas_list
    finally:
        db.close()

def create_baliza(id: str, latitud: float, longitud: float, cultivo_id: int, label: Optional[str] = None):
    """
    Inserta una nueva baliza en la tabla balizas de PostgreSQL.
    De momento todas se asignan al cliente de la demo (DEMO_CLIENTE_ID).
    """
    db = SessionLocal()
    try:
        query = text("""
            INSERT INTO balizas (id, cliente_id, latitud, longitud, cultivo_id, label)
            VALUES (:id, :cliente_id, :latitud, :longitud, :cultivo_id, :label);
        """)

        db.execute(query, {
            "id": id,
            "cliente_id": DEMO_CLIENTE_ID,
            "latitud": latitud,
            "longitud": longitud,
            "cultivo_id": cultivo_id,
            "label": label or None,
        })

        db.commit()

        return {"status": "success", "id": id}

    except Exception as e:
        db.rollback()  # Si hay un error (ej. ID duplicado), deshacemos los cambios
        raise e
    finally:
        db.close()
