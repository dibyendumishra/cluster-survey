"""
algorithms/existing_detections.py
----------------------------------
Wraps your existing model's output as a "native" algorithm so surveyors
can compare it directly against DBSCAN / HDBSCAN / SAM results.

This is not a clustering algorithm — it just reads your pre-existing
house centroids (CSV or GeoJSON) and groups spatially nearby detections
into habitation polygons using a simple buffer-and-union approach.

Config keys
-----------
group_dist_m : float, default 150
    Detections within this distance (metres) are grouped into one
    habitation cluster polygon. Tune to match your study area.

min_cluster_size : int, default 2
    Groups smaller than this are discarded as isolated detections.

Usage
-----
    from algorithms.existing_detections import ExistingDetections
    algo = ExistingDetections(config={"group_dist_m": 120})

    # From CSV (lat/lon columns)
    points = existing_detections_to_points("data/my_detections.csv")
    clusters_gdf = algo.run(points)

    # Or pipe directly from your model output GeoDataFrame
    clusters_gdf = algo.run(my_model_output_gdf)
"""

import numpy as np
import geopandas as gpd
from shapely.ops import unary_union

from .base import ClusterAlgorithm


class ExistingDetections(ClusterAlgorithm):

    name        = "existing"
    description = "Your model — baseline detections for comparison"

    def run(self, points: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        group_dist_m     = self.config.get("group_dist_m", 150)
        min_cluster_size = self.config.get("min_cluster_size", 2)

        metric_crs = points.estimate_utm_crs()
        pts_metric = points.to_crs(metric_crs)

        # Buffer each point, dissolve overlapping buffers into clusters
        buffered = pts_metric.copy()
        buffered["geometry"] = buffered.geometry.buffer(group_dist_m / 2)
        dissolved = (
            buffered
            .dissolve()
            .explode(index_parts=False)
            .reset_index(drop=True)
        )

        rows = []
        cluster_idx = 1
        for cluster_geom in dissolved.geometry:
            inside = pts_metric[pts_metric.geometry.within(cluster_geom)]
            if len(inside) < min_cluster_size:
                continue

            # Actual polygon = convex hull of member points
            cluster_poly = unary_union(inside.geometry).convex_hull
            cluster_wgs  = (
                gpd.GeoSeries([cluster_poly], crs=metric_crs)
                .to_crs("EPSG:4326")
                .iloc[0]
            )

            n_houses  = len(inside)
            area_ha   = float(cluster_poly.area) / 10_000
            mean_conf = float(inside["confidence"].mean()) if "confidence" in inside.columns else 1.0

            rows.append({
                "geometry":       cluster_wgs,
                "cluster_id":     f"EXISTING-{cluster_idx:03d}",
                "algorithm":      self.name,
                "households":     n_houses,
                "area_ha":        round(area_ha, 2),
                "_raw_score":     n_houses * mean_conf,
            })
            cluster_idx += 1

        if not rows:
            return gpd.GeoDataFrame(
                columns=["geometry","cluster_id","algorithm","households","priority_score","area_ha"],
                crs="EPSG:4326"
            )

        gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
        mx = gdf["_raw_score"].max()
        gdf["priority_score"] = (gdf["_raw_score"] / mx).round(3) if mx > 0 else 0.5
        gdf = gdf.drop(columns=["_raw_score"])

        return self.validate_output(gdf)
