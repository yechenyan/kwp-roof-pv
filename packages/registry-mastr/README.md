# Frozen MaStR registry

This Python/uv package imports public XML records without filling suppressed addresses or coordinates. It selects generating/storage units by official municipality code, then retrieves the technical locations and storage systems referenced by those units. Date-stamped official ZIPs are read through bounded HTTP ranges; compressed members, CRC results, retained XML fragments and source metadata remain under ignored `var/registry/`.

```sh
uv run --package roof-pv-registry roof-pv-registry acquire \
  --url https://download.marktstammdatenregister.de/Gesamtdatenexport_20261115_26.2.zip \
  --workers 3
```

Commands run from the repository root. An interrupted acquisition can resume its completed members. A live extraction holds `.acquire.lock`; inspect the recorded process before removing a stale lock after a hard termination. Failed or partial acquisitions are never exposed as completed snapshots.

The normalized snapshot contains solar units, storage units, storage systems, locations, catalog labels and source relationship checks. Both strict-under-30 and exactly-30 active building-PV cohorts are retained. Coordinate-bearing and other context records remain available. Source fields and raw XML are preserved; normalized values use English field names.

Only location reverse lists that contain both the solar and storage unit, plus a storage-system reverse link, produce a supported storage relationship. Unsupported joins are retained as source conflicts. Even supported relationships are not exclusive capacity allocations. Aggregate capacity must count each storage-system ID once.

Linked units outside the selected AGS scope are not automatically imported: a technical location can reference additional external units. The source graph is complete for retained records and referenced location/system records, not asserted complete for every external unit at those locations. Registry corrections and non-public address evidence remain separate sources.

Completed snapshots are immutable and checksummed. Large source files and normalized databases are not committed to Git. Source attribution: Bundesnetzagentur / Marktstammdatenregister, dl-de/by-2-0.
