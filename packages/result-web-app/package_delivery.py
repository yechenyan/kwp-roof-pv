"""Assemble the versioned inventory reports, geodata and standalone atlas."""
import argparse
from collections import Counter
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from build import APP, GIS_DATA, REPORT_INPUTS, VERIFIED_BUILD_FILES, WEB_DATA, digest, ranking_files, read, write
from candidate_scoring import POINT_SCALE


REPORTS = ("roof-pv-report.pdf", "roof-pv-report-zh.pdf")


def release_fingerprints(source):
    verification = read(source / "verification.json")
    build_hashes = verification.get("build_files_sha256", {})
    if (not verification.get("building_evidence_and_postcode_reconciliation")
            or set(build_hashes) != set((*VERIFIED_BUILD_FILES, *ranking_files(source)))):
        raise ValueError("Verify the current building inventory before packaging it.")
    for name, checksum in build_hashes.items():
        if digest(source / name) != checksum:
            raise ValueError(f"Build file differs from verification: {name}")

    report_inputs = {name: build_hashes[name] for name in REPORT_INPUTS}
    reports = {}
    for name in REPORTS:
        sidecar = (source / name).with_suffix(".provenance.json")
        if not sidecar.is_file():
            raise ValueError(f"Regenerate the report to record its build fingerprint: {name}")
        report = read(sidecar)
        if report.get("inputs_sha256") != report_inputs or report.get("pdf_sha256") != digest(source / name):
            raise ValueError(f"Report differs from the verified build: {name}")
        reports[name] = report
    return verification, reports


def package(destination=APP / "delivery-v4", *, results_only=False):
    source = APP / "dist"
    verification, reports = release_fingerprints(source)
    with TemporaryDirectory(dir=APP) as folder:
        stage = Path(folder) / "delivery"
        assemble(source, stage, verification, reports, results_only=results_only)
        previous = stage.parent / "previous"
        if destination.exists():
            destination.rename(previous)
        try:
            stage.rename(destination)
        except OSError:
            if previous.exists():
                previous.rename(destination)
            raise
    print(f"Assembled uncompressed delivery in {destination.name}.")


def published_matches(records, groups, metadata):
    group_lookup = {group["properties"]["id"]: group for group in groups}
    features = []
    for row in records:
        proposal = row.get("joint_proposal")
        if proposal:
            kind = "joint"
            group_ids = proposal["group_ids"]
            building_id = proposal["building_id"]
            score = proposal["score"]
            evidence_points = [value / POINT_SCALE for value in proposal["points"]]
        elif row["association_status"] == "inferred_image_association":
            kind = "coordinate"
            group_ids = [row["group_id"]]
            building_id = None
            score = row["score"]
            evidence_points = None
        else:
            continue

        observations = [group_lookup[key] for key in group_ids]
        outline = unary_union([shape(feature["geometry"]) for feature in observations])
        if outline.is_empty or not outline.is_valid:
            raise ValueError(f"Invalid matched array geometry: {row['solar_id']}")
        point = row["point"] if kind == "coordinate" else list(outline.representative_point().coords[0])
        dates = sorted({date for feature in observations for date in feature["properties"]["capture_dates"]})
        features.append({
            "type": "Feature", "id": row["solar_id"], "geometry": mapping(outline),
            "properties": {
                "solar_id": row["solar_id"], "kind": kind, "point": point, "building_id": building_id,
                "municipality": row["municipality"], "postcode": row["postcode"], "power_kw": row["power_kw"],
                "commissioned": row["commissioned"], "capture_dates": dates, "group_count": len(group_ids),
                "score": score, "evidence_points": evidence_points, "identity_verified": row["identity_verified"],
            },
        })
    summary = metadata["summary"]
    expected = Counter(joint=summary["joint_proposal_count"],
                       coordinate=summary["states"].get("inferred_image_association", 0))
    if Counter(feature["properties"]["kind"] for feature in features) != expected:
        raise ValueError("Published matches do not agree with the verified counts.")
    return {"type": "FeatureCollection", "snapshot": metadata["created_at"], "features": features}


def assemble(source, output, verification, reports, *, results_only=False):
    source_paths = [*APP.glob("*.py"), APP / "README.md"]
    for name in ["static", "locales", "tests", "evidence"]:
        source_paths.extend(p for p in (APP/name).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    output.mkdir(exist_ok=True)
    geodata = output / "geodata"
    website = output / "web"
    geodata.mkdir(exist_ok=True)
    website.mkdir(exist_ok=True)
    hashes = verification["build_files_sha256"]

    def copy_checked(name, destination, checksum=None):
        shutil.copy2(source / name, destination)
        if digest(destination) != (hashes[name] if checksum is None else checksum):
            raise ValueError(f"Verified file changed during packaging: {name}")

    for name, report in reports.items():
        copy_checked(name, output / name, report["pdf_sha256"])
        write((output / name).with_suffix(".provenance.json"), report)
    write(output / "verification.json", verification)
    for name in GIS_DATA:
        copy_checked(name, geodata / name)
    (website / "static").mkdir()
    copy_checked("static/style.css", website / "static/style.css")
    shutil.copytree(source / "vendor", website / "vendor")
    if results_only:
        for name in ("summary.json", "municipalities.geojson", "postcodes.geojson"):
            copy_checked(name, website / name)
        context = read(source / "context.json")
        matches = published_matches(read(source / "records.json"), context["groups"], context["metadata"])
        write(website / "matches.geojson", matches)
        shutil.copy2(APP / "static/delivery.html", website / "index.html")
        for name in ("delivery.js", "delivery.css"):
            shutil.copy2(APP / "static" / name, website / "static" / name)
    else:
        for name in (*WEB_DATA, *ranking_files(source)):
            (website / name).parent.mkdir(parents=True, exist_ok=True)
            copy_checked(name, website / name)
        for name in ("static/index.html", "static/app.js", "index.html"):
            copy_checked(name, website / name)
        html = (website / "index.html").read_text()
        html = html.replace('href="roof-pv-report-zh.pdf"', 'href="../roof-pv-report-zh.pdf"')
        html = html.replace('href="roof-pv-report.pdf"', 'href="../roof-pv-report.pdf"')
        html = html.replace('href="roof-pv-results.gpkg"', 'href="../geodata/roof-pv-results.gpkg"')
        (website / "index.html").write_text(html)
        application = website / "static" / "app.js"
        application.write_text(application.read_text().replace('"roof-pv-results.gpkg"', '"../geodata/roof-pv-results.gpkg"'))

    interpretation = (
        "The map shows one published result for each assigned record, including joint matches and public-coordinate associations. "
        "Purple markers show joint matches. Blue markers show public-coordinate associations. "
        "Selecting a marker or record opens its evidence. Building identities remain unverified.\n\n"
        if results_only else
        "The atlas opens on observed PV buildings and postcode totals. The registry review shows scored candidates "
        "and identifies joint proposals. The local annotation service handles confidence and reason edits.\n\n"
    )
    (output / "README.md").write_text(
        "# Rooftop PV inventory - project delivery\n\n"
        "Study area: Hohen Neuendorf and Birkenwerder.\n\n"
        "- `roof-pv-report.pdf` and `roof-pv-report-zh.pdf`: inventory and postcode summaries.\n"
        "- `geodata/roof-pv-results.gpkg`: primary GIS delivery; all spatial layers use EPSG:3035.\n"
        "- `geodata/results.geojson`: provisional public-coordinate associations in EPSG:4326.\n"
        "- `web/`: standalone read-only result website, local data and vendored Leaflet.\n\n"
        "## View the website\n\n"
        "Open a terminal in this delivery folder and run:\n\n"
        "```sh\npython3 -m http.server 8791 --bind 127.0.0.1\n```\n\n"
        "Open <http://127.0.0.1:8791/web/>. Serve the delivery root so the website's report and GIS links resolve. "
        "A server is required because browsers restrict data requests from local file URLs. "
        "Live Brandenburg RGB/CIR WMS and the optional OpenStreetMap basemap require internet access.\n\n"
        "## Interpretation\n\n"
        + interpretation +
        "The GeoPackage includes postcode totals, including records commissioned after image capture. "
        "Storage totals must be deduplicated by equipment/system ID across PV rows. "
        "Methods, quality limits and source attribution are in the report.\n"
    )
    files = {str(p.relative_to(output)): {"sha256": digest(p), "bytes": p.stat().st_size}
             for p in sorted(output.rglob("*")) if p.is_file() and p.name != "delivery-manifest.json"}
    algorithm = read(website / "summary.json")["algorithm"]
    write(output / "delivery-manifest.json", {"schema_version": 1, "algorithm": algorithm, "files": files,
          "source_files": {str(p.relative_to(APP)): digest(p) for p in sorted(source_paths)}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracked", action="store_true", help="Replace the tracked delivery directory with the verified build.")
    args = parser.parse_args()
    package(APP / "delivery" if args.tracked else APP / "delivery-v4", results_only=args.tracked)
