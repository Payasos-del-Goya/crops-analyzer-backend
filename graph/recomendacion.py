"""
Recomendación de cultivos por zonas con el modelo entrenado (modelo_europa.py).

Las zonas y sus condiciones salen igual que en la idoneidad (graph/idoneidad.py):
balizas partidas con SKATER, último año de las balizas (InfluxDB) completado con
Meteomatics. Con eso se calculan las variables de clima del modelo, que da para
cada cultivo su cuota (qué parte de la tierra le dedican las regiones con ese
clima) y su rendimiento.

El orden no es el ingreso por hectárea: sin costes no se pueden comparar
cultivos (una patata factura diez veces más que un cereal y saldría siempre
primera, aunque cueste mucho más producirla y no se pueda repetir cada año).
Cada cultivo se compara consigo mismo:

    ventaja    = (rendimiento aquí / rendimiento habitual en Europa)
                 × (precio en el país / precio habitual en Europa)
    puntuación = cuota (%) × ventaja

La cuota recoge lo que de verdad se siembra con ese clima (los agricultores ya
cuentan con costes, rotación y mercado) y la ventaja sube los cultivos que aquí
rinden o se pagan mejor de lo normal. El ingreso bruto por hectárea se da como
dato, no como criterio.
"""
import logging
from collections import Counter
from typing import Optional

from database.eurostat import EurostatError
from graph.idoneidad import country_of, zone_conditions
from modelo_predicciones import modelo_europa
from modelo_predicciones.rentabilidad import crop_price

logger = logging.getLogger(__name__)

# orden -> campo por el que se ordena
ORDER_KEYS = {"puntuacion": "puntuacion", "cuota": "cuota_pct", "ingreso": "ingreso_bruto_eur_ha"}


def get_recommendation(cliente_id: str, n: int = 1, balizas: Optional[list] = None, start: str = "-365d",
                       start_zonas: str = "-24h", top: Optional[int] = 10, orden: str = "puntuacion",
                       pais: Optional[str] = None) -> dict:
    """
    orden: "puntuacion" (cuota × ventaja, por defecto), "cuota" (lo que más se
    siembra con ese clima, sin mirar precios) o "ingreso" (ingreso bruto en
    €/ha: solo como referencia, favorece siempre a los cultivos caros de producir).

    Returns {"modelo", "meteomatics_disponible", "eurostat_disponible", "pais", "orden",
    "zonas": [{zona, balizas, centro, variables, fuera_de_rango, cultivos_actuales, cultivos}]}.
    """
    if orden not in ORDER_KEYS:
        raise ValueError("orden debe ser puntuacion, cuota o ingreso")
    country = country_of(cliente_id, pais)
    zones, registered, meteomatics_ok = zone_conditions(cliente_id, n, balizas, start, start_zonas, regadio=False)
    meta = modelo_europa.model()["meta"]
    eurostat = {"ok": True}

    def price(crop_id):
        if not eurostat["ok"]:
            return None
        try:
            return crop_price(crop_id, country)
        except EurostatError as e:
            logger.warning("Sin datos de Eurostat: %s", e)
            eurostat["ok"] = False
            return None

    result = []
    for label, zone in enumerate(zones):
        conditions = zone["conditions"]
        variables = modelo_europa.climate_variables(
            conditions["dias"], conditions["temp_media"], conditions["hr"],
            conditions["precip_mm"], conditions["radiacion_mj"])
        crops = []
        if variables is not None:
            for prediction in modelo_europa.predict(variables):
                market = price(prediction["id"])
                gross = None if market is None else prediction["rendimiento_t_ha"] * market["valor"]
                # Sin precio (o sin referencia europea) la ventaja es solo la del rendimiento.
                relative_price = market["valor"] / market["europeo"] if market and market["europeo"] else 1.0
                advantage = prediction["rendimiento_relativo"] * relative_price
                crops.append({
                    "id": prediction["id"],
                    "cultivo": prediction["cultivo"],
                    "puntuacion": round(100 * prediction["cuota"] * advantage, 1),
                    "cuota_pct": round(100 * prediction["cuota"], 1),
                    "ventaja": round(advantage, 2),
                    "rendimiento_t_ha": round(prediction["rendimiento_t_ha"], 2),
                    "rendimiento_relativo": round(prediction["rendimiento_relativo"], 2),
                    "precio_eur_t": None if market is None else round(market["valor"]),
                    "precio_relativo": None if market is None or not market["europeo"] else round(relative_price, 2),
                    "ingreso_bruto_eur_ha": None if gross is None else round(gross),
                    "fuente_precio": None if market is None else market["fuente"],
                })
            # sort es estable: a igualdad (o sin precio con orden=ingreso) manda la puntuación.
            crops.sort(key=lambda c: c["puntuacion"], reverse=True)
            if orden != "puntuacion":
                key = ORDER_KEYS[orden]
                crops.sort(key=lambda c: -1 if c[key] is None else c[key], reverse=True)

        by_id = {c["id"]: c for c in crops}
        planted = Counter(registered[b]["cultivo_id"] for b in zone["balizas"] if registered[b]["cultivo_id"])
        current = [{**by_id[crop_id], "balizas": count} if crop_id in by_id
                   else {"id": crop_id, "balizas": count, "fuera_del_modelo": True}
                   for crop_id, count in planted.most_common()]

        result.append({
            "zona": label,
            "balizas": zone["balizas"],
            "centro": zone["centro"],
            "variables": None if variables is None else {k: round(v, 1) for k, v in variables.items()},
            # Variables fuera de lo visto al entrenar: ahí el modelo extrapola.
            "fuera_de_rango": [] if variables is None else modelo_europa.out_of_range(variables),
            "cultivos_actuales": current,
            "cultivos": crops[:top] if top else crops,
        })

    return {
        "modelo": {"datos": meta["datos"], "cultivos": len(meta["codigos"]), "sklearn": meta["sklearn"],
                   "entrenado_al_arrancar": bool(meta.get("entrenado_al_arrancar"))},
        "meteomatics_disponible": meteomatics_ok,
        "eurostat_disponible": eurostat["ok"],
        "pais": country,
        "orden": orden,
        "zonas": result,
    }
