"""
algorithms/sam_cluster.py
--------------------------
SAM (Segment Anything Model) based building detection + clustering.

Unlike DBSCAN/HDBSCAN which cluster pre-detected points, SAM works
directly on the raster tile to segment building footprints. The
resulting footprints are then spatially grouped into habitation
clusters using a simple proximity union.

This uses the lightweight `mobile_sam` checkpoint by default, which
runs on CPU in reasonable time. Swap to `vit_h` for higher accuracy
if you have a GPU.

Config keys
-----------
checkpoint   : str
    Path to SAM model weights (.pth file).
    Download: https://github.com/facebookresearch/segment-anything

model_type   : str, default "vit_b"
    One of: "vit_h", "vit_l", "vit_b", "mobile_sam"

group_dist_m : float, default 200
    Buildings within this distance (metres) are merged into one
    habitation cluster polygon.

min_area_m2  : float, default 20
    Discard segments smaller than this (removes road markings,
    tree canopy noise, etc.)

max_area_m2  : float, default 5000
    Discard segments larger than this (removes fields, water bodies).

Usage
-----
    from algorithms.sam_cluster import SAMCluster
    algo = SAMCluster(config={
        "checkpoint": "weights/sam_vit_b.pth",
        "model_type": "vit_b",
        "group_dist_m": 150
    })
    clusters_gdf = algo.run_on_raster("data/my_tile.tif")

Note: SAMCluster.run() accepts the same point GeoDataFrame as other
algorithms (for pipeline compatibility) but also exposes
run_on_raster() which takes a GeoTIFF path directly.
"""

import numpy as np
import geopandas as gpd
from shapely.geometry import shape
from shapely.ops import unary_union
import rasterio
from rasterio.transform import xy as rasterio_xy

from .base import ClusterAlgorithm

try:
    import torch
    from segment_anything import sam_model_registry, SamAutomaticMaskGenerator
    _SAM_AVAILABLE = True
except ImportError:
    _SAM_AVAILABLE = False


class SAMCluster(ClusterAlgorithm):

    name        = "sam"
    description = "SAM (Segment Anything) — segments buildings from imagery"

    def run(self, points: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Pipeline-compatible entry point. Expects points to have been
        derived from a raster; uses their bounding box to find the
        source tile. Prefer run_on_raster() for direct raster input.
        """
        raise NotImplementedError(
            "SAMCluster works on raster tiles directly. "
            "Use run_on_raster('path/to/tile.tif') instead, "
            "then pass the result to upload_clusters.py."
        )

    def run_on_raster(self, tiff_path: str) -> gpd.GeoDataFrame:
        """
        Run SAM on a GeoTIFF and return a cluster GeoDataFrame.

        Parameters
        ----------
        tiff_path : path to a GeoTIFF (RGB or single-band)

        Returns
        -------
        GeoDataFrame matching ClusterAlgorithm output schema
        """
        if not _SAM_AVAILABLE:
            raise ImportError(
                "segment-anything is not installed.\n"
                "pip install git+https://github.com/facebookresearch/segment-anything.git\n"
                "pip install torch torchvision"
            )

        checkpoint   = self.config.get("checkpoint")
        model_type   = self.config.get("model_type", "vit_b")
        group_dist_m = self.config.get("group_dist_m", 200)
        min_area_m2  = self.config.get("min_area_m2", 20)
        max_area_m2  = self.config.get("max_area_m2", 5000)

        if not checkpoint:
            raise ValueError("SAMCluster requires config['checkpoint'] path to .pth weights.")

        # ── Load model ────────────────────────────────────────────────────────
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"  SAM: loading {model_type} on {device}")

        sam = sam_model_registry[model_type](checkpoint=checkpoint)
        sam.to(device=device)

        mask_gen = SamAutomaticMaskGenerator(
            model=sam,
            points_per_side=32,         # lower = faster, less detail
            pred_iou_thresh=0.88,
            stability_score_thresh=0.95,
            min_mask_region_area=100,   # pixels
        )

        # ── Read raster ───────────────────────────────────────────────────────
        with rasterio.open(tiff_path) as src:
            transform = src.transform
            crs       = src.crs
            img       = src.read()      # (bands, H, W)

        # SAM expects uint8 RGB (H, W, 3)
        if img.shape[0] == 1:
            img = np.repeat(img, 3, axis=0)
        img_rgb = np.moveaxis(img[:3], 0, -1)
        if img_rgb.dtype != np.uint8:
            img_rgb = ((img_rgb - img_rgb.min()) /
                       (img_rgb.max() - img_rgb.min() + 1e-8) * 255).astype(np.uint8)

        print(f"  SAM: generating masks for {tiff_path} ({img_rgb.shape})")
        masks = mask_gen.generate(img_rgb)
        print(f"  SAM: {len(masks)} raw segments found")

        # ── Masks → geographic polygons ───────────────────────────────────────
        src_crs = crs
        building_polys = []

        for m in masks:
            seg   = m["segmentation"].astype(np.uint8)
            score = m["predicted_iou"]

            # Convert mask to polygon via rasterio shapes
            for geom_dict, val in rasterio.features.shapes(seg, transform=transform):
                if val == 0:
                    continue
                poly = shape(geom_dict)
                building_polys.append({"geometry": poly, "confidence": float(score)})

        if not building_polys:
            print("  SAM: no building polygons detected")
            return gpd.GeoDataFrame(
                columns=["geometry","cluster_id","algorithm","households","priority_score","area_ha"],
                crs="EPSG:4326"
            )

        gdf_raw = gpd.GeoDataFrame(building_polys, crs=src_crs).to_crs("EPSG:4326")
        metric_crs = gdf_raw.estimate_utm_crs()
        gdf_metric = gdf_raw.to_crs(metric_crs)

        # ── Filter by area ────────────────────────────────────────────────────
        areas = gdf_metric.geometry.area
        gdf_metric = gdf_metric[(areas >= min_area_m2) & (areas <= max_area_m2)].copy()
        print(f"  SAM: {len(gdf_metric)} segments after area filter")

        if gdf_metric.empty:
            return gpd.GeoDataFrame(
                columns=["geometry","cluster_id","algorithm","households","priority_score","area_ha"],
                crs="EPSG:4326"
            )

        # ── Group nearby buildings into habitation clusters ───────────────────
        # Buffer each building by half the grouping distance, union overlapping
        buffered = gdf_metric.copy()
        buffered["geometry"] = buffered.geometry.buffer(group_dist_m / 2)
        dissolved = buffered.dissolve().explode(index_parts=False).reset_index(drop=True)

        rows = []
        for i, cluster_geom in enumerate(dissolved.geometry):
            # Find buildings inside this cluster
            inside = gdf_metric[gdf_metric.geometry.within(cluster_geom)]
            if inside.empty:
                continue

            # Cluster footprint = union of actual building polygons (not buffered)
            cluster_poly = unary_union(inside.geometry).convex_hull
            cluster_wgs  = (
                gpd.GeoSeries([cluster_poly], crs=metric_crs)
                .to_crs("EPSG:4326")
                .iloc[0]
            )

            n_houses  = len(inside)
            area_ha   = float(cluster_poly.area) / 10_000
            mean_conf = float(inside["confidence"].mean())

            rows.append({
                "geometry":       cluster_wgs,
                "cluster_id":     f"SAM-{i+1:03d}",
                "algorithm":      self.name,
                "households":     n_houses,
                "area_ha":        round(area_ha, 2),
                "_raw_score":     n_houses * mean_conf,
            })

        if not rows:
            return gpd.GeoDataFrame(
                columns=["geometry","cluster_id","algorithm","households","priority_score","area_ha"],
                crs="EPSG:4326"
            )

        gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
        mx = gdf["_raw_score"].max()
        gdf["priority_score"] = (gdf["_raw_score"] / mx).round(3) if mx > 0 else 0.5
        gdf = gdf.drop(columns=["_raw_score"])

        print(f"  SAM: {len(gdf)} habitation clusters")
        return self.validate_output(gdf)
