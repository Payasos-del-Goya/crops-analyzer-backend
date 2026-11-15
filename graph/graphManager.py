import numpy as np
from scipy.spatial.distance import cdist
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import minimum_spanning_tree
from sklearn.neighbors import kneighbors_graph
from sklearn.preprocessing import StandardScaler
from libpysal.weights import W
import geopandas as gpd
from shapely.geometry import Point
from spopt.region import Skater
from sklearn.metrics.pairwise import euclidean_distances


class GraphManager:
    def __init__(self):
        pass

    @staticmethod
    def normalize_coordinates(coordinates):
        coordinates = np.asarray(coordinates, dtype=float)

        if coordinates.ndim != 2 or coordinates.shape[1] != 2:
            raise ValueError("Las coordenadas deben tener formato (N, 2)")

        if len(coordinates) == 0:
            raise ValueError("No hay coordenadas")

        if not np.all(np.isfinite(coordinates)):
            raise ValueError("Coordenadas no válidas")
        
        # Coordenada de menor módulo en cada eje
        origin = coordinates[
            np.abs(coordinates).argmin(axis=0),
            np.arange(coordinates.shape[1])
        ]

        # Trasladar el origen
        shifted = coordinates - origin

        # Escala común para X e Y
        scale = np.max(np.abs(shifted))

        if scale == 0:
            return np.zeros_like(coordinates)

        return shifted / scale

    @staticmethod
    def create_graph(positions, k=4):
        """
        Create a graph from the given positions using k-nearest neighbors.

        :param positions: A 2D numpy array of shape (n_samples, n_features).
        :param k: The number of nearest neighbors to consider for each position.
        :return: A sparse matrix representing the graph.
        """
        
        if len(positions) == 0:
            raise ValueError("Positions array is empty.")
        if len (positions) == 1:
            return np.array([[0]])  # Return a single node graph if there's only one position
        
        # Create a k-nearest neighbors graph
        if len(positions) < k:
            k = len(positions) - 1  # Adjust k if there are fewer positions than k
        graph = kneighbors_graph(positions, n_neighbors=k, mode='distance', include_self=False)
        graph = graph.maximum(graph.T)  # Make the graph symmetric
        return graph


    @staticmethod
    def skate_clusting(graph,coordinates,parameters_list, parameters_keys, n_zones):

        def scipy_to_pysal(graph):
            graph = graph.tocsr()
            n = graph.shape[0]

            neighbors = {
                i: graph[i].indices.tolist()
                for i in range(n)
            }

            return W(neighbors, id_order=list(range(n)))

        scaler = StandardScaler()
        normalized_parameters = scaler.fit_transform(parameters_list)

        argument = {
            "geometry": [Point(x, y) for x, y in coordinates],
            **{key: list(values) for key, values in zip(parameters_keys, normalized_parameters.T)}
        }

        gdf = gpd.GeoDataFrame(argument)

        # Convertir grafo espacial
        w = scipy_to_pysal(graph)

        # Ejecutar SKATER
        model = Skater(
            gdf,
            w,
            attrs_name=parameters_keys,
            n_clusters= n_zones,
            spanning_forest_kwds={
                "dissimilarity": euclidean_distances
            }
        )

        model.solve()

        # Obtener agrupaciones
        labels = model.labels_

        return labels

    @staticmethod
    def zone_mean(labels, parameters_list):
        """
        Return the mean of each parameter component for each label group.
        Each parameter row is associated with its label and then the average is
        computed independently for every position in the tuple.
        """
        if len(labels) != len(parameters_list):
            raise ValueError("labels and parameters_list must have the same length")

        grouped = {}
        for label, params in zip(labels, parameters_list):
            grouped.setdefault(label, []).append(np.asarray(params, dtype=float))

        result = {}
        for label, rows in grouped.items():
            rows_array = np.asarray(rows, dtype=float)
            result[int(label)] = np.mean(rows_array, axis=0).tolist()

        return result

