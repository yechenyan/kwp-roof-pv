# MaStR comparison and proposed matching design

This is a readiness audit and a proposed design. No PV observation has been assigned a MaStR identity, and no review decision, original inference artifact, or GeoPackage has been changed.

The recommendation is to resolve image regions into reviewable array groups, then rank registry associations with explicit alternatives and an unmatched option. The current public data cannot reliably identify every anonymous small installation at building level.

## Snapshots and reproducibility

| Input | Audited snapshot |
| --- | --- |
| Image run | `municipal-27320a7f3fb1` |
| Human review | Revision 0; all 6,355 active candidates pending, mounting unknown |
| Image acquisition | 2026-04-22 and 2026-05-01, according to individual source tiles |
| MaStR export | `Gesamtdatenexport_20261114_26.2.zip` |
| Local registry tables | 3,475 solar units; 1,970 storage units; 1,970 storage systems; 3,475 technical locations |
| Registry provenance | 97 solar/storage XML shards and 100 location/storage-system XML shards |

The existing pilot extract was reused, rather than silently changing its reference date. The official download page checked on 2026-11-15 advertises a newer daily export. A production matching run should import a newly pinned snapshot and report changes against this baseline.

The audit verifies unique IDs, equality between processed records and all retained shard-cache records, and equality of every retained record to its saved original XML fragment. Input hashes are in [mastr-readiness.json](mastr-readiness.json). Historical compressed shards were not downloaded again, so their previously recorded CRC checks are not a new remote verification.

There is an extraction limitation: all expected shards were processed, but the original local selection used exact municipality names. All retained solar records have the expected AGS codes, but this does not prove that a nationwide AGS-only selection would return no additional records. The new importer should select by AGS `12065144` and `12065036`, audit name/AGS disagreements, and retain corrections separately.

Run from the repository root with the existing pilot data available:

```sh
uv run --package roof-pv-review python scripts/audit_mastr_readiness.py --registry-home ../lab
```

This writes the summary to `docs/mastr-readiness.json` and detailed diagnostics plus copies of the registry tables to ignored `var/analysis/mastr-readiness/`. The review database is opened read-only inside a consistent transaction. The script performs diagnostics, not matching.

## Actual comparison

| Measure | Hohen Neuendorf | Birkenwerder | Total |
| --- | ---: | ---: | ---: |
| MaStR solar records, all statuses/types | 2,574 | 901 | 3,475 |
| Building solar, all statuses/powers | 1,804 | 594 | 2,398 |
| Plug-in/balcony solar, all statuses | 770 | 306 | 1,076 |
| Ground-mounted solar, all statuses | 0 | 1 | 1 |
| Active building solar, `0 < kWp < 30`, no coordinates | 1,741 | 577 | 2,318 |
| Same scope including exactly 30 kWp | 1,742 | 578 | 2,320 |
| Image candidate regions intersecting the municipality | 4,747 | 1,608 | 6,355 |
| Image candidates with cadastral overlap | 3,800 | 1,324 | 5,124 |
| Target units with reciprocal storage links | 1,128 | 354 | 1,482 |

The table's original target cohort uses `EinheitBetriebsstatus=35` (in operation), `ArtDerSolaranlage=853` (building solar), positive gross power below 30 kWp and no coordinate pair. Building solar is a registry category; it does not by itself prove roof mounting. The other status codes present are 31 (planned), 37 (temporarily shut down), and 38 (permanently shut down). Labels were decoded from the export's `Katalogwerte.xml`.

The [official FAQ](https://www.marktstammdatenregister.de/MaStRHilfe/subpages/faq.html) states that confidential precise location information includes installations **at most 30 kW(p)**. The two exactly-30-kWp records should therefore form an explicit additional cohort. Preserve the original strict-under-30 results for comparability.

Candidate regions are not installation counts. Different roof faces, disconnected module rows, segmentation fragments, and unresolved RGB/RGBI disagreement can produce multiple regions. Conversely, one region can contain multiple registry units or an extension. False positives, missed arrays, balcony systems and non-roof installations also remain possible. The excess count cannot be interpreted as unregistered installations or used to calculate detection accuracy.

### Image readiness

| Observation | Result | Consequence |
| --- | ---: | --- |
| RGBI/RGB agreement category `both` | 2,862 | Agreement is review evidence, not identity confidence |
| Geometry conflict | 1,026 | Resolve geometry before using array area |
| RGBI only / RGB only | 1,292 / 1,175 | Keep separate observation evidence |
| Candidates smaller than 10 square metres | 4,014 | Fragmentation and small detections need review |
| Median candidate area | 6.04 square metres | A candidate should not automatically represent a complete installation |
| Distinct intersected cadastral feature IDs | 3,450 | This is not a count of confirmed PV buildings |
| Cadastral features intersecting multiple candidates | 1,180 | Prioritize grouping and duplicate checks |

The cadastral snapshot contains building IDs and footprints, but no street/house number, postcode polygons, roof-plane slope, or roof-plane azimuth. One cadastral feature may also contain multiple geometric parts. Grouping all detections with one feature ID would be unsafe.

### Registry field availability in the 2,318-unit target cohort

| Field | Present | Interpretation |
| --- | ---: | --- |
| Street / house number / longitude / latitude | 0 each | No direct address or nearest-neighbour match for these targets |
| Municipality, postcode, town and location ID | 2,318 each | Useful for narrowing and registry relationships |
| Commissioning date and gross power | 2,318 each | Dates and capacity, not observed construction date or measured image power |
| Module count | 2,301 | Useful with data-quality checks |
| Main direction | 2,241 | Categorical direction, not a measured exact azimuth |
| Main inclination | 2,233 | Usually an interval rather than an exact angle |
| Secondary direction | 762 | Missing does not prove that a second roof face is absent |

Populated fields can be wrong. A broad review screen flags 14 records whose power per reported module falls outside 0.1–0.8 kWp. This screen is not a physical impossibility rule: older module technology and other explanations require review. For example, `SEE980446902857` reports 9.010 kWp and one module. Such a count must be downweighted rather than used to force a precise array-size match.

Municipality and postcode can also disagree. One Birkenwerder target has postcode 16540 instead of the predominant 16547, and 10 Hohen Neuendorf targets use 16515. Keep these records, flag the discrepancy, and avoid a hard postcode exclusion until postcode geometry and source quality have been checked.

A real anonymous target illustrates the remaining ambiguity: `SEE908386676980` in Birkenwerder reports 6.960 kWp, 24 modules, commissioning on 2017-03-24, main direction east, secondary direction west, and main inclination 41–60 degrees. Its location ID is `SEL967373029826`, but it has no public address or coordinate. These values support searching for a compatible older east/west array group within the municipality. They do not identify a unique building among the 1,608 current image candidates in Birkenwerder. No building has been assigned to this record.

### Dates

| Commissioning relative to the two acquisition dates | Strict-under-30 targets |
| --- | ---: |
| Before 2026-04-22 | 2,164 |
| On 2026-04-22 | 1 |
| After 2026-04-22 through 2026-05-01 | 6 |
| After 2026-05-01 | 147 |

Use a candidate's actual tile dates in matching; a municipality-wide date is insufficient. Commissioning is not necessarily installation: panels may already be visible before electrical commissioning, and extensions may share a roof with older panels. The 147 later records should enter a separate evidence queue, not be forced onto an old detected array or automatically declared absent. Same-day observations and mixed-date polygons remain uncertain.

### Coordinate-bearing records: a diagnostic, not a benchmark

There are 21 solar records with coordinate pairs. Nineteen fall inside the study area; three of these were commissioned after their local image acquisition. This leaves 16 pre-acquisition, in-area records for initial inspection. These are larger systems and cannot establish accuracy on anonymous installations below 30 kWp.

Of those 16 points, 13 are within 25 m of some candidate polygon and 15 within 50 m; 10 have multiple candidate polygons within 25 m. These are proximity counts, not matching precision or recall. The largest nearest-polygon distance is 55.075 m.

Actual diagnostic examples:

1. `SEE962414056526`: 39.680 kWp, 128 modules, commissioned 2020-06-05. The local image is from 2026-04-22. The nearest candidate, `municipal-27320a7f3fb1:candidate:3404`, is 1.220 m from the recorded point; one polygon lies within 25 m. This is a promising manual inspection case, not an established identity.
2. `SEE980705107849`: commissioned 2009-09-29. Its point is 15.670 m from candidate `5018`, with six candidates within 25 m. `SEE963056634660`, commissioned 2026-04-30, has exactly the same published coordinate and nearest candidate, although the image was captured on 2026-04-22. A nearest-neighbour rule would conflate distinct units and potentially an extension with an older installation.
3. `SEE967308390861`: its published point is approximately 71.8 km outside the declared municipality. Another record is approximately 2.25 km outside. Both require source correction or external verification and must not become calibration anchors.

### Storage relationships

The existing tables support registry relationship checks independently of image matching:

- A naive grouping join including missing location IDs creates 4,544 pair rows. The earlier diagnostic included 2,750 spurious missing-ID pairs (55 solar records times 50 storage records); a missing ID is not a shared location.
- After excluding missing location IDs, 1,794 pairs remain. All are supported both by the location's reverse unit list and the storage system's reverse list.
- The importer and corrected audit reject missing-location joins before relationship creation. The 2,750 pairs are not real location associations or battery allocations.
- There are 1,687 unique storage units among the supported pairs; 100 appear with multiple solar records.
- In the target cohort, 1,482 solar records have at least one supported storage association.

These are registry location relationships, not proof that a particular detected roof has that battery. Keep kW from the storage unit and usable kWh from the storage system at their original levels. Count each storage ID once in totals; do not sum the same capacity once per linked PV record. No exclusive allocation or new image association was performed in this audit.

## Proposed implementation sequence

### 1. Freeze and normalize the registry

Add `packages/registry-mastr` as a Python/uv package. Download or import a dated official export; filter by AGS; retain raw XML, hashes, original values, catalog version and import lineage. Maintain separate solar-unit, technical-location, storage-unit, storage-system and relationship tables. Validate statuses, coordinate placement, duplicate IDs, dates, powers and module counts.

Expose strict-under-30 and exactly-30 cohorts separately. Retain larger, planned, closed, ground-mounted and plug-in records as context so that visible arrays are not all consumed by the target cohort. Keep registry-snapshot date separate from image-acquisition date and commissioning date.

### 2. Build image array groups before registry assignment

Add grouping to a new `packages/matching` package, referencing immutable inference IDs and current candidate versions. Retain original RGBI and RGB observations. Start with the RGBI geometry; use RGB evidence to flag omissions/conflicts rather than summing both outputs.

Propose groups using cadastral geometric parts, spatial continuity, roof faces where available, shared orientation evidence and human edits. Preserve every member region. Inspect conflicts, unusually small fragments and very large groups first. Do not automatically merge every region on one building or remove every small region. Permit one installation on a house and garage, several registry units on one roof, and extensions at the same location.

Human PV-presence/mounting review and array grouping establish image evidence; they do not establish MaStR identity.

### 3. Inspect anchors and fill the most useful data gaps

Manually inspect the 16 eligible coordinate records and resolve their true roof/array associations where evidence permits. Do not treat the points themselves as exact array outlines. Diagnose missed detections, fragmented arrays and point offsets.

Acquire verified postcode/address geography if available. Roof-plane data from a suitable 3D building/surface product would improve slope, facing direction and projected-area interpretation; no such product is currently loaded. A 2D building outline or the long axis of a panel polygon alone does not give the panels' facing azimuth. Inferred rectangle orientation has a 180-degree ambiguity.

The best improvement for exact small-unit identities would be authorized address/location information already available in KWPconnect or from a network operator/municipality. Availability is unknown. Public names of installations should not become an unverified address source.

### 4. Generate candidates and rank evidence

For coordinate-bearing records, search a validated spatial neighbourhood and keep point-error alternatives. For anonymous records, use the municipality and checked postcode evidence to form the search area. Do not invent a point at a postcode centroid and measure distances from it.

Compare grouped projected PV area, module-count range, gross power, installation-era module characteristics, roof slope, main/secondary directions, roof-face count and temporal evidence. Use intervals and uncertainty for missing or suspect attributes. In a suitable roof model, projected area is approximately module surface area multiplied by the cosine of roof slope; gaps, occlusion and segmentation errors add uncertainty. Do not convert area to an exact kWp value with one universal constant.

The ranking output should include feature-level evidence, conflicts, the best few alternatives, best-versus-next score gap, data completeness and the unmatched option. Initially call the score a ranking score, not a probability. Set weights and acceptance thresholds using independently verified cases, rather than choosing attractive constants and describing them as validated confidence.

Use location groups and capacity accounting to detect competing assignments. A global optimization may make proposals more consistent, but it must permit unmatched and many-to-many cases. It cannot create identity evidence. Avoid a blanket one-registry-record-to-one-polygon constraint.

### 5. Review identity separately from image presence

Extend the React application with a MaStR comparison panel: the grouped array, raw regions, RGB/CIR imagery and acquisition dates alongside registry values, decoded categories, data-quality warnings and alternative proposals. Keep two independent decisions: `pv_presence_review` and `registry_identity_review`.

Proposed association states:

| State | Meaning |
| --- | --- |
| `verified_identity` | Independent location/identifier evidence supports this association; record that evidence |
| `probable_assignment` | Several compatible attributes and a clear ranking gap, but identity is not independently established |
| `ambiguous` | Multiple plausible buildings/array groups or conflicting evidence |
| `post_capture` | Additional temporal evidence is needed for a record commissioned after relevant imagery |
| `unmatched_registry` | No defensible image association yet |
| `unmatched_image` | No defensible registry association yet |
| `source_conflict` | Registry geography/status or other source evidence requires correction |

A human confirming that an image contains panels must not automatically produce `verified_identity`. Review events need evidence, reasons, candidate versions, matcher version, registry snapshot ID and optimistic concurrency checks. Re-grouping or editing geometry makes affected proposals stale; historical decisions remain auditable.

### 6. Handle later installations and export without false precision

For later-commissioned records, prioritize newer official aerial photography, the municipalities' own flights, or authorized location records. Before obtaining replacement imagery, check its acquisition date, ground resolution, spectral bands, coverage, licence and georeferencing. Common 10 m satellite imagery will not resolve typical small roof arrays sufficiently for exact identity assignment.

If building-level evidence remains absent, retain the record with municipality/postcode geometry or a clearly labelled distribution over plausible buildings. Such statistical allocation is useful for aggregate planning, but is a separate product and must not be exported as verified building coordinates. Preserve unmatched records rather than dropping them.

GeoPackage exports remain EPSG:3035. Add separate registry attribute tables, array-group geometry, a many-to-many association table and storage-location relationships. Each association should carry its evidence, state, reasons, alternatives, registry snapshot and image/review provenance. Only verified associations populate a verified identity layer; probable and unlocated results remain distinguishable. Attach storage to the registry location, with an explicit allocation policy and unique-ID totals.

## Validation and delivery gates

First deliver the normalized frozen registry and the anchor-review interface. Then deliver image grouping plus a proposal-only pilot in a limited area, before running municipal assignment.

Measure image-group completeness independently of identity matching. Evaluate retrieval and accepted-assignment precision on independently verified identities, using spatially separated validation cases and explicit coverage/abstention. The 16 larger anchors are a smoke check, not sufficient small-roof validation data. If small-unit identity ground truth is unavailable, report that matching accuracy is unmeasured; visual plausibility and aggregate capacity agreement are insufficient substitutes.

Before enabling automatic acceptance, define an acceptable precision target with the project owner and demonstrate it with enough representative verified cases and uncertainty bounds. Until then, proposals remain reviewable, and no numerical automatic-match success rate is promised.

## Sources checked

- [Official MaStR data download](https://www.marktstammdatenregister.de/MaStR/Datendownload): daily public XML exports and historical snapshots.
- [Official MaStR FAQ](https://www.marktstammdatenregister.de/MaStRHilfe/subpages/faq.html): public/confidential data and the at-most-30-kWp location rule.
- [open-MaStR](https://github.com/OpenEnergyPlatform/open-MaStR/): optional Python acquisition/processing library; it cannot restore coordinates suppressed in the public source.
- [OEP MaStR export schema](https://openenergyplatform.github.io/mastr-docs/schema/): field and entity reference; the retained export's XML/catalog values remain the audit input.

All numeric local results in this document come from the frozen source tables and the read-only audit, not from the external documentation.
