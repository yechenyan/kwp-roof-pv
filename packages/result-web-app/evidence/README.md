# Frozen LGB building evidence

Attribution for both geometry inputs: GeoBasis-DE/LGB, dl-de/by-2-0.

`lgb-postcodes.geojson` contains the official postcode geometries intersecting the study extract. Its provenance lists the original [LGB postcode service](https://ogc-api.geobasis-bb.de/datasets/plz/collections/plz_bebb), bounding box, retrieval timestamp and response checksum.

`lod2-roof-faces.json.gz` is a compact extraction from the [official CityGML LoD2 tiles](https://data.geobasis-bb.de/geobasis/daten/3d_gebaeude/lod2_gml/). It includes every available LoD2 building referenced by the frozen group batch. Its header records the original URLs and archive checksums for all 46 tiles, and the hashes for the survey footprints and group GeoJSON. Ground geometry and usable roof projections are stored as WKB hex in EPSG:25833. Each roof face includes its plane slope and its true-north downslope azimuth, when meaningful.

During extraction, faces are accepted only when their planar projection is valid, their slope is at most 65 degrees and the source vertices lie within 0.10 m of the fitted plane. The atlas performs the footprint comparison and the intersection with detected PV when building the inventory. A different survey or grouping snapshot requires a compatible LoD2 evidence extract.
