#!/usr/bin/env python3
"""Check every route file against the rules in README.md.

Errors would break the app or misinform riders, and fail CI. Warnings are worth a
look but can be legitimate, so they are only reported.

    python3 tools/validate.py                     check everything
    python3 tools/validate.py --city naga         check one city
    python3 tools/validate.py --base origin/main  also list the lines changed since a ref
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass

from routeformat import CITIES, ROOT, distance_km, dumps_network, network_path, path_km

# Roomy box around the Philippines: catches swapped lat/lng and stray points.
LNG_RANGE = (116.0, 127.5)
LAT_RANGE = (4.0, 21.5)
STATUSES = {
    "osm_route_relation", "archived_dotc_gtfs_shape", "inferred_from_lptrp_anchors",
    "inferred_from_official_route_structure", "community_verified",
}
MODES = {"jeepney", "uv_express"}
REQUIRED = ("id", "number", "name", "color", "status", "distance_km", "anchors")
CIRCUIT_GAP_KM = 0.5  # the app treats a line as a circuit inside this gap
JUMP_KM = 2.0
ANCHOR_OFF_LINE_KM = 0.4
LENGTH_TOLERANCE_KM = 0.05


@dataclass(frozen=True)
class Issue:
    level: str  # "error" or "warning"
    city: str
    line: str | None
    message: str

    def __str__(self) -> str:
        where = f"{self.city}/{self.line}" if self.line else self.city
        return f"{self.level.upper():7} {where}: {self.message}"


def _in_range(lng: object, lat: object) -> bool:
    return (isinstance(lng, (int, float)) and isinstance(lat, (int, float))
            and LNG_RANGE[0] <= lng <= LNG_RANGE[1] and LAT_RANGE[0] <= lat <= LAT_RANGE[1])


def _point_to_path_km(lng: float, lat: float, coordinates: list) -> float:
    # Vertices are dense enough on real lines; long sketched segments are sampled.
    best = float("inf")
    for first, second in zip(coordinates, coordinates[1:]):
        steps = max(1, int(distance_km(*first, *second) / 0.1))
        for step in range(steps):
            t = step / steps
            best = min(best, distance_km(lng, lat, first[0] + (second[0] - first[0]) * t, first[1] + (second[1] - first[1]) * t))
    return min(best, distance_km(lng, lat, *coordinates[-1]))


def check_feature(city: str, feature: dict) -> list[Issue]:
    """Rules for one line. The editor runs exactly these before saving."""
    properties = feature.get("properties") or {}
    line = str(properties.get("id") or "(no id)")
    issues: list[Issue] = []

    def error(message: str) -> None:
        issues.append(Issue("error", city, line, message))

    def warn(message: str) -> None:
        issues.append(Issue("warning", city, line, message))

    for key in REQUIRED:
        if properties.get(key) in (None, ""):
            error(f"missing required property `{key}`")
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates")
    if geometry.get("type") != "LineString" or not isinstance(coordinates, list) or len(coordinates) < 2:
        error("geometry must be a LineString with at least 2 points")
        return issues
    bad = [i for i, point in enumerate(coordinates)
           if not (isinstance(point, list) and len(point) >= 2 and _in_range(point[0], point[1]))]
    if bad:
        error(f"{len(bad)} point(s) outside the Philippines, first at index {bad[0]}: check for swapped lat/lng")
        return issues

    color = properties.get("color")
    if color and not re.fullmatch(r"#[0-9a-fA-F]{6}", str(color)):
        error(f"color `{color}` is not a #rrggbb value")
    status = properties.get("status")
    if status and status not in STATUSES:
        error(f"unknown status `{status}`")
    mode = properties.get("mode")
    if mode is not None and mode not in MODES:
        error(f"unknown mode `{mode}`")
    if properties.get("osm_relations") and not properties.get("attribution"):
        warn("has osm_relations but no attribution; ODbL requires the credit")

    drawn = path_km(coordinates)
    stated = properties.get("distance_km")
    if isinstance(stated, (int, float)) and abs(stated - drawn) > max(LENGTH_TOLERANCE_KM, drawn * 0.01):
        error(f"distance_km says {stated} but the drawn line is {drawn:.2f} km")

    for key in ("anchors", "return_anchors"):
        items = properties.get(key) or []
        if not isinstance(items, list):
            error(f"`{key}` must be a list")
            continue
        for item in items:
            if not isinstance(item, dict) or not item.get("name") or not _in_range(item.get("lng"), item.get("lat")):
                error(f"`{key}` has an entry without a name and a position in the Philippines: {item!r}")
            elif "endpoint" not in item["name"] and _point_to_path_km(item["lng"], item["lat"], coordinates) > ANCHOR_OFF_LINE_KM:
                warn(f"landmark `{item['name']}` is more than {int(ANCHOR_OFF_LINE_KM * 1000)} m from the line")

    gap = distance_km(*coordinates[0][:2], *coordinates[-1][:2])
    if gap > CIRCUIT_GAP_KM:
        warn(f"ends {gap:.2f} km from where it starts; draw the full out-and-back circuit")
    longest = max((distance_km(*a[:2], *b[:2]), i) for i, (a, b) in enumerate(zip(coordinates, coordinates[1:])))
    if longest[0] > JUMP_KM:
        warn(f"straight jump of {longest[0]:.1f} km after point {longest[1]}: it probably doesn't follow the road")
    return issues


def check_network(city: str, network: dict) -> list[Issue]:
    issues: list[Issue] = []
    if network.get("type") != "FeatureCollection" or not isinstance(network.get("features"), list):
        return [Issue("error", city, None, "file must be a GeoJSON FeatureCollection")]
    numbers: dict[str, str] = {}
    for feature in network["features"]:
        issues.extend(check_feature(city, feature))
        properties = feature.get("properties") or {}
        number, line = str(properties.get("number")), properties.get("id")
        if number in numbers and numbers[number] != line:
            issues.append(Issue("warning", city, line, f"number `{number}` is also used by `{numbers[number]}`"))
        numbers.setdefault(number, line)
    return issues


def check_all(cities: list[str], networks: dict[str, dict]) -> list[Issue]:
    issues: list[Issue] = []
    seen: dict[str, str] = {}
    for city in cities:
        issues.extend(check_network(city, networks[city]))
        for feature in networks[city].get("features", []):
            line = (feature.get("properties") or {}).get("id")
            if line in seen:
                issues.append(Issue("error", city, line, f"id is already used in {seen[line]}; ids must be unique across all cities"))
            elif line:
                seen[line] = city
    return issues


def changes_since(ref: str, city: str, network: dict, root=ROOT) -> list[str]:
    """Plain-language list of what changed in one city since a git ref."""
    name = network_path(city).name
    shown = subprocess.run(["git", "show", f"{ref}:{name}"], cwd=root, capture_output=True, text=True, encoding="utf-8")
    if shown.returncode != 0:
        return [f"{city}: new file"]
    before = {feature["properties"]["id"]: feature for feature in json.loads(shown.stdout)["features"]}
    after = {feature["properties"]["id"]: feature for feature in network["features"]}
    lines = []
    for line in sorted(after.keys() - before.keys()):
        lines.append(f"{city}/{line}: added ({path_km(after[line]['geometry']['coordinates']):.2f} km)")
    for line in sorted(before.keys() - after.keys()):
        lines.append(f"{city}/{line}: REMOVED. Community notes are stored against this id")
    for line in sorted(after.keys() & before.keys()):
        if after[line] != before[line]:
            old, new = before[line]["geometry"]["coordinates"], after[line]["geometry"]["coordinates"]
            what = f"{path_km(old):.2f} -> {path_km(new):.2f} km, {len(old)} -> {len(new)} points" if old != new else "details only"
            lines.append(f"{city}/{line}: changed ({what})")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--city", choices=sorted(CITIES), help="Check one city only")
    parser.add_argument("--base", help="Also list lines changed since this git ref")
    parser.add_argument("--quiet", action="store_true", help="Hide warnings")
    args = parser.parse_args()
    cities = [args.city] if args.city else list(CITIES)

    networks, issues = {}, []
    for city in cities:
        path = network_path(city)
        raw = path.read_text(encoding="utf-8")
        try:
            networks[city] = json.loads(raw)
        except json.JSONDecodeError as problem:
            issues.append(Issue("error", city, None, f"{path.name} is not valid JSON: {problem}"))
            continue
        if networks[city].get("features") is not None and dumps_network(networks[city]) != raw:
            issues.append(Issue("error", city, None, f"{path.name} isn't formatted; run python3 tools/format.py"))
    issues.extend(check_all(list(networks), networks))

    errors = [issue for issue in issues if issue.level == "error"]
    warnings = [issue for issue in issues if issue.level == "warning"]
    for issue in errors + ([] if args.quiet else warnings):
        print(issue)
    if args.base:
        changed = [item for city in networks for item in changes_since(args.base, city, networks[city])]
        print(f"\nChanged since {args.base}:" if changed else f"\nNo lines changed since {args.base}.")
        for item in changed:
            print(f"  {item}")
    lines = sum(len(network.get("features", [])) for network in networks.values())
    print(f"\n{len(networks)} cities, {lines} lines: {len(errors)} errors, {len(warnings)} warnings.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
