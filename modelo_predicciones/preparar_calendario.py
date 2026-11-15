"""
Prepara modelo_predicciones/datos/calendario_siembra_ggcmi.csv.gz.

Fuente: GGCMI Phase 3 crop calendar (Jägermeyr et al., 2021), CC-BY 4.0,
https://zenodo.org/records/5062513. Para cada cultivo y celda de 0,5° da el día
medio de siembra y de madurez (secano). Aquí se recorta a Europa y se guarda
en una tabla pequeña que el backend consulta por coordenadas.

Uso (solo hace falta si se quiere regenerar; necesita netCDF4):
    python modelo_predicciones/preparar_calendario.py
"""
import urllib.request
from pathlib import Path

import netCDF4
import numpy as np
import pandas as pd

URL = "https://zenodo.org/api/records/5062513/files/{}_rf_ggcmi_crop_calendar_phase3_v1.01.nc4/content"
# Cultivos de GGCMI que usa el modelo: trigo de invierno, cebada, centeno, maíz, patata,
# colza, girasol, soja, sorgo, remolacha azucarera y arroz.
CROPS = ["wwh", "bar", "rye", "mai", "pot", "rap", "sun", "soy", "sor", "sgb", "ri1"]
LAT = (34.0, 72.0)
LON = (-25.0, 45.0)
OUT = Path(__file__).resolve().parent / "datos" / "calendario_siembra_ggcmi.csv.gz"


def main():
    rows = []
    cache = Path("cache_calendario")
    cache.mkdir(exist_ok=True)
    for crop in CROPS:
        path = cache / f"{crop}.nc4"
        if not path.exists():
            urllib.request.urlretrieve(URL.format(crop), path)
        data = netCDF4.Dataset(path)
        lat, lon = np.asarray(data["lat"][:]), np.asarray(data["lon"][:])
        planting, maturity = data["planting_day"][:], data["maturity_day"][:]
        for i in np.where((lat >= LAT[0]) & (lat <= LAT[1]))[0]:
            for j in np.where((lon >= LON[0]) & (lon <= LON[1]))[0]:
                if np.ma.is_masked(planting[i, j]) or np.ma.is_masked(maturity[i, j]):
                    continue
                rows.append({"cultivo": crop, "lat": float(lat[i]), "lon": float(lon[j]),
                             "siembra_dia": int(planting[i, j]), "madurez_dia": int(maturity[i, j])})
    table = pd.DataFrame(rows)
    table.to_csv(OUT, index=False)
    print(table.shape, "->", OUT)


if __name__ == "__main__":
    main()
