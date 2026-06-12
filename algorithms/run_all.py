"""
algorithms/run_all.py
----------------------
Orchestrates multiple clustering algorithms on the same input and
uploads all results to Supabase, tagged by algorithm name.

Surveyors can then toggle between algorithm outputs in the app.

Usage
-----
  # Run all algorithms on detected house points from a CSV:
  python algorithms/run_all.py \\
      --input data/my_detections.csv \\
      --input-type csv \\
      --batch v2025-06-12 \\
      --algorithms dbscan hdbscan existing

  # Run SAM directly on a GeoTIFF:
  python algorithms/run_all.py \\
      --input data/my_tile.tif \\
      --input-type raster \\
      --algorithms sam \\
      --sam-checkpoint weights/sam_vit_b.pth

  # Run everything:
  python algorithms/run_all.py \\
      --input data/my_detections.csv \\
      --input-type csv \\
      --algorithms all
"""

import argparse
import json
import sys
import os
from datetime import datetime

# Allow running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from algorithms.utils import geotiff_to_points, existing_detections_to_points
from algorithms.dbscan_cluster import DBSCANCluster
from algorithms.hdbscan_cluster import HDBSCANCluster
from algorithms.sam_cluster import SAMCluster
from algorithms.existing_detections import ExistingDetections

# ── Supabase config (same as upload_clusters.py) ──────────────────────────────
SUPABASE_URL = "https://YOUR_PROJECT_ID.supabase.co"
SUPABASE_KEY = "YOUR_ANON_KEY"

# ── Algorithm registry ────────────────────────────────────────────────────────
# Add new algorithms here — that's the only change needed to register them.
REGISTRY = {
    "dbscan":   DBSCANCluster,
    "hdbscan":  HDBSCANCluster,
    "sam":      SAMCluster,
    "existing": ExistingDetections,
}


def gdf_to_geojson_features(gdf):
    """Convert GeoDataFrame rows to list of GeoJSON feature dicts."""
    features = []
    for _, row in gdf.iterrows():
        props = row.drop("geometry").to_dict()
        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": row.geometry.__geo_interface__
        })
    return features


def upload_to_supabase(features, batch_id, reset_status=False):
    """Upload algorithm output features to Supabase clusters table."""
    try:
        from supabase import create_client
    except ImportError:
        print("  supabase not installed. Run: pip install supabase")
        return

    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    rows = []
    for f in features:
        p = f["properties"]
        rows.append({
            "id":             p["cluster_id"],
            "name":           p.get("name", p["cluster_id"]),
            "algorithm":      p["algorithm"],
            "households":     p.get("households"),
            "priority_score": p.get("priority_score"),
            "area_ha":        p.get("area_ha"),
            "geometry":       f["geometry"],
            "batch_id":       batch_id,
            "status":         "pending",
        })

    if reset_status:
        client.table("clusters").upsert(rows, on_conflict="id").execute()
    else:
        ids = [r["id"] for r in rows]
        existing = {
            r["id"] for r in
            client.table("clusters").select("id").in_("id", ids).execute().data
        }
        new_rows = [r for r in rows if r["id"] not in existing]
        old_rows = [r for r in rows if r["id"] in existing]

        if new_rows:
            client.table("clusters").insert(new_rows).execute()
        for r in old_rows:
            upd = {k: v for k, v in r.items() if k not in ("status", "flagged_reason")}
            client.table("clusters").update(upd).eq("id", r["id"]).execute()

    print(f"    Uploaded {len(rows)} clusters to Supabase")


def run_algorithm(name, cls, points, config, batch_id, output_dir, upload, reset_status):
    print(f"\n[{name.upper()}] running...")
    algo = cls(config=config)

    if name == "sam":
        raise ValueError(
            "SAM requires run_on_raster(). Pass --input-type raster with a GeoTIFF."
        )

    gdf = algo.run(points)
    print(f"  → {len(gdf)} clusters found")

    # Save locally
    out_path = os.path.join(output_dir, f"clusters_{name}_{batch_id}.geojson")
    gdf.to_file(out_path, driver="GeoJSON")
    print(f"  → saved to {out_path}")

    if upload:
        features = gdf_to_geojson_features(gdf)
        upload_to_supabase(features, batch_id, reset_status)

    return gdf


def main():
    parser = argparse.ArgumentParser(description="Run clustering algorithms and upload to Supabase")
    parser.add_argument("--input",         required=True,  help="Path to input file")
    parser.add_argument("--input-type",    default="csv",  choices=["csv", "raster", "geojson"],
                                           help="Input format")
    parser.add_argument("--algorithms",    nargs="+",      default=["dbscan"],
                                           choices=list(REGISTRY.keys()) + ["all"],
                                           help="Which algorithms to run")
    parser.add_argument("--batch",         default=datetime.today().strftime("%Y-%m-%d"),
                                           help="Batch label")
    parser.add_argument("--output-dir",    default="data/outputs",
                                           help="Directory for local GeoJSON outputs")
    parser.add_argument("--no-upload",     action="store_true",
                                           help="Skip Supabase upload (save locally only)")
    parser.add_argument("--reset-status",  action="store_true",
                                           help="Reset cluster status to pending on re-run")
    # Per-algorithm tuning
    parser.add_argument("--dbscan-eps",    type=float, default=120, help="DBSCAN eps in metres")
    parser.add_argument("--dbscan-min",    type=int,   default=3,   help="DBSCAN min_samples")
    parser.add_argument("--hdbscan-min",   type=int,   default=5,   help="HDBSCAN min_cluster_size")
    parser.add_argument("--sam-checkpoint",             default=None, help="Path to SAM .pth weights")
    parser.add_argument("--sam-model",                  default="vit_b")
    parser.add_argument("--group-dist",    type=float, default=150, help="Group distance metres (existing/SAM)")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    algo_names = list(REGISTRY.keys()) if "all" in args.algorithms else args.algorithms

    # ── Load input ────────────────────────────────────────────────────────────
    points = None
    if args.input_type == "csv":
        print(f"Loading CSV: {args.input}")
        points = existing_detections_to_points(args.input)
        print(f"  {len(points)} house detections loaded")
    elif args.input_type == "geojson":
        import geopandas as gpd
        points = gpd.read_file(args.input).to_crs("EPSG:4326")
        if "confidence" not in points.columns:
            points["confidence"] = 1.0
        print(f"  {len(points)} features loaded from GeoJSON")
    elif args.input_type == "raster":
        if "sam" not in algo_names:
            print("Loading raster as probability map (single-band expected)...")
            from algorithms.utils import geotiff_to_points
            points = geotiff_to_points(args.input)
            print(f"  {len(points)} detections above threshold")

    # ── Run each algorithm ────────────────────────────────────────────────────
    configs = {
        "dbscan":   {"eps_m": args.dbscan_eps, "min_samples": args.dbscan_min},
        "hdbscan":  {"min_cluster_size": args.hdbscan_min},
        "sam":      {"checkpoint": args.sam_checkpoint, "model_type": args.sam_model,
                     "group_dist_m": args.group_dist},
        "existing": {"group_dist_m": args.group_dist},
    }

    results = {}
    for name in algo_names:
        cls = REGISTRY[name]

        if name == "sam" and args.input_type == "raster":
            print(f"\n[SAM] running on raster {args.input}...")
            algo = cls(config=configs["sam"])
            gdf  = algo.run_on_raster(args.input)
            print(f"  → {len(gdf)} clusters found")
            out_path = os.path.join(args.output_dir, f"clusters_sam_{args.batch}.geojson")
            gdf.to_file(out_path, driver="GeoJSON")
            print(f"  → saved to {out_path}")
            if not args.no_upload:
                upload_to_supabase(gdf_to_geojson_features(gdf), args.batch, args.reset_status)
            results[name] = gdf
        elif name == "sam":
            print("[SAM] skipped — requires --input-type raster")
        else:
            if points is None:
                print(f"[{name}] skipped — no point input loaded")
                continue
            gdf = run_algorithm(
                name, cls, points, configs[name],
                args.batch, args.output_dir,
                upload=not args.no_upload,
                reset_status=args.reset_status
            )
            results[name] = gdf

    print(f"\nDone. {len(results)} algorithm(s) ran.")
    for name, gdf in results.items():
        print(f"  {name}: {len(gdf)} clusters")


if __name__ == "__main__":
    main()
