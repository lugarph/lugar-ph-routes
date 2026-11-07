"""Shared helpers for the route files: which file is which city, and how they are written.

Standard library only, so contributors need nothing but Python 3.10+.

Files are written with one coordinate per line. Moving one point then changes one
line in `git diff`, which is what makes a correction reviewable.
"""

from __future__ import annotations

import json
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# City id -> (display name, file). The id is the address the app serves: /cdo.
CITIES = {
    "baguio": ("Baguio City", "baguio_jeepney_network.geojson"),
    "naga": ("Naga City", "naga_jeepney_network.geojson"),
    "bacolod": ("Bacolod City", "jeepney_network.geojson"),
    "iloilo": ("Iloilo City", "iloilo_jeepney_network.geojson"),
    "cebu": ("Cebu City", "cebu_jeepney_network.geojson"),
    "tacloban": ("Tacloban City", "tacloban_jeepney_network.geojson"),
    "cdo": ("Cagayan de Oro", "cdo_jeepney_network.geojson"),
    "davao": ("Davao City", "davao_jeepney_network.geojson"),
}

# The status of any line a person has corrected. The app gives approved in-app
# edits the same status, and the build scripts never overwrite a line that has it.
CORRECTED_STATUS = "community_verified"


def network_path(city: str, root: Path = ROOT) -> Path:
    return root / CITIES[city][1]


def load_network(city: str, root: Path = ROOT) -> dict:
    return json.loads(network_path(city, root).read_text(encoding="utf-8"))


def _json(value: object, indent: int = 0) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    return text.replace("\n", "\n" + " " * indent)


def dumps_network(network: dict) -> str:
    """Serialise a network in the canonical form. Parsing the result gives `network` back."""
    extra = {key: value for key, value in network.items() if key != "features"}
    head = json.dumps(extra, ensure_ascii=False)[:-1]
    features = []
    for feature in network["features"]:
        parts = []
        for key, value in feature.items():
            if key == "geometry" and value.get("type") == "LineString":
                others = "".join(f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}, "
                                 for k, v in value.items() if k != "coordinates")
                points = ",\n".join("      " + json.dumps(point) for point in value["coordinates"])
                parts.append(f'    "geometry": {{{others}"coordinates": [\n{points}\n    ]}}')
            else:
                parts.append(f"    {json.dumps(key)}: {_json(value, 4)}")
        features.append("  {\n" + ",\n".join(parts) + "\n  }")
    return head + ', "features": [\n' + ",\n".join(features) + "\n]}\n"


def save_network(city: str, network: dict, root: Path = ROOT) -> None:
    """Write via a temporary file, so a crash can't leave half a route file behind."""
    path = network_path(city, root)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(dumps_network(network), encoding="utf-8")
    temporary.replace(path)


def distance_km(a_lng: float, a_lat: float, b_lng: float, b_lat: float) -> float:
    dlat, dlng = radians(b_lat - a_lat), radians(b_lng - a_lng)
    value = sin(dlat / 2) ** 2 + cos(radians(a_lat)) * cos(radians(b_lat)) * sin(dlng / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(value))


def path_km(coordinates: list) -> float:
    return sum(distance_km(a[0], a[1], b[0], b[1]) for a, b in zip(coordinates, coordinates[1:]))
