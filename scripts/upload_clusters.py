"""
upload_clusters.py
------------------
Reads a GeoJSON FeatureCollection from disk and upserts
each feature into the Supabase `clusters` table.

Usage:
    pip install supabase
    python upload_clusters.py --file clusters.geojson --batch v2025-06-12

Expected GeoJSON feature properties (add whatever your algo outputs):
{
  "id":             "CLU-001",       # required, used as primary key
  "name":           "Rampur Tola",
  "block":          "Karauli",
  "district":       "Karauli",
  "state":          "Rajasthan",
  "households":     34,
  "priority_score": 0.87,
  "area_ha":        2.1
}
"""

import json
import argparse
from datetime import datetime
from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────
SUPABASE_URL = "https://YOUR_PROJECT_ID.supabase.co"
SUPABASE_KEY = "YOUR_ANON_KEY"          # anon key is fine for uploads


def load_geojson(path: str) -> list[dict]:
    with open(path) as f:
        fc = json.load(f)
    assert fc["type"] == "FeatureCollection", "Expected a FeatureCollection"
    return fc["features"]


def feature_to_row(feature: dict, batch_id: str) -> dict:
    props = feature["properties"]
    geom  = feature["geometry"]

    # Required: id must exist in properties
    cluster_id = props.get("id")
    if not cluster_id:
        raise ValueError(f"Feature missing 'id' in properties: {props}")

    return {
        "id":             cluster_id,
        "name":           props.get("name"),
        "block":          props.get("block"),
        "district":       props.get("district"),
        "state":          props.get("state"),
        "households":     props.get("households"),
        "priority_score": props.get("priority_score"),
        "area_ha":        props.get("area_ha"),
        "geometry":       geom,           # stored as JSONB
        "batch_id":       batch_id,
        # Don't overwrite status/flagged_reason if cluster already exists
        # — the upsert below uses ignoreDuplicates=False but only touches
        #   non-status columns via on_conflict
    }


def upload(features: list[dict], batch_id: str, reset_status: bool = False):
    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    rows = [feature_to_row(f, batch_id) for f in features]

    if reset_status:
        # Full overwrite including status (use for a fresh batch)
        for row in rows:
            row["status"] = "pending"
            row["flagged_reason"] = None
        result = (
            client.table("clusters")
            .upsert(rows, on_conflict="id")
            .execute()
        )
    else:
        # Upsert geometry/metadata only; preserve existing status
        # Supabase upsert with ignoreDuplicates keeps existing rows untouched
        # for matching ids, so we split: insert new, update geometry on existing.
        ids = [r["id"] for r in rows]
        existing_resp = client.table("clusters").select("id").in_("id", ids).execute()
        existing_ids  = {r["id"] for r in existing_resp.data}

        new_rows      = [r for r in rows if r["id"] not in existing_ids]
        existing_rows = [r for r in rows if r["id"] in existing_ids]

        if new_rows:
            for row in new_rows:
                row["status"] = "pending"
            client.table("clusters").insert(new_rows).execute()
            print(f"  Inserted {len(new_rows)} new clusters")

        for row in existing_rows:
            # Update geometry + metadata only, leave status alone
            update_fields = {k: v for k, v in row.items()
                             if k not in ("status", "flagged_reason")}
            client.table("clusters").update(update_fields).eq("id", row["id"]).execute()
        if existing_rows:
            print(f"  Updated geometry for {len(existing_rows)} existing clusters")

    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file",         required=True, help="Path to .geojson file")
    parser.add_argument("--batch",        default=datetime.today().strftime("%Y-%m-%d"),
                                          help="Batch label (default: today's date)")
    parser.add_argument("--reset-status", action="store_true",
                        help="Reset all clusters to 'pending' (fresh batch)")
    args = parser.parse_args()

    print(f"Loading {args.file}...")
    features = load_geojson(args.file)
    print(f"Found {len(features)} features. Uploading to Supabase (batch={args.batch})...")

    rows = upload(features, args.batch, reset_status=args.reset_status)
    print(f"Done. {len(rows)} clusters in Supabase.")


if __name__ == "__main__":
    main()
