"""
Ingreso por hectárea de cada cultivo: precio × rendimiento, con datos de Eurostat.

- ingreso_medio_eur_ha: precio de venta medio del país (€/t) × rendimiento
  medio del país (t/ha). Es lo que ingresa de media una hectárea de ese
  cultivo en el país, no en esta finca.
- ingreso_esperado_eur_ha: el anterior × el % de idoneidad de la zona. Es una
  estimación sencilla: supone que el rendimiento baja en proporción a lo que
  la zona se aleja de las condiciones ideales del cultivo.

Es ingreso bruto: no descuenta costes (mano de obra, riego, plantación, años
sin cosecha de un frutal joven), que cambian mucho de un cultivo a otro.

Solo tienen datos los cultivos que Eurostat publica; el resto sale con
`economia: None`.
"""
from typing import Optional

from database.eurostat import get_market, get_price

# id de la tabla `cultivos` -> (códigos de precio apri_ap_crpouta, códigos de cultivo apro_cpsh1).
# Con varios códigos se usa el primero que tenga dato.
EUROSTAT_CODES = {
    1: (["01110000"], ["C1110", "C1100"]),             # Trigo blando de invierno
    2: (["01120000"], ["C1120"]),                      # Trigo duro
    3: (["01300000", "01310000"], ["C1300"]),          # Cebada
    4: (["01400000"], ["C1410", "C1400"]),             # Avena
    5: (["01500000"], ["C1500"]),                      # Maíz grano
    7: (["01910000"], ["C1700"]),                      # Sorgo grano
    8: (["01600000"], ["C2000"]),                      # Arroz
    9: (["02992000"], ["P9200"]),                      # Garbanzo
    10: (["02991000"], ["P9100"]),                     # Lenteja
    11: (["02230000"], ["P1200"]),                     # Haba seca
    12: (["04199000"], ["V5100"]),                     # Guisante fresco
    13: (["04198100", "04197000"], ["V5200"]),         # Judía verde
    15: (["02120000"], ["I1120"]),                     # Girasol
    16: (["02110000"], ["I1110"]),                     # Colza
    19: (["02400000"], ["R2000"]),                     # Remolacha azucarera
    20: (["05120000", "05110000"], ["R1000"]),         # Patata
    22: (["04121000"], ["V3100"]),                     # Tomate (al aire libre)
    25: (["04194100"], ["V3200"]),                     # Pepino (al aire libre)
    26: (["04199911"], ["V3420"]),                     # Calabacín
    27: (["04199913"], ["V3510"]),                     # Melón
    28: (["04199914"], ["V3520"]),                     # Sandía
    29: (["04192100"], ["V2300"]),                     # Lechuga (al aire libre)
    30: (["04196000"], ["V4210", "V4200"]),            # Cebolla
    31: (["04199906"], ["V4600"]),                     # Ajo
    32: (["04195000"], ["V4100"]),                     # Zanahoria
    33: (["04191100"], ["V1300"]),                     # Col / repollo
    34: (["04110000"], ["V1100"]),                     # Coliflor
    35: (["04110000"], ["V1100"]),                     # Brócoli (Eurostat lo da junto a la coliflor)
    36: (["04193000"], ["V2500"]),                     # Espinaca
    38: (["04199910"], ["V2600"]),                     # Espárrago
    39: (["06193100", "06193000"], ["S0000"]),         # Fresa
    40: (["06490000"], ["W1100"]),                     # Vid de vinificación
    41: (["06410000"], ["W1200"]),                     # Uva de mesa
    42: (["06590000"], ["O1910", "O1000"]),            # Olivo (aceituna de almazara)
    43: (["06194130"], ["F4300"]),                     # Almendro
    44: (["06110000"], ["F1110"]),                     # Manzano
    45: (["06120000"], ["F1120"]),                     # Peral
    46: (["06191100"], ["F1242", "F1240"]),            # Cerezo
    47: (["06130000"], ["F1210", "F1210_1220"]),       # Melocotonero
    48: (["06199100"], ["F1230"]),                     # Albaricoquero
    49: (["06192000"], ["F1250"]),                     # Ciruelo
    51: (["06194110"], ["F4100"]),                     # Nogal
    52: (["06210000"], ["T1000"]),                     # Naranjo
    53: (["06230000"], ["T3000"]),                     # Limonero
    54: (["06220000"], ["T2000"]),                     # Mandarino
    58: (["01200000"], ["C1210", "C1200"]),            # Centeno
    59: (["01920000"], ["C1600"]),                     # Triticale
    65: (["02130000"], ["I1130"]),                     # Soja
    68: (["02300000"], ["I3000"]),                     # Tabaco
    71: (["04199903"], ["V2100"]),                     # Puerro
    72: (["04199908"], ["V4500"]),                     # Rábano
    74: (["04199905"], ["V4300"]),                     # Remolacha de mesa
    79: (["06310000"], ["F2100"]),                     # Higuera
    84: (["06194120"], ["F4200"]),                     # Avellano
    85: (["06194140"], ["F4400"]),                     # Castaño
    92: (["06199200"], ["F3200"]),                     # Frambuesa
}


def crop_price(crop_id, country: str) -> Optional[dict]:
    """Precio de venta {valor €/t, codigo, fuente, europeo €/t} de un cultivo, o None si Eurostat no lo publica."""
    codes = EUROSTAT_CODES.get(crop_id)
    if codes is None:
        return None
    return get_price(country, codes[0])


def crop_economics(crop_id, percent: Optional[float], country: str) -> Optional[dict]:
    """Precio, rendimiento e ingreso por hectárea de un cultivo, o None si Eurostat no lo publica."""
    codes = EUROSTAT_CODES.get(crop_id)
    if codes is None:
        return None
    market = get_market(country, *codes)
    price, crop_yield = market["precio"], market["rendimiento"]
    if price is None or crop_yield is None:
        return None
    income = price["valor"] * crop_yield["valor"]
    return {
        "precio_eur_t": round(price["valor"]),
        "rendimiento_t_ha": round(crop_yield["valor"], 2),
        "ingreso_medio_eur_ha": round(income),
        "ingreso_esperado_eur_ha": None if percent is None else round(income * percent / 100),
        "fuente_precio": price["fuente"],
        "fuente_rendimiento": crop_yield["fuente"],
    }
