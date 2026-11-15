# Human review guide

1. Start the local service and open the workspace. Confirm the run and image date. All initial candidates are pending.
2. Enter a reviewer name. This is audit attribution on a local service, not an authenticated identity.
3. Select a candidate from the queue or map. Enable cyan RGBI and pink RGB observations to compare evidence; the selected default outline is highlighted in white. Use Full sample to reset the map extent. Switch RGB/CIR display to inspect a false-color NIR/R/G view derived from the same original four-band input; this display is not a substitute model input.
4. Choose Confirm PV, Reject, or Uncertain. Reject and Uncertain require a reason. Separately classify mounting as roof, ground, other or unknown. PV confirmation does not imply roof mounting. Save notes with the decision. A model score is not an accuracy percentage.
5. To correct an outline, select Edit vertices, drag handles, and explicitly save. Cancel abandons geometry changes. Restore model geometry creates another audited review update.
6. Use Draw missed PV to add a polygon: click at least three corners, then Finish polygon. The polygon starts pending and needs a separate confirmation. Invalid/self-intersecting or out-of-footprint shapes are rejected by the API.
7. Inspect roofs that have no model candidate too. Use Mark checked area to record a completed inspection region. This is separate from PV confirmation and is not a statement of PV absence.
8. A version-conflict message means another tab changed the candidate. Reload, inspect the newer decision, and retry deliberately. Review decisions persist across page reloads and service restarts.
9. Check multiple candidates and Merge to combine review geometries. Split draws a cutting line across one selected polygon. Parents become inactive, descendants start pending, and lineage stays in the audit trail. These are review objects, not registry identities.
10. Undo last action uses the current review revision and preserves the original event. An undo cannot immediately be undone again.
11. Export GeoPackage creates a fixed review snapshot and an offline English HTML report. Initial machine results have zero confirmed polygons.

## GeoPackage content

| Layer/table | Meaning |
| --- | --- |
| `candidates` | All model/manual candidates with current geometry and review state |
| `confirmed_pv` | Only candidates explicitly confirmed by a reviewer |
| `confirmed_rooftop_pv` | Confirmed PV explicitly classified as roof mounted |
| `candidate_points` | Interior representative points in EPSG:3035 |
| `confirmed_rooftop_points` | Interior points of confirmed rooftop geometry |
| `retired_candidates` | Superseded objects retained for lineage |
| `rgbi_observations` | Immutable RGBI model polygons |
| `rgb_observations` | Immutable RGB model polygons |
| `reviewed_areas` | Regions explicitly recorded as inspected |
| `coverage` | Input image footprint and date; not an assertion of complete valid pixels or no PV |
| `pixel_coverage` | Municipal target-pixel valid/missing regions |
| `buildings` | Current official ALKIS contextual footprints |
| `municipalities` | Official target boundaries |
| `export_metadata` | Export/run/review revision, scope and attribution |
| `inference_provenance` | Original model/input parameters, hashes and input validity counts |
| `review_events` | Versioned audit trail with original/changed geometry and decisions |

All spatial layers use EPSG:3035. Preview geometry/API use longitude/latitude (RFC 7946); the underlying source and score rasters use EPSG:25833. The optional OSM tiles require internet; local image review does not.

The municipal overview displays aggregated candidate counts. Zoom closer for outlines and building footprints. Lack of cadastral overlap is evidence for inspection, not a ground-mounting classification. Authenticated multi-user review is outside this local application.

## MaStR identity review

Use the left-hand **MaStR registrations** list. The default cohort contains active, anonymous building-PV records up to and including 30 kWp; coordinate-bearing and other context records have separate filters. Search by registry ID, municipality or postcode, or filter by association state. Selecting a record places its numbered image alternatives on the centre map. Clicking a point opens its proposed array group in the right-hand panel.

The right-hand panel separates saved links, potential matches and other identity decisions. Changing the potential registry partner there preserves the left-hand search record and its map points. Use the image-review tab to check PV presence or edit geometry. The cyan dashed group outline preserves its constituent PV regions. A public registry coordinate, when present, is shown separately in magenta; numbered image candidates do not fill that coordinate field. Five scored examples can come from a much larger tied set. See [map review workspace](map-review-workspace.md) for layout and marker details.

First review the image members, rooftop mounting and grouping. Edit, add, merge or split PV candidates as needed, then rebuild proposals. These image edits make the previous group stale. Afterwards record a separate identity decision and its source reference. Attribute compatibility supports an ambiguous or probable association; it does not establish a verified identity. Verified identity additionally requires independent location/identifier evidence, checked grouping, and all group members confirmed as rooftop PV.

Identity reviews have independent optimistic versions and evidence events. They do not alter the PV-presence decision. Changing the source registry snapshot or affected image members prevents saving an obsolete association. Rebuilding does not automatically migrate earlier identity decisions. Review history remains in SQLite.

When a batch exists, the usual export includes `array_groups`, current `verified_mastr_pv` / `verified_mastr_points`, full `mastr_solar` and other registry tables, `mastr_association_proposals`, `mastr_unit_outcomes`, `mastr_identity_reviews`, `mastr_review_events`, `mastr_provenance` and `mastr_storage_relationships`. Unmatched records remain attribute rows, without invented building coordinates. Count usable storage capacity once per storage-system ID; relationship rows can share a system.
