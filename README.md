# Cluster Survey

A lightweight field-survey tool for ground-truthing AI-detected household clusters on a map.

**The workflow:** Your spatial algorithm outputs GeoJSON cluster polygons → you upload them to a Supabase database → field surveyors open the map on their phones and confirm or flag each cluster.

---

## Quick start

### 1. Set up Supabase (free)

1. Create a project at [supabase.com](https://supabase.com)
2. Go to **SQL Editor** → paste the contents of `db/schema.sql` → **Run**
3. Go to **Project Settings → API** and copy:
   - Project URL (looks like `https://xxxx.supabase.co`)
   - Anon public key

### 2. Upload your clusters

```bash
pip install -r requirements.txt

python scripts/upload_clusters.py \
  --file data/your_clusters.geojson \
  --batch v2025-06-12
```

Your GeoJSON features need at minimum an `id` field in `properties`. See `data/sample/clusters_sample.geojson` for the full expected schema.

**Rerunning is safe.** New clusters are inserted as `pending`. Clusters that surveyors have already actioned keep their status.

To reset all statuses to `pending` (fresh batch):
```bash
python scripts/upload_clusters.py --file data/your_clusters.geojson --reset-status
```

### 3. Configure the app

Open `app/surveyor_view.html` and edit the two lines at the top:

```js
const SUPABASE_URL = "https://YOUR_PROJECT_ID.supabase.co";
const SUPABASE_KEY = "YOUR_ANON_KEY";
```

### 4. Share with surveyors

Host `app/surveyor_view.html` anywhere — GitHub Pages, Netlify (free), or just share the file directly. It works offline-ish once loaded; actions sync when connectivity returns.

> **GitHub Pages:** Push to `main`, go to Settings → Pages → Source: `main`, folder: `/app`. Your surveyors get a URL like `https://your-org.github.io/cluster-survey/surveyor_view.html`.

---

## Expected GeoJSON schema

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {
        "id":             "CLU-001",
        "name":           "Rampur Tola",
        "block":          "Karauli",
        "district":       "Karauli",
        "state":          "Rajasthan",
        "households":     34,
        "priority_score": 0.87,
        "area_ha":        2.1
      },
      "geometry": {
        "type": "Polygon",
        "coordinates": [...]
      }
    }
  ]
}
```

`id` is required and used as the primary key. All other fields are optional but recommended. `priority_score` (0–1) controls the sort order surveyors see clusters in.

---

## Database schema

Two tables in Supabase:

**`clusters`** — one row per polygon, updated in place as surveyors action them.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | from your GeoJSON |
| `name`, `block`, `district`, `state` | text | |
| `households` | integer | estimated |
| `priority_score` | numeric | 0–1, your model output |
| `area_ha` | numeric | |
| `geometry` | jsonb | GeoJSON geometry object |
| `status` | text | `pending` / `confirmed` / `flagged` |
| `flagged_reason` | text | set when status = flagged |
| `batch_id` | text | e.g. `v2025-06-12` |
| `created_at`, `updated_at` | timestamptz | auto-managed |

**`responses`** — append-only audit log of every surveyor action.

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial PK | |
| `cluster_id` | text FK | references clusters |
| `action` | text | `confirmed` / `flagged` |
| `reason` | text | flag reason(s) |
| `surveyor_id` | text | name entered on app load |
| `responded_at` | timestamptz | |

---

## Surveyor app features

- Cluster polygons colour-coded by status: **amber** pending, **green** confirmed, **red** flagged
- Tap any polygon → bottom sheet with cluster details and action buttons
- Flag reasons: Not a habitation · Low population · Already covered · Urban area · Wrong boundary · Inaccessible · Other
- OSM ↔ Esri satellite tile toggle
- Live stats bar (total / pending / confirmed / flagged)
- Actions write to Supabase instantly; map updates without a reload

---

## Project structure

```
cluster-survey/
├── app/
│   └── surveyor_view.html     # the mobile-first surveyor interface
├── data/
│   └── sample/
│       └── clusters_sample.geojson   # example input for testing
├── db/
│   └── schema.sql             # run once in Supabase SQL Editor
├── docs/                      # reserved for future documentation
├── scripts/
│   └── upload_clusters.py     # uploads GeoJSON → Supabase
├── .gitignore
├── CONTRIBUTING.md
├── README.md
└── requirements.txt
```

---

## Roadmap

- [ ] Admin upload UI (drag-and-drop GeoJSON in browser)
- [ ] Admin dashboard (response summary table, CSV export)
- [ ] Surveyor auth (replace `prompt()` with Supabase Auth)
- [ ] Offline support (IndexedDB cache for low-connectivity areas)

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Map | [Leaflet.js](https://leafletjs.com) + OpenStreetMap | Open source, mobile-friendly, no API key |
| Satellite tiles | Esri World Imagery | Free, no key needed |
| Database + API | [Supabase](https://supabase.com) | Free tier, instant REST API, no server to manage |
| Upload script | Python + `supabase-py` | Fits a data scientist's existing workflow |
| Frontend | Vanilla HTML/JS | No build step — share a single file |
