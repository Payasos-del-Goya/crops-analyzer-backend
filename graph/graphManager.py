import warnings

import numpy as np
import geopandas as gpd
from libpysal.weights import W
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, minimum_spanning_tree
from scipy.spatial.distance import pdist, squareform
from shapely.geometry import Point
from sklearn.metrics.pairwise import euclidean_distances
from sklearn.preprocessing import StandardScaler
from spopt.region import Skater

EARTH_RADIUS_M = 6_371_000.0


class GraphManager:

    @staticmethod
    def normalize_coordinates(coordinates):
        """
        (latitud, longitud) en grados -> (x, y) en metros respecto al centro.

        Proyección equirectangular: a la escala de una finca es exacta de sobra
        y, a diferencia de usar los grados tal cual, un grado de longitud no
        cuenta lo mismo que uno de latitud (a 41° N mide un 25 % menos).
        """
        coordinates = np.asarray(coordinates, dtype=float)

        if coordinates.ndim != 2 or coordinates.shape[1] != 2 or len(coordinates) == 0:
            raise ValueError("Las coordenadas deben tener formato (N, 2) con N > 0")
        if not np.all(np.isfinite(coordinates)):
            raise ValueError("Coordenadas no válidas")

        lat = np.radians(coordinates[:, 0])
        lon = np.radians(coordinates[:, 1])
        lat0 = lat.mean()
        x = (lon - lon.mean()) * np.cos(lat0) * EARTH_RADIUS_M
        y = (lat - lat0) * EARTH_RADIUS_M
        return np.column_stack([x, y])

    @staticmethod
    def create_graph(positions, k=2):
        """
        Grafo de adyacencia entre balizas: cada una se une a sus k vecinas más
        cercanas y, además, se añaden las aristas del árbol de expansión mínima
        de todas las distancias.

        El kNN solo no basta: si las balizas están agrupadas por parcelas, cada
        grupo se queda aislado y SKATER devuelve un cluster extra por cada
        componente suelta. El árbol garantiza un único componente uniendo cada
        grupo con el más cercano.

        Devuelve una matriz dispersa simétrica de conectividad (1 = adyacentes).
        """
        positions = np.asarray(positions, dtype=float)
        n = len(positions)
        if n == 0:
            raise ValueError("No hay posiciones")
        if n == 1:
            return csr_matrix((1, 1))

        dist = squareform(pdist(positions))
        k = max(1, min(k, n - 1))

        adjacency = np.zeros((n, n), dtype=bool)
        # k vecinas más cercanas (la columna 0 del argsort es la propia baliza).
        order = np.argsort(dist, axis=1, kind="stable")
        rows = np.repeat(np.arange(n), k)
        adjacency[rows, order[:, 1:k + 1].ravel()] = True

        # Árbol de expansión mínima. Se suma un epsilon para que dos balizas en
        # el mismo punto (distancia 0) no se pierdan como "sin arista".
        mst = minimum_spanning_tree(csr_matrix(dist + 1e-9 * (1 - np.eye(n))))
        adjacency |= mst.toarray() > 0

        adjacency |= adjacency.T
        np.fill_diagonal(adjacency, False)
        return csr_matrix(adjacency.astype(float))

    @staticmethod
    def skate_clusting(graph, coordinates, parameters_list, parameters_keys, n_zones):
        """
        Divide las balizas en n_zones zonas contiguas en el grafo y parecidas en
        sus parámetros (SKATER). Devuelve una etiqueta int por baliza, 0..n_zones-1.
        """
        n = len(parameters_list)
        if n == 0:
            return []
        n_zones = int(max(1, min(int(n_zones), n)))
        if n_zones == 1 or n == 1:
            return [0] * n

        graph = graph.tocsr()
        n_components, _ = connected_components(graph, directed=False)
        if n_components > 1:
            # Con islas spopt sube n_clusters por su cuenta: mejor fallar claro.
            raise ValueError(f"El grafo de balizas tiene {n_components} componentes; debe ser conexo")

        values = np.asarray(parameters_list, dtype=float)
        # Huecos (una baliza sin pH, p. ej.): media del parámetro en el resto.
        # Columnas sin ningún dato o constantes no aportan nada: fuera.
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # columna toda NaN
            col_mean = np.nanmean(values, axis=0)
        values = np.where(np.isnan(values), col_mean, values)
        usable = [i for i in range(values.shape[1]) if np.isfinite(values[:, i]).all() and np.ptp(values[:, i]) > 0]

        if not usable:
            # Nada que diferencie a las balizas: zonas solo por cercanía.
            keys = ["x", "y"]
            normalized = StandardScaler().fit_transform(np.asarray(coordinates, dtype=float))
        else:
            keys = [str(parameters_keys[i]) for i in usable]
            normalized = StandardScaler().fit_transform(values[:, usable])

        gdf = gpd.GeoDataFrame(
            {key: normalized[:, j] for j, key in enumerate(keys)},
            geometry=[Point(x, y) for x, y in coordinates],
        )

        neighbors = {i: graph[i].indices.tolist() for i in range(n)}
        w = W(neighbors, id_order=list(range(n)), silence_warnings=True)

        model = Skater(
            gdf,
            w,
            attrs_name=keys,
            n_clusters=n_zones,
            spanning_forest_kwds={"dissimilarity": euclidean_distances},
        )
        model.solve()

        # Reetiquetar a 0..k-1 por orden de aparición y como int de Python
        # (np.int32 no se serializa a JSON).
        remap = {}
        return [remap.setdefault(int(label), len(remap)) for label in model.labels_]

    @staticmethod
    def zone_mean(labels, parameters_list):
        """
        Media de cada parámetro por zona, ignorando huecos (NaN).
        Devuelve {zona: [media_param_0, media_param_1, ...]} (None si no hay dato).
        """
        if len(labels) != len(parameters_list):
            raise ValueError("labels and parameters_list must have the same length")

        values = np.asarray(parameters_list, dtype=float)
        labels = np.asarray(labels)
        result = {}
        for label in sorted(set(labels.tolist())):
            rows = values[labels == label]
            means = []
            for col in rows.T:
                col = col[~np.isnan(col)]
                means.append(float(col.mean()) if len(col) else None)
            result[int(label)] = means
        return result
