"""
algorithms/hdbscan_cluster.py
------------------------------
HDBSCAN clustering of building centroids.

HDBSCAN is a hierarchical extension of DBSCAN that automatically
handles varying densities — useful when your study area has both
dense village cores and scattered fringe houses. It also gives
a soft membership probability per point, which we use as confidence.

Config keys
-----------
min_cluster_size : int, default 5
    Minimum number of houses to form a persistent cluster.
    Larger → fewer, bigger clusters. Start at 5 and go up.

min_samples      : int, default None
    Controls conservativeness. Defaults to min_cluster_size.
    Increase to reduce noise.

cluster_selection_epsilon : float (metres), default 0
    Merge clusters closer than this distance. Useful if you're
    getting many tiny clusters that should be one village.

Usage
-----
    from algorithms.hdbscan_cluster import HDBSCANCluster
    algo = HDBSCANCluster(config={"min_cluster_size": 8})
    clusters_gdf = algo.run(points_gdf)
"""

import numpy as np
import geopandas as gpd

from .base import ClusterAlgorithm
from .utils import labels_to_polygons

try:
    import hdbscan
    _HDBSCAN_AVAILABLE = True
except ImportError:
    _HDBSCAN_AVAILABLE = False


class HDBSCANCluster(ClusterAlgorithm):

    name        = "hdbscan"
    description = "HDBSCAN — handles variable density, finds village cores"

    def run(self, points: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        if not _HDBSCAN_AVAILABLE:
            raise ImportError(
                "hdbscan is not installed. Run: pip install hdbscan"
            )

        min_cluster_size = self.config.get("min_cluster_size", 5)
        min_samples      = self.config.get("min_samples", None)
        epsilon          = self.config.get("cluster_selection_epsilon", 0)

        pts_metric = points.to_crs(points.estimate_utm_crs())
        coords = np.column_stack([
            pts_metric.geometry.x,
            pts_metric.geometry.y
        ])

        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            min_samples=min_samples,
            cluster_selection_epsilon=epsilon,
            metric="euclidean",
            prediction_data=True
        )
        clusterer.fit(coords)

        # Use soft membership probabilities to refine confidence
        pts_with_conf = points.copy()
        if "confidence" not in pts_with_conf.columns:
            pts_with_conf["confidence"] = 1.0
        pts_with_conf["confidence"] *= clusterer.probabilities_

        gdf = labels_to_polygons(pts_with_conf, clusterer.labels_, self.name)
        return self.validate_output(gdf)
