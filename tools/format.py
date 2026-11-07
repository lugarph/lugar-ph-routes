#!/usr/bin/env python3
"""Rewrite every route file in the canonical form. Add --check to only report."""

from __future__ import annotations

import argparse
import json
import sys

from routeformat import CITIES, dumps_network, network_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report files that differ; change nothing")
    args = parser.parse_args()
    stale = []
    for city in CITIES:
        path = network_path(city)
        raw = path.read_text(encoding="utf-8")
        network = json.loads(raw)
        text = dumps_network(network)
        if json.loads(text) != network:
            print(f"{path.name}: formatting would change the data; not writing", file=sys.stderr)
            return 2
        if text != raw:
            stale.append(path.name)
            if not args.check:
                path.write_text(text, encoding="utf-8")
    verb = "Not formatted" if args.check else "Formatted"
    for name in stale:
        print(f"{verb}: {name}")
    if not stale:
        print("All route files are already formatted.")
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
