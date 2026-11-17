"""Assemble an uncompressed English report, geodata and standalone result website."""
import shutil

from build import APP, digest, write, read


def package():
    source = APP / "dist"
    source_paths = [*APP.glob("*.py"), APP / "README.md"]
    for name in ["static", "locales", "tests"]:
        source_paths.extend(p for p in (APP/name).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    source_files = {str(p.relative_to(source)): {"sha256": digest(p), "bytes": p.stat().st_size}
                    for p in sorted(source.rglob("*")) if p.is_file() and p.name != "delivery-manifest.json"}
    write(source / "delivery-manifest.json", {"schema_version": 1,
          "algorithm": read(source / "summary.json")["algorithm"], "files": source_files,
          "source_files": {str(p.relative_to(APP)): digest(p) for p in sorted(source_paths)}})
    output = APP / "delivery"
    output.mkdir(exist_ok=True)
    geodata = output / "geodata"
    website = output / "web"
    geodata.mkdir(exist_ok=True)
    website.mkdir(exist_ok=True)
    shutil.copy2(source / "roof-pv-report.pdf", output / "roof-pv-report.pdf")
    for name in ["roof-pv-results.gpkg", "results.geojson"]:
        shutil.copy2(source / name, geodata / name)
    for name in ["records.json", "summary.json", "arrays.geojson", "buildings.geojson", "municipalities.geojson"]:
        shutil.copy2(source / name, website / name)
    for directory in ["static", "vendor"]:
        shutil.copytree(source / directory, website / directory, dirs_exist_ok=True)
    html = (source / "index.html").read_text()
    html = html.replace('<a href="roof-pv-report-zh.pdf" target="_blank" rel="noopener">Chinese report ↗</a>', "")
    html = html.replace('href="roof-pv-report.pdf"', 'href="../roof-pv-report.pdf"')
    html = html.replace('href="roof-pv-results.gpkg"', 'href="../geodata/roof-pv-results.gpkg"')
    (website / "index.html").write_text(html)
    application = website / "static" / "app.js"
    application.write_text(application.read_text().replace('"roof-pv-results.gpkg"', '"../geodata/roof-pv-results.gpkg"'))
    (output / "README.md").write_text(
        "# Rooftop PV geolocation - project delivery\n\n"
        "Study area: Hohen Neuendorf and Birkenwerder.\n\n"
        "- `roof-pv-report.pdf`: English overall project report.\n"
        "- `geodata/roof-pv-results.gpkg`: primary GIS delivery; all spatial layers use EPSG:3035.\n"
        "- `geodata/results.geojson`: associated result points in EPSG:4326.\n"
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
        "Image associations are inferred and later-installation building allocations are statistical. "
        "Scores are compatibility measures, not calibrated identity probabilities. "
        "Storage totals must be deduplicated by equipment/system ID across PV rows. "
        "Detailed methods, quality limits, source attribution and model comparisons are in the report.\n"
    )
    files = {str(p.relative_to(output)): {"sha256": digest(p), "bytes": p.stat().st_size}
             for p in sorted(output.rglob("*")) if p.is_file() and p.name != "delivery-manifest.json"}
    if any("-zh" in name or name.endswith((".zip", ".tar", ".gz")) for name in files):
        raise ValueError("The delivery must contain no Chinese report or compressed archives.")
    write(output / "delivery-manifest.json", {"schema_version": 1,
          "algorithm": read(source / "summary.json")["algorithm"], "files": files})
    print("Assembled uncompressed delivery in packages/result-web-app/delivery")


if __name__ == "__main__":
    package()
