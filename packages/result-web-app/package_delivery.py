"""Assemble the versioned inventory reports, geodata and standalone atlas."""
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from build import APP, GIS_DATA, REPORT_INPUTS, VERIFIED_BUILD_FILES, WEB_DATA, digest, ranking_files, read, write


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


def package():
    source = APP / "dist"
    destination = APP / "delivery-v4"
    verification, reports = release_fingerprints(source)
    with TemporaryDirectory(dir=APP) as folder:
        stage = Path(folder) / "delivery"
        assemble(source, stage, verification, reports)
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


def assemble(source, output, verification, reports):
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
    for name in (*WEB_DATA, *ranking_files(source)):
        (website / name).parent.mkdir(parents=True, exist_ok=True)
        copy_checked(name, website / name)
    (website / "static").mkdir()
    for name in ("static/index.html", "static/app.js", "static/style.css", "index.html"):
        copy_checked(name, website / name)
    shutil.copytree(source / "vendor", website / "vendor")
    html = (website / "index.html").read_text()
    html = html.replace('href="roof-pv-report-zh.pdf"', 'href="../roof-pv-report-zh.pdf"')
    html = html.replace('href="roof-pv-report.pdf"', 'href="../roof-pv-report.pdf"')
    html = html.replace('href="roof-pv-results.gpkg"', 'href="../geodata/roof-pv-results.gpkg"')
    (website / "index.html").write_text(html)
    application = website / "static" / "app.js"
    application.write_text(application.read_text().replace('"roof-pv-results.gpkg"', '"../geodata/roof-pv-results.gpkg"'))
    (output / "README.md").write_text(
        "# Rooftop PV inventory - project delivery\n\n"
        "Study area: Hohen Neuendorf and Birkenwerder.\n\n"
        "- `roof-pv-report.pdf` and `roof-pv-report-zh.pdf`: inventory and postcode summaries.\n"
        "- `geodata/roof-pv-results.gpkg`: primary GIS delivery; all spatial layers use EPSG:3035.\n"
        "- `geodata/results.geojson`: provisional public-coordinate associations in EPSG:4326.\n"
        "- `web/`: standalone read-only result website, local data and vendored Leaflet.\n\n"
        "## View the website\n\n"
        "Open a terminal in this delivery folder and run:\n\n"
        "```sh\npython3 -m http.server 8791\n```\n\n"
        "Open <http://127.0.0.1:8791/web/>. Serve the delivery root so the website's report and GIS links resolve. "
        "A server is required because browsers restrict data requests from local file URLs. "
        "Live Brandenburg RGB/CIR WMS and the optional OpenStreetMap basemap require internet access.\n\n"
        "The website supports browsing, search, filters and installation details. "
        "Confidence/reason editing requires the repository's separate result server; editing is disabled in this standalone static copy.\n\n"
        "## Interpretation\n\n"
        "The atlas opens on observed PV buildings and postcode totals. Anonymous registry candidates are selected by their total evidence points. A collapsed panel offers the highest-scoring roofs for review when none qualifies. "
        "Post-capture records are retained in postcode totals. Scores express technical compatibility. "
        "Storage totals must be deduplicated by equipment/system ID across PV rows. "
        "Methods, quality limits and source attribution are in the report.\n"
    )
    files = {str(p.relative_to(output)): {"sha256": digest(p), "bytes": p.stat().st_size}
             for p in sorted(output.rglob("*")) if p.is_file() and p.name != "delivery-manifest.json"}
    algorithm = read(website / "summary.json")["algorithm"]
    write(output / "delivery-manifest.json", {"schema_version": 1, "algorithm": algorithm, "files": files,
          "source_files": {str(p.relative_to(APP)): digest(p) for p in sorted(source_paths)}})


if __name__ == "__main__":
    package()
