#!/usr/bin/env python3
"""Local route editor. Standard library only.

    python3 tools/serve.py

Opens a map where you can correct a line, test a ride on it, and save it to the
route file in this checkout. Nothing leaves your machine until you push a branch.
It listens on 127.0.0.1 only and refuses requests from any other site, because it
can write to your files.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from routeformat import CITIES, CORRECTED_STATUS, ROOT, load_network, network_path, path_km, save_network
from validate import changes_since, check_all, check_feature, check_network

EDITOR_PAGE = Path(__file__).resolve().parent / "editor.html"
EDITABLE = ("number", "name", "color", "mode", "source_url", "anchors", "return_anchors")
NEW_LINE_COLOR = "#7c3aed"  # the app's colour for community lines
MAX_BODY_BYTES = 8_000_000


class Rejected(ValueError):
    """The request can't be applied; the message is shown to the person editing."""


def slug(text: str, separator: str = "-") -> str:
    return re.sub(r"[^a-z0-9]+", separator, text.lower()).strip(separator)


def _number(value: object) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{value!r} is not a coordinate")
    return value


def _clean_anchor(item: dict) -> dict:
    """Numbers stay exactly as sent: rounding here would rewrite landmarks nobody touched."""
    name = " ".join(str(item.get("name", "")).split())
    return {"id": item.get("id") or slug(name, "_"), "name": name,
            "lng": _number(item["lng"]), "lat": _number(item["lat"]),
            **{key: value for key, value in item.items() if key not in ("id", "name", "lng", "lat")}}


def apply_line(city: str, network: dict, submitted: dict, is_new: bool, taken_ids: set[str]) -> dict:
    """Merge a submitted line into a network and return the stored feature.

    The server owns `id`, `status`, and `distance_km`: an id is permanent, a changed
    line is always marked as corrected, and the length always matches the drawing.
    """
    incoming = submitted.get("properties") or {}
    # Kept exactly as sent, for the same reason. The page rounds the points it creates.
    coordinates = [[_number(point[0]), _number(point[1])]
                   for point in (submitted.get("geometry") or {}).get("coordinates", [])]
    existing = next((feature for feature in network["features"]
                     if feature["properties"]["id"] == incoming.get("id")), None)
    if is_new:
        name = " ".join(str(incoming.get("name", "")).split())
        if not name:
            raise Rejected("Give the new line a name first.")
        base = slug(f"{city} {name}")
        line_id, count = base, 2
        while line_id in taken_ids:
            line_id, count = f"{base}-{count}", count + 1
        properties = {"id": line_id, "color": NEW_LINE_COLOR, "status": CORRECTED_STATUS, "anchors": []}
    elif existing is None:
        raise Rejected(f"Line `{incoming.get('id')}` isn't in this city's file. Reload the page.")
    else:
        properties = dict(existing["properties"])

    for key in EDITABLE:
        if key in incoming:
            properties[key] = incoming[key]
    if isinstance(properties.get("name"), str):
        properties["name"] = " ".join(properties["name"].split())
    for key in ("anchors", "return_anchors"):
        if key in incoming:
            properties[key] = [_clean_anchor(item) for item in incoming[key]]
    if is_new and not properties.get("source_url"):
        properties.pop("source_url", None)

    geometry = {"type": "LineString", "coordinates": coordinates}
    if existing is not None:
        unchanged = (coordinates == existing["geometry"]["coordinates"]
                     and all(properties.get(key) == existing["properties"].get(key) for key in EDITABLE))
        if unchanged:
            return existing
        if coordinates != existing["geometry"]["coordinates"]:
            properties["distance_km"] = round(path_km(coordinates), 2)
    else:
        properties["distance_km"] = round(path_km(coordinates), 2)
    properties["status"] = CORRECTED_STATUS

    feature = {"type": "Feature", "properties": properties, "geometry": geometry}
    if existing is not None:
        network["features"][network["features"].index(existing)] = feature
    else:
        network["features"].append(feature)
    return feature


class Editor:
    """Reads and writes route files in one checkout."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.lock = threading.Lock()

    def head(self, city: str) -> dict[str, dict]:
        """The committed version of each line, which edits are compared against."""
        shown = subprocess.run(["git", "show", f"HEAD:{network_path(city).name}"], cwd=self.root,
                               capture_output=True, text=True, encoding="utf-8")
        if shown.returncode != 0:
            return {}
        return {feature["properties"]["id"]: feature for feature in json.loads(shown.stdout)["features"]}

    def networks(self) -> dict[str, dict]:
        return {city: load_network(city, self.root) for city in CITIES}

    def cities(self) -> list[dict]:
        return [{"id": city, "name": CITIES[city][0], "lines": len(network["features"])}
                for city, network in self.networks().items()]

    def network(self, city: str) -> dict:
        network, head = load_network(city, self.root), self.head(city)
        return {"features": network["features"], "committed": head,
                "changed": [feature["properties"]["id"] for feature in network["features"]
                            if head.get(feature["properties"]["id"]) != feature]}

    def _issues(self, city: str, network: dict, feature: dict) -> list[dict]:
        line = feature["properties"]["id"]
        issues = check_feature(city, feature)
        issues += [issue for issue in check_network(city, {"type": "FeatureCollection", "features": network["features"]})
                   if issue.line == line and issue not in issues]
        return [{"level": issue.level, "message": issue.message} for issue in issues]

    def check(self, city: str, body: dict) -> dict:
        """Apply a line to a copy of the network and report what the validator says."""
        network = load_network(city, self.root)
        taken = {feature["properties"]["id"] for other in self.networks().values() for feature in other["features"]}
        if len((body["feature"].get("geometry") or {}).get("coordinates", [])) < 2:
            return {"issues": [{"level": "error", "message": "Draw at least two points."}], "distance_km": 0}
        try:
            feature = apply_line(city, network, body["feature"], bool(body.get("new")), taken)
        except Rejected as problem:
            return {"issues": [{"level": "error", "message": str(problem)}], "distance_km": 0}
        return {"issues": self._issues(city, network, feature), "distance_km": feature["properties"].get("distance_km")}

    def save(self, city: str, body: dict) -> dict:
        with self.lock:
            networks = self.networks()
            network = networks[city]
            taken = {feature["properties"]["id"] for other in networks.values() for feature in other["features"]}
            feature = apply_line(city, network, body["feature"], bool(body.get("new")), taken)
            problems = [issue for issue in check_all(list(CITIES), networks) if issue.level == "error"
                        and issue.city == city and issue.line == feature["properties"]["id"]]
            if problems:
                raise Rejected("; ".join(issue.message for issue in problems))
            save_network(city, network, self.root)
            return {"feature": feature, "issues": self._issues(city, network, feature)}

    def delete(self, city: str, body: dict) -> dict:
        with self.lock:
            network = load_network(city, self.root)
            kept = [feature for feature in network["features"] if feature["properties"]["id"] != body.get("id")]
            if len(kept) == len(network["features"]):
                raise Rejected("That line isn't in this city's file.")
            save_network(city, {**network, "features": kept}, self.root)
            return {"deleted": body["id"]}

    def revert(self, city: str, body: dict) -> dict:
        """Put one line back as committed, in its committed place. A line added since is removed."""
        with self.lock:
            network, head = load_network(city, self.root), self.head(city)
            line = body.get("id")
            current = {feature["properties"]["id"]: feature for feature in network["features"]}
            if line in head:
                current[line] = head[line]
            elif line in current:
                del current[line]
            else:
                raise Rejected("That line has nothing to undo.")
            ordered = [current.pop(key) for key in head if key in current]
            ordered += list(current.values())
            save_network(city, {**network, "features": ordered}, self.root)
            return {"reverted": line}

    def changes(self) -> dict:
        items = []
        for city, network in self.networks().items():
            head = self.head(city)
            current = {feature["properties"]["id"] for feature in network["features"]}
            items += [{"city": city, "id": feature["properties"]["id"], "name": feature["properties"]["name"], "kind":
                       "added" if feature["properties"]["id"] not in head else "changed"}
                      for feature in network["features"] if head.get(feature["properties"]["id"]) != feature]
            items += [{"city": city, "id": line, "name": old["properties"]["name"], "kind": "removed"}
                      for line, old in head.items() if line not in current]
        summary = [item for city, network in self.networks().items()
                   for item in changes_since("HEAD", city, network, self.root)]
        return {"items": items, "summary": summary}


def make_handler(editor: Editor, port: int) -> type[BaseHTTPRequestHandler]:
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    actions = {"check": editor.check, "save": editor.save, "delete": editor.delete, "revert": editor.revert}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: object) -> None:
            pass

        def _send(self, status: int, payload: object, content_type: str = "application/json; charset=utf-8") -> None:
            body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _host_allowed(self) -> bool:
            # Blocks DNS rebinding: another site's name pointed at this port.
            return self.headers.get("Host") in allowed_hosts

        def do_GET(self) -> None:
            if not self._host_allowed():
                return self._send(403, {"error": "Open the editor at its own 127.0.0.1 address."})
            parts = [unquote(part) for part in urlparse(self.path).path.split("/") if part]
            if not parts:
                return self._send(200, EDITOR_PAGE.read_bytes(), "text/html; charset=utf-8")
            if parts == ["api", "cities"]:
                return self._send(200, editor.cities())
            if parts == ["api", "changes"]:
                return self._send(200, editor.changes())
            if len(parts) == 3 and parts[:2] == ["api", "network"] and parts[2] in CITIES:
                return self._send(200, editor.network(parts[2]))
            return self._send(404, {"error": "Not found"})

        def do_POST(self) -> None:
            origin = self.headers.get("Origin")
            if (not self._host_allowed()
                    or (origin is not None and urlparse(origin).netloc != self.headers.get("Host"))
                    or not (self.headers.get("Content-Type") or "").startswith("application/json")):
                return self._send(403, {"error": "Requests must come from the editor page."})
            parts = [unquote(part) for part in urlparse(self.path).path.split("/") if part]
            if len(parts) != 3 or parts[0] != "api" or parts[1] not in actions or parts[2] not in CITIES:
                return self._send(404, {"error": "Not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                return self._send(413, {"error": "That line is too large."})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                return self._send(200, actions[parts[1]](parts[2], body))
            except Rejected as problem:
                return self._send(409, {"error": str(problem)})
            except (ValueError, KeyError, TypeError, IndexError) as problem:
                return self._send(400, {"error": f"Couldn't read that line: {problem}"})

    return Handler


def make_server(root: Path, port: int) -> ThreadingHTTPServer:
    editor = Editor(root)
    server = ThreadingHTTPServer(("127.0.0.1", port), None)  # type: ignore[arg-type]
    server.RequestHandlerClass = make_handler(editor, server.server_address[1])
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", type=Path, default=ROOT, help="Checkout to edit (default: this one)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open a browser tab")
    args = parser.parse_args()
    server = make_server(args.root.resolve(), args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Route editor: {url}\nEditing files in {args.root.resolve()}\nPress Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
