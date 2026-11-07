"""Tests for the route tools. Run with: python3 -m unittest discover -s tools"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from routeformat import CITIES, CORRECTED_STATUS, ROOT, dumps_network, load_network, network_path, path_km, save_network  # noqa: E402
from serve import Rejected, apply_line, make_server  # noqa: E402
from validate import changes_since, check_all, check_feature, check_network  # noqa: E402

LOOP = [[122.95, 10.70], [122.96, 10.70], [122.96, 10.71], [122.95, 10.70]]


def line(line_id="bacolod-test", **overrides):
    properties = {"id": line_id, "number": "1", "name": "A – B", "color": "#2563eb", "status": "inferred_from_lptrp_anchors",
                  "distance_km": round(path_km(LOOP), 2), "anchors": [{"id": "a", "name": "Plaza", "lng": 122.95, "lat": 10.70}]}
    properties.update(overrides.pop("properties", {}))
    return {"type": "Feature", "properties": properties,
            "geometry": {"type": "LineString", "coordinates": overrides.pop("coordinates", copy.deepcopy(LOOP))}}


def messages(issues, level="error"):
    return [issue.message for issue in issues if issue.level == level]


class FormatTests(unittest.TestCase):
    def test_round_trip_keeps_data_and_is_stable(self):
        network = {"type": "FeatureCollection", "features": [line(properties={"name": "Ñ – Ét"}), line("two")]}
        text = dumps_network(network)
        self.assertEqual(json.loads(text), network)
        self.assertEqual(dumps_network(json.loads(text)), text)
        self.assertIn("Ñ – Ét", text)

    def test_moving_one_point_changes_one_line(self):
        network = {"type": "FeatureCollection", "features": [line()]}
        before = dumps_network(network).splitlines()
        network["features"][0]["geometry"]["coordinates"][1] = [122.961, 10.70]
        after = dumps_network(network).splitlines()
        self.assertEqual(len(before), len(after))
        self.assertEqual(sum(a != b for a, b in zip(before, after)), 1)

    def test_real_files_are_canonical_and_valid(self):
        networks = {city: load_network(city) for city in CITIES}
        for city, network in networks.items():
            self.assertEqual(dumps_network(network), network_path(city).read_text(encoding="utf-8"), city)
        self.assertEqual(messages(check_all(list(CITIES), networks)), [])


class ValidateTests(unittest.TestCase):
    def test_good_line_passes(self):
        self.assertEqual(check_feature("bacolod", line()), [])

    def test_swapped_lat_lng_is_an_error(self):
        swapped = [[lat, lng] for lng, lat in LOOP]
        self.assertTrue(any("outside the Philippines" in m for m in messages(check_feature("bacolod", line(coordinates=swapped)))))

    def test_wrong_length_is_an_error(self):
        self.assertTrue(any("distance_km says" in m for m in messages(check_feature("bacolod", line(properties={"distance_km": 99})))))

    def test_missing_property_and_bad_values(self):
        found = messages(check_feature("bacolod", line(properties={"name": "", "color": "blue", "status": "guess"})))
        self.assertTrue(any("`name`" in m for m in found))
        self.assertTrue(any("color" in m for m in found))
        self.assertTrue(any("status" in m for m in found))

    def test_single_point_is_an_error(self):
        self.assertTrue(messages(check_feature("bacolod", line(coordinates=[[122.95, 10.70]]))))

    def test_open_line_and_jump_and_far_landmark_are_warnings_only(self):
        far = line(coordinates=[[122.95, 10.70], [123.00, 10.75]],
                   properties={"distance_km": round(path_km([[122.95, 10.70], [123.00, 10.75]]), 2),
                               "anchors": [{"id": "x", "name": "Far", "lng": 122.5, "lat": 10.2}]})
        issues = check_feature("bacolod", far)
        self.assertEqual(messages(issues), [])
        warnings = " ".join(messages(issues, "warning"))
        for text in ("full out-and-back", "straight jump", "`Far`"):
            self.assertIn(text, warnings)

    def test_endpoint_anchors_are_ignored_like_the_app(self):
        ignored = line(properties={"anchors": [{"id": "s", "name": "1 route endpoint", "lng": 122.5, "lat": 10.2}]})
        self.assertEqual(check_feature("bacolod", ignored), [])

    def test_duplicate_id_across_cities_is_an_error(self):
        networks = {"bacolod": {"type": "FeatureCollection", "features": [line("same")]},
                    "naga": {"type": "FeatureCollection", "features": [line("same")]}}
        self.assertTrue(any("unique across all cities" in m for m in messages(check_all(["bacolod", "naga"], networks))))

    def test_duplicate_number_is_a_warning(self):
        network = {"type": "FeatureCollection", "features": [line("one"), line("two")]}
        self.assertTrue(messages(check_network("bacolod", network), "warning"))


class ApplyLineTests(unittest.TestCase):
    def network(self):
        return {"type": "FeatureCollection", "features": [line(properties={"attribution": "© OSM", "planned_length_km": 9})]}

    def test_correction_marks_status_recomputes_length_and_keeps_other_properties(self):
        network = self.network()
        edit = copy.deepcopy(network["features"][0])
        edit["geometry"]["coordinates"][1] = [122.97, 10.70]
        stored = apply_line("bacolod", network, edit, False, set())
        self.assertEqual(stored["properties"]["status"], CORRECTED_STATUS)
        self.assertEqual(stored["properties"]["distance_km"], round(path_km(stored["geometry"]["coordinates"]), 2))
        self.assertEqual(stored["properties"]["attribution"], "© OSM")
        self.assertEqual(stored["properties"]["planned_length_km"], 9)
        self.assertIs(network["features"][0], stored)

    def test_client_cannot_set_status_or_length(self):
        network = self.network()
        edit = copy.deepcopy(network["features"][0])
        edit["properties"].update({"status": "osm_route_relation", "distance_km": 1, "name": "A – C", "attribution": ""})
        stored = apply_line("bacolod", network, edit, False, set())
        self.assertEqual(stored["properties"]["status"], CORRECTED_STATUS)
        self.assertEqual(stored["properties"]["attribution"], "© OSM")
        self.assertEqual(stored["properties"]["distance_km"], line()["properties"]["distance_km"])

    def test_no_change_leaves_line_untouched(self):
        network = self.network()
        before = copy.deepcopy(network)
        apply_line("bacolod", network, copy.deepcopy(network["features"][0]), False, set())
        self.assertEqual(network, before)

    def test_unknown_id_is_rejected_rather_than_created(self):
        with self.assertRaises(Rejected):
            apply_line("bacolod", self.network(), line("not-there"), False, set())

    def test_new_line_gets_a_free_slug(self):
        network = self.network()
        new = line(None, properties={"name": "Mandalagan – Libertad", "number": "9"})
        stored = apply_line("bacolod", network, new, True, {"bacolod-mandalagan-libertad"})
        self.assertEqual(stored["properties"]["id"], "bacolod-mandalagan-libertad-2")
        self.assertEqual(stored["properties"]["status"], CORRECTED_STATUS)
        self.assertEqual(len(network["features"]), 2)

    def test_new_landmark_gets_an_id_and_a_tidy_name(self):
        network = self.network()
        edit = copy.deepcopy(network["features"][0])
        edit["properties"]["anchors"].append({"name": "  SM   City ", "lng": 122.951235, "lat": 10.7})
        stored = apply_line("bacolod", network, edit, False, set())
        self.assertEqual(stored["properties"]["anchors"][-1], {"id": "sm_city", "name": "SM City", "lng": 122.951235, "lat": 10.7})

    def test_saving_never_rewrites_points_or_landmarks_nobody_touched(self):
        precise = [[122.95000123456, 10.70000987654], *copy.deepcopy(LOOP[1:]), [122.95, 10.7]]
        anchor = {"id": "a", "name": "Plaza", "lng": 122.95000123456, "lat": 10.70000987654}
        network = {"type": "FeatureCollection", "features": [line(coordinates=precise, properties={"anchors": [anchor]})]}
        edit = copy.deepcopy(network["features"][0])
        edit["geometry"]["coordinates"][1] = [122.961, 10.70]
        stored = apply_line("bacolod", network, edit, False, set())
        self.assertEqual(stored["geometry"]["coordinates"][0], precise[0])
        self.assertEqual(stored["properties"]["anchors"], [anchor])

    def test_non_numeric_coordinates_are_refused(self):
        edit = line(coordinates=[["122", 10.7], [122.96, 10.7]])
        with self.assertRaises(ValueError):
            apply_line("bacolod", self.network(), edit, True, set())


class ServerTests(unittest.TestCase):
    """Runs the real server against a throwaway git checkout."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        for city in CITIES:
            features = [line(f"{city}-1")] if city == "bacolod" else []
            save_network(city, {"type": "FeatureCollection", "features": features}, cls.root)
        for command in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "seed"]):
            subprocess.run(["git", *command], cwd=cls.root, check=True)
        cls.server = make_server(cls.root, 0)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.directory.cleanup()

    def setUp(self):
        subprocess.run(["git", "checkout", "-q", "--", "."], cwd=self.root, check=True)

    def post(self, path, body, headers=None):
        request = urllib.request.Request(self.url + path, json.dumps(body).encode(), {"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as problem:
            return problem.code, json.loads(problem.read())

    def get(self, path):
        with urllib.request.urlopen(self.url + path) as response:
            return json.loads(response.read())

    def edited(self):
        feature = copy.deepcopy(self.get("/api/network/bacolod")["features"][0])
        feature["geometry"]["coordinates"][1] = [122.97, 10.70]
        return feature

    def test_save_writes_a_canonical_file_and_shows_as_changed(self):
        status, _ = self.post("/api/save/bacolod", {"feature": self.edited()})
        self.assertEqual(status, 200)
        stored = load_network("bacolod", self.root)
        self.assertEqual(network_path("bacolod", self.root).read_text(encoding="utf-8"), dumps_network(stored))
        self.assertEqual(self.get("/api/network/bacolod")["changed"], ["bacolod-1"])
        items = self.get("/api/changes")["items"]
        self.assertEqual([(item["id"], item["kind"]) for item in items], [("bacolod-1", "changed")])

    def test_save_refuses_a_line_with_errors_and_leaves_the_file_alone(self):
        before = network_path("bacolod", self.root).read_bytes()
        bad = self.edited()
        bad["geometry"]["coordinates"][1] = [10.7, 122.97]
        status, payload = self.post("/api/save/bacolod", {"feature": bad})
        self.assertEqual(status, 409)
        self.assertIn("outside the Philippines", payload["error"])
        self.assertEqual(network_path("bacolod", self.root).read_bytes(), before)

    def test_check_reports_without_writing(self):
        before = network_path("bacolod", self.root).read_bytes()
        status, payload = self.post("/api/check/bacolod", {"feature": self.edited()})
        self.assertEqual(status, 200)
        self.assertEqual([issue["level"] for issue in payload["issues"]], ["warning"])  # the edit draws a long straight jump
        self.assertEqual(network_path("bacolod", self.root).read_bytes(), before)

    def test_revert_restores_the_committed_line(self):
        before = network_path("bacolod", self.root).read_bytes()
        self.post("/api/save/bacolod", {"feature": self.edited()})
        self.assertEqual(self.post("/api/revert/bacolod", {"id": "bacolod-1"})[0], 200)
        self.assertEqual(network_path("bacolod", self.root).read_bytes(), before)

    def test_delete_then_revert_restores(self):
        before = network_path("bacolod", self.root).read_bytes()
        self.assertEqual(self.post("/api/delete/bacolod", {"id": "bacolod-1"})[0], 200)
        self.assertEqual(load_network("bacolod", self.root)["features"], [])
        self.assertEqual(self.get("/api/changes")["items"][0]["kind"], "removed")
        self.post("/api/revert/bacolod", {"id": "bacolod-1"})
        self.assertEqual(network_path("bacolod", self.root).read_bytes(), before)

    def test_other_origins_and_hosts_are_refused(self):
        before = network_path("bacolod", self.root).read_bytes()
        self.assertEqual(self.post("/api/save/bacolod", {"feature": self.edited()}, {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.post("/api/save/bacolod", {"feature": self.edited()}, {"Host": "evil.example"})[0], 403)
        request = urllib.request.Request(self.url + "/api/save/bacolod", b"{}", {"Content-Type": "text/plain"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(network_path("bacolod", self.root).read_bytes(), before)

    def test_unknown_city_cannot_reach_other_paths(self):
        self.assertEqual(self.post("/api/save/..%2f..%2fetc", {"feature": {}})[0], 404)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/api/network/..%2fsecrets")
        self.assertEqual(caught.exception.code, 404)

    def test_malformed_body_is_a_400_not_a_crash(self):
        self.assertEqual(self.post("/api/save/bacolod", {"nope": 1})[0], 400)

    def test_changes_since_describes_the_edit(self):
        self.post("/api/save/bacolod", {"feature": self.edited()})
        summary = changes_since("HEAD", "bacolod", load_network("bacolod", self.root), self.root)
        self.assertEqual(len(summary), 1)
        self.assertIn("bacolod/bacolod-1: changed", summary[0])


class RootTests(unittest.TestCase):
    def test_tools_default_to_this_checkout(self):
        self.assertEqual(ROOT, Path(__file__).resolve().parent.parent)


if __name__ == "__main__":
    unittest.main()
