from sqlalchemy import text
from config import SessionLocal

def retrieve_balizas_basic_info():
    db = SessionLocal()
    try:
        query = text("SELECT id, latitud, longitud, cultivo_id FROM balizas;")
        result = db.execute(query).fetchall()

        balizas_list = []
        for row in result:
            balizas_list.append({
                "id": row[0],
                "latitud": row[1],
                "longitud": row[2],
                "cultivo_id": row[3],
            })

        return balizas_list
    finally:
        db.close()

def create_baliza(id: str, latitud: float, longitud: float, cultivo_id: int):
    """
    Inserta una nueva baliza en la tabla balizas de PostgreSQL.
    """
    db = SessionLocal()
    try:
        query = text("""
            INSERT INTO balizas (id, latitud, longitud, cultivo_id)
            VALUES (:id, :latitud, :longitud, :cultivo_id);
        """)

        db.execute(query, {
            "id": id,
            "latitud": latitud,
            "longitud": longitud,
            "cultivo_id": cultivo_id
        })

        db.commit()

        return {"status": "success", "id": id}

    except Exception as e:
        db.rollback()  # Si hay un error (ej. ID duplicado), deshacemos los cambios
        raise e
    finally:
        db.close()
