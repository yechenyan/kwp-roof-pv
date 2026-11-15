"""Registry selection, integrity and shared-storage semantics."""
import json
import hashlib
from pathlib import Path
import zipfile
import zlib
import pytest
from roof_pv_registry.source import extract_xml, atomic_json, digest
from roof_pv_registry.snapshot import normalize_solar, make_relationships, normalize, RegistrySnapshot


def test_ags_selection_is_independent_of_municipality_spelling_and_checks_crc():
    xml = ('<?xml version="1.0" encoding="utf-16"?><Data><EinheitSolar><EinheitMastrNummer>SEE1</EinheitMastrNummer><Gemeindeschluessel>12065144</Gemeindeschluessel><Gemeinde>Different spelling</Gemeinde></EinheitSolar><EinheitSolar><EinheitMastrNummer>SEE2</EinheitMastrNummer><Gemeindeschluessel>00000000</Gemeindeschluessel><Gemeinde>Hohen Neuendorf</Gemeinde></EinheitSolar></Data>').encode("utf-16")
    compressor = zlib.compressobj(wbits=-15)
    packed = compressor.compress(xml) + compressor.flush()
    info = zipfile.ZipInfo("EinheitenSolar_1.xml")
    info.CRC, info.file_size = zlib.crc32(xml), len(xml)
    rows, total = extract_xml(packed, info, "EinheitSolar", lambda r: r.get("Gemeindeschluessel") == "12065144")
    assert total == 2 and [r["EinheitMastrNummer"] for r in rows] == ["SEE1"]
    assert "Different spelling" in rows[0]["_source_xml"]
    info.CRC += 1
    with pytest.raises(ValueError, match="CRC"):
        extract_xml(packed, info, "EinheitSolar", lambda r: True)


def test_exactly_thirty_is_a_separate_cohort_and_suspect_modules_remain_flagged():
    row = {"EinheitMastrNummer": "SEE1", "ArtDerSolaranlage": "853", "EinheitBetriebsstatus": "35", "Bruttoleistung": "30", "AnzahlModule": "1"}
    unit = normalize_solar(row, {})
    assert unit["cohort"] == "active_building_exactly30"
    assert unit["longitude"] is None and "module_count_power_review" in unit["flags"]
    row["Bruttoleistung"] = "29.9"
    assert normalize_solar(row, {})["cohort"] == "active_building_under30"


def test_storage_requires_reverse_links_and_does_not_allocate_shared_capacity():
    tables = {"solar": [{"EinheitMastrNummer": "SEE1", "LokationMaStRNummer": "SEL1"}, {"EinheitMastrNummer": "SEE2", "LokationMaStRNummer": "SEL1"}, {"EinheitMastrNummer": "SEE3", "LokationMaStRNummer": "SEL1"}],
              "storage": [{"EinheitMastrNummer": "SEE9", "LokationMaStRNummer": "SEL1", "SpeMastrNummer": "SSE1", "Nettonennleistung": "5"}],
              "locations": [{"MastrNummer": "SEL1", "VerknuepfteEinheitenMaStRNummern": "SEE1,SEE2,SEE9"}],
              "systems": [{"MaStRNummer": "SSE1", "VerknuepfteEinheitenMaStRNummern": "SEE9", "NutzbareSpeicherkapazitaet": "10"}]}
    pairs = make_relationships(tables)
    assert [p["relationship_status"] for p in pairs] == ["supported", "supported", "source_conflict"]
    assert len({p["storage_id"] for p in pairs if p["relationship_status"] == "supported"}) == 1
    assert not any(p["exclusive_allocation"] for p in pairs)
    tables["solar"].append({"EinheitMastrNummer": "SEE4"})
    tables["storage"].append({"EinheitMastrNummer": "SEE10"})
    assert len(make_relationships(tables)) == 3


def make_registry(root):
    folder = Path(root) / "registry/mastr-20261115-test"
    folder.mkdir(parents=True)
    atomic_json(folder / "source.json", {"url": "https://download.marktstammdatenregister.de/Gesamtdatenexport_20261115_26.2.zip", "ags": ["12065144"]})
    atomic_json(folder / "extraction.json", {"status": "completed", "source_sha256": digest(folder / "source.json")})
    for name, rows in {"solar": [{"EinheitMastrNummer": "SEE1", "Gemeindeschluessel": "12065144", "Gemeinde": "Hohen Neuendorf", "Bruttoleistung": "10", "AnzahlModule": "25", "ArtDerSolaranlage": "853", "EinheitBetriebsstatus": "35", "Inbetriebnahmedatum": "2020-01-01"}], "storage": [], "systems": [], "locations": [], "catalog": []}.items():
        atomic_json(folder / (name + ".json"), rows)
    normalize(folder)
    return folder


def test_published_registry_is_immutable_and_tampering_is_detected(tmp_path):
    folder = make_registry(tmp_path)
    assert RegistrySnapshot(folder).manifest["summary"]["solar"] == 1
    (folder / "normalized.json").write_text("{}")
    with pytest.raises(ValueError, match="integrity"):
        RegistrySnapshot(folder)


def test_completed_acquisition_reuses_verified_snapshot_without_rewriting_provenance(tmp_path, monkeypatch):
    from roof_pv_registry import source
    original = make_registry(tmp_path)
    ags = ["12065144"]
    target = original.with_name("mastr-20261115-" + hashlib.sha256(json.dumps(ags).encode()).hexdigest()[:8])
    original.rename(target)
    manifest = json.loads((target / "manifest.json").read_text())
    manifest["snapshot_id"] = target.name
    atomic_json(target / "manifest.json", manifest)
    before = digest(target / "extraction.json")
    def no_network(*args):
        raise AssertionError("Completed snapshots must not be reacquired.")
    monkeypatch.setattr(source, "RemoteZip", no_network)
    assert source.acquire(tmp_path, manifest["source"]["url"], ags) == target
    assert digest(target / "extraction.json") == before
