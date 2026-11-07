# Lugar.ph routes

Jeepney routes for Philippine cities, as one GeoJSON file per city. These are the routes behind [Lugar.ph](https://github.com/lugarph/lugar-ph), and they're free to reuse under the [ODbL](#licence).

Every line is a best estimate. If you ride one and it's wrong, please fix it or tell us.

## Fix a wrong route

- **No Git needed:** [report it in an issue](../../issues/new?template=route-correction.yml). Say which line and what's wrong.
- **Correct it on a map:** run the editor below and open a pull request.
- **Mapped in OpenStreetMap?** Fix it [there](https://wiki.openstreetmap.org/wiki/Public_transport) too, so everyone gets the fix. The editor says when a line came from OpenStreetMap.

## Correct a route on a map

You need Python 3.10 or newer and nothing else.

```sh
git clone https://github.com/lugarph/lugar-ph-routes.git
cd lugar-ph-routes
python3 tools/serve.py
```

This opens a map on your computer. Pick a city and a line, then:

| Tool | What it does |
| --- | --- |
| **Move** | Drag a point to where the road really is. |
| **Add** | Drag anywhere on the line to add a point there. |
| **Extend** | Click the map to add points after the last one. |
| **Erase** | Click a point to remove it. |
| **Erase stretch** | Click the first and last points of a wrong stretch to remove everything between, then redraw it with **Add** or **Extend**. |
| **Landmark** | Add a place riders know. It joins the list in riding order. |
| **Test ride** | Click a start and a place to get off, to check this line can carry a rider that way. |

The dashed line is the committed version, and the arrows show the way the jeep runs. **Checks** runs the same rules as CI each time you edit. **Save correction** writes to the file in your checkout, and nothing is shared until you open a pull request. **Your changes** lists your edits, undoes them one line at a time, and gives you the commands to send them for review.

Saving sets the line's `status` to `community_verified` and recalculates `distance_km`. Rebuilding a city's routes keeps lines with that status, so your fix isn't overwritten.

Two things to know:

- **Stacked points.** Where the way out and the way back share a road, each has its own points in the same place. Moving one only moves that direction, so drag again if both directions changed.
- **Test ride checks one line.** It follows the same one-way rule as the app, but it doesn't plan transfers. To try the full planner, run [the app](https://github.com/lugarph/lugar-ph#run-it) with its `data/` folder on your branch.

## Open a pull request

Say how you know: a published route plan, a photo of the signboard, or a ride you took. CI checks the files and lists the lines you changed. Once it's merged, Lugar.ph picks the change up the next time it updates to the latest routes.

Without the editor, you can edit a file in [geojson.io](https://geojson.io), QGIS, or JOSM. Then run:

```sh
python3 tools/format.py
python3 tools/validate.py --base origin/main
```

## Files

| City | File |
| --- | --- |
| Baguio | `baguio_jeepney_network.geojson` |
| Naga | `naga_jeepney_network.geojson` |
| Bacolod | `jeepney_network.geojson` |
| Iloilo | `iloilo_jeepney_network.geojson` |
| Cebu | `cebu_jeepney_network.geojson` |
| Tacloban | `tacloban_jeepney_network.geojson` |
| Cagayan de Oro | `cdo_jeepney_network.geojson` |
| Davao | `davao_jeepney_network.geojson` |

`sources/` holds the route plans and place lists some cities are built from, and [SOURCES.md](SOURCES.md) says where each city's lines come from and what's known to be wrong. `road_segments.geojson` is a small set of road shapes the app draws.

Each city file is a `FeatureCollection` with one `LineString` feature per line, written with one coordinate per line so that moving a point changes one line of the diff.

```json
{
  "type": "Feature",
  "properties": {
    "id": "banago-libertad",
    "number": 1,
    "name": "Banago – Libertad Loop",
    "color": "#ef6f77",
    "status": "inferred_from_lptrp_anchors",
    "distance_km": 12.94,
    "anchors": [
      { "id": "banago_school", "name": "Banago School", "lng": 122.95053, "lat": 10.70964 },
      { "id": "libertad", "name": "Libertad", "lng": 122.94974, "lat": 10.66012 }
    ]
  },
  "geometry": { "type": "LineString", "coordinates": [[122.95053, 10.70964], [122.95061, 10.70941]] }
}
```

### Line properties

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `id` | string | yes | Stable slug, unique across all cities. Community notes are stored against it, so don't rename it. |
| `number` | string or int | yes | Route number or code shown on the jeep (`1`, `04L`), unique within the city. |
| `name` | string | yes | Route name as `Terminal – Terminal`, with an en dash. The signboard shown to riders drops `via …` and a trailing `Loop`. |
| `color` | hex string | yes | The line's colour on chips, directions, and maps. Pick one distinct from lines sharing its roads. Light colours get dark text automatically. |
| `status` | string | yes | Where the geometry came from. `osm_route_relation` (mapped street by street in OpenStreetMap) and `archived_dotc_gtfs_shape` are traced paths. `inferred_from_lptrp_anchors` and `inferred_from_official_route_structure` are estimated from route plans; riders see a note on those. |
| `mode` | string | no | `jeepney` (default) or `uv_express`. UV Express rides get no fare estimate. |
| `distance_km` | number | yes | Length of the drawn geometry. |
| `planned_length_km` | number or null | no | Length stated in the published route plan. |
| `source_url` | string | no | Document the line comes from. Defaults to the city's source. |
| `anchors` | array | yes | Named landmarks from the first terminal to the far terminal, in riding order (see below). |
| `return_anchors` | array | no | Landmarks passed only on the way back, when the return uses other streets. |
| `osm_relations` | array of ints | no | OpenStreetMap route relations the geometry was imported from, in riding order. |
| `attribution` | string | no | Required credit for imported geometry, such as `© OpenStreetMap contributors, ODbL`. |

### Anchors

| Property | Type | Meaning |
| --- | --- | --- |
| `id` | string | Slug for the landmark. |
| `name` | string | Place name riders know (`Ayala`, `Burgos Market`). Names of three letters or fewer are shown in capitals (`SM`). Names containing `endpoint` are ignored. |
| `lng`, `lat` | number | Position in WGS84. |

Anchors do three things. They name stops ("Get off near SM"), they give each ride its direction, and they feed instant search suggestions. The direction reads "toward" the last anchor on the way out and "toward" the first anchor on the way back.

### Geometry rules

Jeeps don't run both ways on the same roads, so every line is drawn one way, as it is driven:

- Coordinates are `[lng, lat]` in WGS84 and follow the roads the jeep drives.
- Draw the full circuit: start at the first anchor, drive out to the far terminal, and come back to the start. The line should end within 500 m of where it begins.
- Draw the way back as it is really driven. Main roads are often shared, but side streets, one-way pairs, and detours differ.
- Keep consecutive points under 500 m apart.
- The planner only rides a line in the order its points are drawn. On a circuit, a ride may continue past the start, the way a jeep carries on through its terminal.
- A line that doesn't come back to its start is treated as one-way and ends at its last point.

## Add a city

Add its file here and to `CITIES` in [`tools/routeformat.py`](tools/routeformat.py). Then add the city to the [app](https://github.com/lugarph/lugar-ph#run-it-for-your-city), which also needs its map centre and search area.

## Licence

The route files are made available under the [Open Database Licence (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/), because they include geometry imported from OpenStreetMap, © OpenStreetMap contributors. Keep the credit when you reuse them. See [LICENSE](LICENSE).

The code in `tools/` is under the [MIT Licence](tools/LICENSE).

`sources/` holds copies of public documents for reference, and they remain under their publishers' terms.
