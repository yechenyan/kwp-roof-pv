"""Measure local HTTP payload/latency changes, not browser rendering performance."""
import argparse
import gzip
import json
from pathlib import Path
import statistics
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8780")
    parser.add_argument("--run", default="municipal-27320a7f3fb1")
    parser.add_argument("--home", type=Path, default=Path("var"))
    parser.add_argument("--output", type=Path, default=Path("docs/web-performance.json"))
    args = parser.parse_args()
    base = args.base + "/api/runs/" + args.run
    results = {}
    for name, suffix in [("legacy_full_snapshot", ""), ("overview", "/workspace"), ("viewport", "/view?bbox=13.2799,52.6725,13.2827,52.6741&zoom=18&layers=buildings")]:
        durations = []
        for _ in range(3):
            start = time.perf_counter()
            request = urllib.request.Request(base + suffix, headers={"Accept-Encoding": "gzip"})
            with urllib.request.urlopen(request, timeout=120) as response:
                wire = response.read()
                body = gzip.decompress(wire) if response.headers.get("content-encoding") == "gzip" else wire
                json.loads(body)
            durations.append(round((time.perf_counter() - start) * 1000, 2))
        results[name] = {"wire_bytes": len(wire), "json_bytes": len(body), "samples_ms": durations, "median_ms": statistics.median(durations)}
    folder = args.home / "runs" / args.run
    previous_geometry_bytes = results["legacy_full_snapshot"]["json_bytes"] + sum((folder / name).stat().st_size for name in ["rgb.geojson", "rgbi.geojson", "buildings.geojson"])
    result = {"run_id": args.run, "measurements": results, "previous_initial_geometry_bytes": previous_geometry_bytes,
              "new_initial_geometry_bytes": results["overview"]["json_bytes"],
              "initial_geometry_reduction_percent": round(100 * (1 - results["overview"]["json_bytes"] / previous_geometry_bytes), 2),
              "conditions": "Local HTTP; 3 sequential samples per endpoint; gzip enabled; browser image transfer and rendering excluded; first request may include cache construction.",
              "visual_browser_verification": False}
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
