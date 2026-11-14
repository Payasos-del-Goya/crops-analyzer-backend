from sqlalchemy import text
from config import SessionLocal

def retrieve_balizas_basic_info():
    db = SessionLocal()
    try:
        query = text("SELECT id, latitud, longitud, cultivo_id FROM cultivos;")
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
