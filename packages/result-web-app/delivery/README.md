# Rooftop PV geolocation - project delivery

Study area: Hohen Neuendorf and Birkenwerder.

- `roof-pv-report.pdf`: English overall project report.
- `geodata/roof-pv-results.gpkg`: primary GIS delivery; all spatial layers use EPSG:3035.
- `geodata/results.geojson`: associated result points in EPSG:4326.
- `web/`: standalone read-only result website, local data and vendored Leaflet.

## View the website

Open a terminal in this delivery folder and run:

```sh
python3 -m http.server 8791
```

Open <http://127.0.0.1:8791/web/>. Serve the delivery root so the website's report and GIS links resolve. A server is required because browsers restrict data requests from local file URLs. Live Brandenburg RGB/CIR WMS and the optional OpenStreetMap basemap require internet access.

The website supports browsing, search, filters and installation details. Confidence/reason editing requires the repository's separate result server; editing is disabled in this standalone static copy.

## Interpretation

Image associations are inferred and later-installation building allocations are statistical. Scores are compatibility measures, not calibrated identity probabilities. Storage totals must be deduplicated by equipment/system ID across PV rows. Detailed methods, quality limits, source attribution and model comparisons are in the report.
