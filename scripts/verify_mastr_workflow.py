"""Verify real frozen registry/proposals and optional GIS exports without review writes."""
from collections import Counter
import json
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET
import argparse
import urllib.request

import geopandas as gpd
import pyogrio
from shapely.geometry import shape
from roof_pv_registry.snapshot import latest_snapshot
from roof_pv_registry.source import digest, PREFIXES
from roof_pv_review.store import Store
from roof_pv_review.matching import MatchingService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path("var"))
    parser.add_argument("--baseline", type=Path, default=Path("../lab"))
    parser.add_argument("--run", default="municipal-27320a7f3fb1")
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    registry = latest_snapshot(args.home)
    tables = {name: json.loads((registry.folder / (name + ".json")).read_text()) for name in PREFIXES}
    expected = registry.manifest["source"]["members"]
    member_paths = list((registry.folder / "members").glob("*.json"))
    assert len(member_paths) == 198
    for path in member_paths:
        record = json.loads(path.read_text())
        packed = registry.folder / "compressed" / (record["member"] + ".deflate")
        assert digest(packed) == record["compressed_sha256"]
        original = next(m for m in expected if m["name"] == record["member"])
        assert original["crc32"] == record["crc32"] and packed.stat().st_size == original["compressed_bytes"]
        for row in record["rows"]:
            assert {c.tag: c.text for c in ET.fromstring(row["_source_xml"])} == {k: v for k, v in row.items() if not k.startswith("_")}
    for name, (prefix, _, id_field) in PREFIXES.items():
        cached = [r for path in member_paths if path.name.startswith(prefix) for r in json.loads(path.read_text())["rows"]]
        assert {r[id_field]: r for r in cached} == {r[id_field]: r for r in tables[name]}
        assert len(cached) == len(tables[name])
    assert all(u["ags"] in {"12065144", "12065036"} for u in registry.data["solar_units"])
    assert all(pair["location_id"] for pair in registry.data["storage_relationships"])
    old_solar = json.loads((args.baseline / "data/processed/mastr_solar.json").read_text())
    old_storage = json.loads((args.baseline / "data/processed/mastr_storage.json").read_text())
    deltas = {}
    for name, old, key in [("solar", old_solar, "EinheitMastrNummer"), ("storage", old_storage, "EinheitMastrNummer")]:
        before = {r[key]: r for r in old}
        after = {r[key]: r for r in tables[name]}
        changes = Counter()
        for item_id in before.keys() & after.keys():
            changes.update(k for k in before[item_id].keys() | after[item_id].keys() if not k.startswith("_") and before[item_id].get(k) != after[item_id].get(k))
        deltas[name] = {"previous_count": len(before), "current_count": len(after), "added_ids": sorted(after.keys() - before.keys()),
                        "removed_ids": sorted(before.keys() - after.keys()), "changed_fields": dict(changes)}
    store = Store(args.home)
    service = MatchingService(store)
    batch = service.batch(args.run)
    source = store.snapshot(args.run)
    ids = [m for g in batch["groups"]["features"] for m in g["properties"]["member_ids"]]
    assert len(ids) == len(set(ids)) == len(source["candidates"]["features"])
    assert all(shape(g["geometry"]).is_valid for g in batch["groups"]["features"])
    assert set(batch["rankings"]["units"]) == set(registry.solar)
    assert not service.summary(args.run)["stale_groups"]
    for entry in batch["rankings"]["units"].values():
        assert entry["state"] != "verified_identity" and len(entry["alternatives"]) <= 5
    with store.connection() as db:
        pv_states = dict(db.execute("SELECT status,count(*) FROM candidates WHERE run_id=? AND active=1 GROUP BY status", (args.run,)).fetchall())
        identity_reviews = db.execute("SELECT count(*) FROM registry_reviews WHERE batch_id=?", (batch["manifest"]["batch_id"],)).fetchone()[0]
    assert pv_states == {"pending": 6355} and identity_reviews == 0
    targets = [r for r in registry.data["solar_units"] if r["cohort"] in ["active_building_under30", "active_building_exactly30"]]
    target_states = Counter(batch["rankings"]["units"][r["id"]]["state"] for r in targets)
    result = {"status": "passed", "run_id": args.run, "registry_snapshot_id": registry.manifest["snapshot_id"],
              "registry_manifest_sha256": digest(registry.folder / "manifest.json"), "registry_summary": registry.manifest["summary"],
              "source_member_count": len(member_paths), "retained_xml_and_compressed_source_checks": True,
              "snapshot_delta": deltas, "baseline_missing_location_cross_join_pairs_excluded": sum(not r.get("LokationMaStRNummer") for r in old_solar) * sum(not r.get("LokationMaStRNummer") for r in old_storage),
              "batch": batch["manifest"], "target_states_including_exactly30": dict(target_states),
              "target_units_with_tied_best_scores": sum("tied_best_alternatives" in batch["rankings"]["units"][u["id"]]["flags"] for u in targets),
              "pv_review_status": pv_states, "identity_review_count": identity_reviews,
              "identity_accuracy_measured": False, "visual_browser_verification": False}
    if args.export:
        layers = {str(name): str(kind) for name, kind in pyogrio.list_layers(args.export)}
        spatial = []
        for name, kind in layers.items():
            if kind == "None":
                continue
            frame = gpd.read_file(args.export, layer=name)
            assert frame.crs.to_epsg() == 3035 and frame.geometry.is_valid.all()
            spatial.append(name)
        with sqlite3.connect(args.export) as db:
            assert db.execute("SELECT count(*) FROM mastr_solar").fetchone()[0] == len(registry.solar)
            assert db.execute("SELECT count(*) FROM verified_mastr_pv").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM mastr_storage_relationships WHERE location_id IS NULL").fetchone()[0] == 0
        result["export"] = {"file": str(args.export), "sha256": digest(args.export), "bytes": args.export.stat().st_size, "spatial_layers_epsg3035": spatial}
    Path("docs/mastr-workflow-verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "batch"}, indent=2))


if __name__ == "__main__":
    main()
