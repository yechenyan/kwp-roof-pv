# Rooftop PV inventory - project delivery

Study area: Hohen Neuendorf and Birkenwerder.

- `roof-pv-report.pdf` and `roof-pv-report-zh.pdf`: inventory and postcode summaries.
- `geodata/roof-pv-results.gpkg`: primary GIS delivery; all spatial layers use EPSG:3035.
- `geodata/results.geojson`: provisional public-coordinate associations in EPSG:4326.
- `web/`: standalone read-only result website, local data and vendored Leaflet.

## View the website

Open a terminal in this delivery folder and run:

```sh
python3 -m http.server 8791 --bind 127.0.0.1
```

Open <http://127.0.0.1:8791/web/>. Serve the delivery root so the website's report and GIS links resolve. A server is required because browsers restrict data requests from local file URLs. Live Brandenburg RGB/CIR WMS and the optional OpenStreetMap basemap require internet access.

## Interpretation

The map shows one published result for each assigned record, including joint matches and public-coordinate associations. Purple markers show joint matches. Blue markers show public-coordinate associations. Selecting a marker or record opens its evidence. Building identities remain unverified.

The GeoPackage includes postcode totals, including records commissioned after image capture. Storage totals must be deduplicated by equipment/system ID across PV rows. Methods, quality limits and source attribution are in the report.
