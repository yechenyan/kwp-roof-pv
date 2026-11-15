"""Normalize public records without inferring private addresses or coordinates."""
from collections import Counter, defaultdict
from datetime import date
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from .source import atomic_json, digest


def numeric(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def normalize_solar(row, catalog):
    power, modules = numeric(row.get("Bruttoleistung")), numeric(row.get("AnzahlModule"))
    lon, lat = numeric(row.get("Laengengrad")), numeric(row.get("Breitengrad"))
    flags = []
    commissioned = row.get("Inbetriebnahmedatum")
    if commissioned:
        try:
            date.fromisoformat(commissioned)
        except ValueError:
            commissioned = None
            flags.append("invalid_commission_date")
    if power is None or power <= 0:
        flags.append("invalid_power")
    names = {"12065144": "Hohen Neuendorf", "12065036": "Birkenwerder"}
    municipality = names.get(row.get("Gemeindeschluessel"), row.get("Gemeinde"))
    if row.get("Gemeinde") != municipality:
        flags.append("municipality_name_ags_disagreement")
    if lon is not None and lat is not None and not (-180 <= lon <= 180 and -90 <= lat <= 90):
        flags.append("invalid_coordinate")
        lon = lat = None
    if (lon is None) != (lat is None):
        flags.append("partial_coordinate")
        lon = lat = None
    if modules is not None and (modules <= 0 or power is not None and not .1 <= power / modules <= .8):
        flags.append("module_count_power_review")
    if not commissioned:
        flags.append("missing_commission_date")
    active_building = row.get("ArtDerSolaranlage") == "853" and row.get("EinheitBetriebsstatus") == "35"
    cohort = "context"
    if active_building and power is not None and 0 < power < 30 and lon is None:
        cohort = "active_building_under30"
    elif active_building and power == 30 and lon is None:
        cohort = "active_building_exactly30"
    elif lon is not None:
        cohort = "coordinate_context"
    fields = ["ArtDerSolaranlage", "EinheitBetriebsstatus", "Hauptausrichtung", "Nebenausrichtung", "HauptausrichtungNeigungswinkel", "NebenausrichtungNeigungswinkel"]
    return {"id": row["EinheitMastrNummer"], "ags": row.get("Gemeindeschluessel"), "municipality": municipality,
            "postcode": row.get("Postleitzahl"), "location_id": row.get("LokationMaStRNummer"), "power_kw": power,
            "module_count": modules, "commissioned": commissioned, "longitude": lon, "latitude": lat,
            "cohort": cohort, "flags": flags, "decoded": {key: catalog.get(row.get(key), row.get(key)) for key in fields},
            "raw": row}


def make_relationships(tables):
    locations = {r["MastrNummer"]: r for r in tables["locations"]}
    systems = {r["MaStRNummer"]: r for r in tables["systems"]}
    by_location = defaultdict(list)
    for row in tables["storage"]:
        if row.get("LokationMaStRNummer"):
            by_location[row["LokationMaStRNummer"]].append(row)
    pairs = []
    for solar in tables["solar"]:
        loc = solar.get("LokationMaStRNummer")
        reverse = set(re.findall(r"SEE\d+", locations.get(loc, {}).get("VerknuepfteEinheitenMaStRNummern") or ""))
        for storage in by_location.get(loc, []):
            system = systems.get(storage.get("SpeMastrNummer"), {})
            both = {solar["EinheitMastrNummer"], storage["EinheitMastrNummer"]} <= reverse
            system_reverse = storage["EinheitMastrNummer"] in set(re.findall(r"SEE\d+", system.get("VerknuepfteEinheitenMaStRNummern") or ""))
            pairs.append({"solar_id": solar["EinheitMastrNummer"], "storage_id": storage["EinheitMastrNummer"],
                          "location_id": loc, "system_id": storage.get("SpeMastrNummer"),
                          "relationship_status": "supported" if both and system_reverse else "source_conflict",
                          "location_lists_both": both, "system_lists_storage": system_reverse,
                          "net_power_kw": numeric(storage.get("Nettonennleistung")),
                          "usable_capacity_kwh": numeric(system.get("NutzbareSpeicherkapazitaet")),
                          "storage_status": storage.get("EinheitBetriebsstatus"), "exclusive_allocation": False})
    return pairs


def normalize(folder):
    folder = Path(folder)
    source = json.loads((folder / "source.json").read_text())
    extraction = json.loads((folder / "extraction.json").read_text())
    if extraction["status"] != "completed":
        raise ValueError("Only complete registry extractions may be published.")
    if extraction["source_sha256"] != digest(folder / "source.json"):
        raise ValueError("Registry source identity changed.")
    for name, expected in extraction.get("member_artifacts", {}).items():
        if Path(name).name != name or digest(folder / "members" / name) != expected:
            raise ValueError("A retained registry shard changed.")
    tables = {name: json.loads((folder / (name + ".json")).read_text()) for name in ["solar", "storage", "locations", "systems", "catalog"]}
    catalog = {row["Id"]: row["Wert"] for row in tables["catalog"]}
    solar = [normalize_solar(row, catalog) for row in tables["solar"]]
    relationships = make_relationships(tables)
    normalized = {"solar_units": solar, "storage_units": tables["storage"], "storage_systems": tables["systems"],
                  "locations": tables["locations"], "storage_relationships": relationships, "catalog": catalog}
    artifacts = {name + ".json": digest(folder / (name + ".json")) for name in tables}
    artifacts["source.json"] = digest(folder / "source.json")
    artifacts["extraction.json"] = digest(folder / "extraction.json")
    normalized_file = folder / "normalized.json"
    if (folder / "manifest.json").exists():
        existing = RegistrySnapshot(folder)
        if existing.manifest["input_artifacts"] != artifacts:
            raise ValueError("A completed registry snapshot is immutable.")
        return existing.manifest
    atomic_json(normalized_file, normalized)
    database = folder / ".registry.sqlite3"
    database.unlink(missing_ok=True)
    with sqlite3.connect(database) as db:
        for name, key, rows in [("solar_units", "id", solar), ("storage_units", "EinheitMastrNummer", tables["storage"]),
                                ("storage_systems", "MaStRNummer", tables["systems"]), ("locations", "MastrNummer", tables["locations"])]:
            db.execute(f"CREATE TABLE {name}(id TEXT PRIMARY KEY, row_json TEXT NOT NULL)")
            db.executemany(f"INSERT INTO {name} VALUES(?,?)", [(row[key], json.dumps(row, ensure_ascii=False)) for row in rows])
        db.execute("CREATE TABLE storage_relationships(solar_id TEXT,storage_id TEXT,row_json TEXT,PRIMARY KEY(solar_id,storage_id))")
        db.executemany("INSERT INTO storage_relationships VALUES(?,?,?)", [(r["solar_id"], r["storage_id"], json.dumps(r)) for r in relationships])
    os.replace(database, folder / "registry.sqlite3")
    supported = [r for r in relationships if r["relationship_status"] == "supported"]
    summary = {"solar": len(solar), "storage_units": len(tables["storage"]), "storage_systems": len(tables["systems"]),
               "locations": len(tables["locations"]), "cohorts": dict(Counter(r["cohort"] for r in solar)),
               "municipalities": dict(Counter(r["municipality"] for r in solar)), "supported_storage_pairs": len(supported),
               "unique_supported_storage_units": len({r["storage_id"] for r in supported}),
               "conflicting_storage_pairs": len(relationships) - len(supported)}
    manifest = {"schema_version": 1, "snapshot_id": folder.name, "status": "completed", "source": source,
                "snapshot_date": re.search(r"_(\d{4})(\d{2})(\d{2})_", source["url"]).expand(r"\1-\2-\3"),
                "summary": summary, "input_artifacts": artifacts,
                "artifacts": {"normalized.json": digest(normalized_file), "registry.sqlite3": digest(folder / "registry.sqlite3")},
                "attribution": "Bundesnetzagentur, Marktstammdatenregister, dl-de/by-2-0",
                "limitations": ["Public location suppression is preserved.", "Storage associations are location relationships, not exclusive PV allocations.",
                                "Retained XML fragments are parsed serializations; compressed source members preserve byte-level provenance."]}
    atomic_json(folder / "manifest.json", manifest)
    return manifest


class RegistrySnapshot:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.manifest = json.loads((self.folder / "manifest.json").read_text())
        for name, expected in {**self.manifest["input_artifacts"], **self.manifest["artifacts"]}.items():
            if Path(name).name != name or digest(self.folder / name) != expected:
                raise ValueError("Registry artifact failed integrity verification.")
        self.data = json.loads((self.folder / "normalized.json").read_text())
        self.solar = {r["id"]: r for r in self.data["solar_units"]}


def latest_snapshot(home):
    paths = sorted((Path(home) / "registry").glob("*/manifest.json"))
    if not paths:
        raise KeyError("No completed MaStR snapshot. Run roof-pv-registry acquire first.")
    return RegistrySnapshot(paths[-1].parent)
