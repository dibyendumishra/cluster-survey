"""
algorithms/utils.py
-------------------
Shared helpers for reading raster tiles, extracting building centroids,
and converting cluster labels → GeoJSON polygons.
"""

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.features import shapes
from shapely.geometry import shape, MultiPoint
from shapely.ops import unary_union
import pandas as pd


# ── Raster → point cloud ──────────────────────────────────────────────────────

def geotiff_to_points(tiff_path: str, threshold: float = 0.5) -> gpd.GeoDataFrame:
    """
    Read a single-band probability GeoTIFF (output of your detector)
    and return a GeoDataFrame of building centroid Points.

    Each pixel above `threshold` is treated as a detected building.
    If your detector outputs multi-band imagery, use `band` to select
    the probability channel.

    Parameters
    ----------
    tiff_path  : path to GeoTIFF
    threshold  : float, pixels above this are treated as detections

    Returns
    -------
    GeoDataFrame with columns: geometry (Point, EPSG:4326), confidence
    """
    with rasterio.open(tiff_path) as src:
        data = src.read(1).astype(float)
        transform = src.transform
        crs = src.crs

        rows, cols = np.where(data >= threshold)
        confidences = data[rows, cols]

        # Pixel centre → geographic coordinates
        xs, ys = rasterio.transform.xy(transform, rows, cols, offset="center")

    gdf = gpd.GeoDataFrame(
        {"confidence": confidences},
        geometry=gpd.points_from_xy(xs, ys),
        crs=crs
    ).to_crs("EPSG:4326")

    return gdf


def existing_detections_to_points(csv_path: str,
                                  lat_col: str = "lat",
                                  lon_col: str = "lon",
                                  confidence_col: str = None) -> gpd.GeoDataFrame:
    """
    Load your existing house detections from a CSV with lat/lon columns.
    Use this when you already have centroids from a previous model run.
    """
    df = pd.read_csv(csv_path)
    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df[lon_col], df[lat_col]),
        crs="EPSG:4326"
    )
    if confidence_col and confidence_col in df.columns:
        gdf["confidence"] = df[confidence_col]
    else:
        gdf["confidence"] = 1.0
    return gdf


# ── Cluster labels → polygons ─────────────────────────────────────────────────

def labels_to_polygons(points: gpd.GeoDataFrame,
                       labels: np.ndarray,
                       algo_name: str,
                       buffer_m: float = 50.0) -> gpd.GeoDataFrame:
    """
    Convert an array of cluster labels (DBSCAN-style: -1 = noise)
    into a GeoDataFrame of convex-hull polygons, one per cluster.

    Parameters
    ----------
    points    : GeoDataFrame of input Points (EPSG:4326)
    labels    : 1-D int array, same length as points; -1 = noise/unclustered
    algo_name : short string used to build cluster_id values
    buffer_m  : metres to buffer each polygon outward (smooths boundaries)

    Returns
    -------
    GeoDataFrame with schema matching ClusterAlgorithm.run() output
    """
    # Project to a metric CRS for buffering (UTM zone auto-detected)
    pts_metric = points.to_crs(points.estimate_utm_crs())
    unique_labels = sorted(set(labels) - {-1})

    rows = []
    for i, lbl in enumerate(unique_labels):
        mask = labels == lbl
        cluster_pts = pts_metric[mask]

        if len(cluster_pts) < 2:
            geom = cluster_pts.geometry.iloc[0].buffer(buffer_m)
        else:
            geom = MultiPoint(list(cluster_pts.geometry)).convex_hull.buffer(buffer_m)

        # Back to WGS-84
        geom_wgs = (
            gpd.GeoSeries([geom], crs=pts_metric.crs)
            .to_crs("EPSG:4326")
            .iloc[0]
        )

        n_houses  = int(mask.sum())
        area_ha   = geom_wgs.area * 1e10 / 1e4  # rough degrees→ha approx; use metric for accuracy
        area_ha   = float(gpd.GeoSeries([geom], crs=pts_metric.crs).area.iloc[0]) / 10_000

        # Priority score: normalised house count weighted by mean confidence
        mean_conf = float(points[mask]["confidence"].mean()) if "confidence" in points.columns else 1.0
        raw_score = n_houses * mean_conf
        rows.append({
            "geometry":       geom_wgs,
            "cluster_id":     f"{algo_name.upper()}-{i+1:03d}",
            "algorithm":      algo_name,
            "households":     n_houses,
            "area_ha":        round(area_ha, 2),
            "_raw_score":     raw_score,
        })

    if not rows:
        return gpd.GeoDataFrame(columns=["geometry","cluster_id","algorithm",
                                         "households","priority_score","area_ha"],
                                crs="EPSG:4326")

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")

    # Normalise priority score to 0-1 across this run
    mx = gdf["_raw_score"].max()
    gdf["priority_score"] = (gdf["_raw_score"] / mx).round(3) if mx > 0 else 0.5
    gdf = gdf.drop(columns=["_raw_score"])

    return gdf.reset_index(drop=True)
