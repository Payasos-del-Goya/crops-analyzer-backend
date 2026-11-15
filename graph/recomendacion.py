"""
Recomendación de cultivos por zonas con el modelo entrenado (modelo_europa.py).

Las zonas y sus condiciones salen igual que en la idoneidad (graph/idoneidad.py):
balizas partidas con SKATER, último año de las balizas (InfluxDB) completado con
Meteomatics. Con eso se calculan las variables de clima del modelo, que da para
cada cultivo la probabilidad de que se dé ahí y su rendimiento. El precio de
venta (Eurostat) convierte el rendimiento en ingreso por hectárea:

    ingreso_esperado = probabilidad × rendimiento (t/ha) × precio (€/t)

Es ingreso bruto: no descuenta costes.
"""
import logging
from collections import Counter
from typing import Optional

from database.eurostat import EurostatError
from graph.idoneidad import country_of, zone_conditions
from modelo_predicciones import modelo_europa
from modelo_predicciones.rentabilidad import crop_price

logger = logging.getLogger(__name__)


def get_recommendation(cliente_id: str, n: int = 1, balizas: Optional[list] = None, start: str = "-365d",
                       start_zonas: str = "-24h", top: Optional[int] = 10, orden: str = "ingreso",
                       pais: Optional[str] = None) -> dict:
    """
    orden: "ingreso" (ingreso esperado en €/ha; los cultivos sin precio van al
    final) o "probabilidad" (lo que mejor se da, sin mirar el precio).

    Returns {"modelo", "meteomatics_disponible", "eurostat_disponible", "pais", "orden",
    "zonas": [{zona, balizas, centro, variables, fuera_de_rango, cultivos_actuales, cultivos}]}.
    """
    if orden not in ("ingreso", "probabilidad"):
        raise ValueError("orden debe ser ingreso o probabilidad")
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
                crops.append({
                    "id": prediction["id"],
                    "cultivo": prediction["cultivo"],
                    "probabilidad_pct": round(100 * prediction["probabilidad"], 1),
                    "rendimiento_t_ha": round(prediction["rendimiento_t_ha"], 2),
                    "precio_eur_t": None if market is None else round(market["valor"]),
                    "ingreso_bruto_eur_ha": None if gross is None else round(gross),
                    "ingreso_esperado_eur_ha": None if gross is None else round(gross * prediction["probabilidad"]),
                    "fuente_precio": None if market is None else market["fuente"],
                })
            crops.sort(key=lambda c: c["probabilidad_pct"], reverse=True)
            if orden == "ingreso":
                # sort es estable: sin precio (-1) al final, por probabilidad.
                crops.sort(key=lambda c: -1 if c["ingreso_esperado_eur_ha"] is None else c["ingreso_esperado_eur_ha"],
                           reverse=True)

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
