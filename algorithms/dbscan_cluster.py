"""
algorithms/dbscan_cluster.py
-----------------------------
DBSCAN clustering of building centroids.

DBSCAN groups points that are densely packed together and marks
isolated points as noise. Good default for rural settings where
habitations have clear spatial gaps between them.

Config keys
-----------
eps_m       : float, default 120
    Maximum distance in metres between two points to be considered
    neighbours. Tune this to the typical gap between houses in your
    study area. Smaller → tighter, more clusters.

min_samples : int, default 3
    Minimum number of houses to form a cluster. Prevents single
    isolated buildings from becoming their own cluster.

Usage
-----
    from algorithms.dbscan_cluster import DBSCANCluster
    algo = DBSCANCluster(config={"eps_m": 100, "min_samples": 4})
    clusters_gdf = algo.run(points_gdf)
"""

import numpy as np
from sklearn.cluster import DBSCAN
import geopandas as gpd

from .base import ClusterAlgorithm
from .utils import labels_to_polygons


class DBSCANCluster(ClusterAlgorithm):

    name        = "dbscan"
    description = "DBSCAN — density-based, good for rural gaps"

    def run(self, points: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        eps_m       = self.config.get("eps_m", 120)
        min_samples = self.config.get("min_samples", 3)

        # Project to metric CRS, extract coords in metres
        pts_metric = points.to_crs(points.estimate_utm_crs())
        coords = np.column_stack([
            pts_metric.geometry.x,
            pts_metric.geometry.y
        ])

        db = DBSCAN(
            eps=eps_m,
            min_samples=min_samples,
            algorithm="ball_tree",
            metric="euclidean"
        ).fit(coords)

        gdf = labels_to_polygons(points, db.labels_, self.name)
        return self.validate_output(gdf)
