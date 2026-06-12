"""
algorithms/base.py
------------------
Every clustering algorithm in this folder must subclass ClusterAlgorithm
and implement `run()`. That's the only contract.

Output is always a GeoDataFrame with one row per cluster polygon,
containing at minimum these columns:

  geometry        : shapely Polygon (in WGS-84 / EPSG:4326)
  cluster_id      : str  — unique within this run, e.g. "DBSCAN-001"
  algorithm       : str  — short name, e.g. "dbscan"
  households      : int  — estimated house count inside polygon
  priority_score  : float 0-1 — higher = more likely to be missed
  area_ha         : float

You can add extra columns freely; upload_clusters.py will pass them
through to Supabase as-is.
"""

from abc import ABC, abstractmethod
import geopandas as gpd


class ClusterAlgorithm(ABC):

    name: str = "base"          # short identifier, used as batch prefix
    description: str = ""       # shown in the surveyor app toggle

    def __init__(self, config: dict = None):
        """
        config: optional dict of algorithm-specific parameters.
        Subclasses should document what keys they accept.
        """
        self.config = config or {}

    @abstractmethod
    def run(self, points: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Parameters
        ----------
        points : GeoDataFrame
            One row per detected building/house centroid.
            Must have a 'geometry' column of shapely Points (EPSG:4326).
            May also have a 'confidence' column (0-1) from your detector.

        Returns
        -------
        GeoDataFrame with columns: geometry, cluster_id, algorithm,
        households, priority_score, area_ha.
        CRS must be EPSG:4326.
        """
        ...

    def validate_output(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        required = {"geometry", "cluster_id", "algorithm", "households",
                    "priority_score", "area_ha"}
        missing = required - set(gdf.columns)
        if missing:
            raise ValueError(f"{self.name}: output is missing columns: {missing}")
        if gdf.crs is None or gdf.crs.to_epsg() != 4326:
            raise ValueError(f"{self.name}: output CRS must be EPSG:4326")
        return gdf
