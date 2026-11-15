from typing import Optional

from sqlalchemy import text
from config import SessionLocal


def retrieve_balizas_basic_info(cliente_id: str):
    """Balizas del cliente (cada cliente solo ve las suyas)."""
    db = SessionLocal()
    try:
        query = text("""
            SELECT id, latitud, longitud, cultivo_id, label, zona, textura_suelo, altitud_m
            FROM balizas
            WHERE cliente_id = :cliente_id
            ORDER BY id;
        """)
        result = db.execute(query, {"cliente_id": cliente_id}).fetchall()

        balizas_list = []
        for row in result:
            balizas_list.append({
                "id": row[0],
                "latitud": row[1],
                "longitud": row[2],
                "cultivo_id": row[3],
                "label": row[4],
                "zona": row[5],
                "textura_suelo": row[6],
                "altitud_m": row[7],
            })

        return balizas_list
    finally:
        db.close()

def create_baliza(cliente_id: str, id: str, latitud: float, longitud: float, cultivo_id: int, label: Optional[str] = None):
    """
    Inserta una nueva baliza del cliente en la tabla balizas de PostgreSQL.
    """
    db = SessionLocal()
    try:
        query = text("""
            INSERT INTO balizas (id, cliente_id, latitud, longitud, cultivo_id, label)
            VALUES (:id, :cliente_id, :latitud, :longitud, :cultivo_id, :label);
        """)

        db.execute(query, {
            "id": id,
            "cliente_id": cliente_id,
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
