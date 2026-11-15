"""
Precalentar /recomendacion al arrancar el backend.

La primera consulta de cada finca tarda 12-25 s: un año de Meteomatics, las
tablas de Eurostat y el modelo. En una demo eso se nota, así que al arrancar se
hace en segundo plano para todas las fincas:

- Meteomatics se guarda por punto redondeado a 0,01° y el centro de cada zona
  depende del número de zonas. Se piden todos los puntos de la rejilla de 0,01°
  que cubre las balizas de la finca: el centro de cualquier zona cae en uno de
  ellos. Es una petición por finca (dos llamadas a Meteomatics).
- Después, recomendaciones con 1 a ZONES zonas: cargan Eurostat y el modelo y
  dejan en caché el año de lecturas de InfluxDB de esas zonas (~2 s por zona).

Se repite cada REPEAT_SECONDS mientras el backend esté encendido, para que las
cachés (1 h InfluxDB, 6 h Meteomatics) no caduquen en mitad de una demo. Si algo
falla solo se avisa en el log: la consulta normal lo volverá a intentar.
"""
import logging
import threading
import time

from sqlalchemy import text

from config import SessionLocal
from database.get_zone_series import forget_older_than
from database.meteomatics import ROUND_DEGREES, MeteomaticsError, get_climate_year
from graph.recomendacion import get_recommendation

logger = logging.getLogger(__name__)

STEP = 10 ** -ROUND_DEGREES
ZONES = 4
REPEAT_SECONDS = 45 * 60


def _fincas() -> dict:
    """cliente_id -> (lat mín, lat máx, lon mín, lon máx) de sus balizas."""
    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT cliente_id, min(latitud), max(latitud), min(longitud), max(longitud)
            FROM balizas WHERE latitud IS NOT NULL AND longitud IS NOT NULL
            GROUP BY cliente_id ORDER BY cliente_id;
        """)).fetchall()
        return {r[0]: tuple(float(v) for v in r[1:]) for r in rows}
    finally:
        db.close()


def _grid(lat_min, lat_max, lon_min, lon_max) -> list:
    def steps(lo, hi):
        first, last = round(lo, ROUND_DEGREES), round(hi, ROUND_DEGREES)
        return [round(first + i * STEP, ROUND_DEGREES) for i in range(int(round((last - first) / STEP)) + 1)]

    return [(lat, lon) for lat in steps(lat_min, lat_max) for lon in steps(lon_min, lon_max)]


def warm_up() -> None:
    # Lo de la pasada anterior se pide de nuevo ahora, antes de que caduque (1 h).
    forget_older_than(REPEAT_SECONDS - 5 * 60)
    try:
        fincas = _fincas()
    except Exception as e:  # PostgreSQL caído: no hay nada que precalentar
        logger.warning("Precalentar: no se pueden leer las balizas (%s)", e)
        return
    for cliente_id, bbox in fincas.items():
        start = time.time()
        points = _grid(*bbox)
        try:
            get_climate_year(points)
        except MeteomaticsError as e:
            logger.warning("Precalentar %s: Meteomatics no responde (%s)", cliente_id, e)
        try:
            for n in range(1, ZONES + 1):
                get_recommendation(cliente_id, n=n, top=1)
        except Exception as e:
            logger.warning("Precalentar %s: la recomendación falla (%s)", cliente_id, e)
            continue
        logger.info("Precalentado %s: %d puntos de clima, %.1f s", cliente_id, len(points), time.time() - start)


def _loop() -> None:
    while True:
        warm_up()
        time.sleep(REPEAT_SECONDS)


def warm_up_in_background() -> None:
    threading.Thread(target=_loop, name="precalentar-recomendacion", daemon=True).start()
