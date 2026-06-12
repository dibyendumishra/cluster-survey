# Contributing

## Architecture overview

```
algorithm output (.geojson)
        │
        ▼
scripts/upload_clusters.py   ← run once per batch
        │
        ▼
   Supabase DB                ← clusters + responses tables
        │
        ▼
app/surveyor_view.html        ← surveyors open on mobile
```

## Adding fields from your algorithm

1. Add the column to `db/schema.sql` and run the ALTER in Supabase SQL Editor:
   ```sql
   alter table clusters add column my_new_field text;
   ```
2. Add it to `feature_to_row()` in `scripts/upload_clusters.py`.
3. Display it in the info grid in `app/surveyor_view.html`.

## Adding flag reasons

Edit the chip list in `app/surveyor_view.html`:
```html
<span class="chip" onclick="toggleChip(this)">Your new reason</span>
```

## Changing the map tile provider

In `app/surveyor_view.html`, the two tile layers are defined near the top of the `<script>` block. Swap the URL for any XYZ tile provider (MapMyIndia, ESRI, CARTO, etc.).

## Planned next pieces

- [ ] Admin upload UI (drag-and-drop GeoJSON → Supabase)
- [ ] Admin dashboard (response summary, download CSV)
- [ ] Surveyor login (replace the `prompt()` with a proper auth flow)
- [ ] Offline support (cache clusters in IndexedDB for areas with poor connectivity)
