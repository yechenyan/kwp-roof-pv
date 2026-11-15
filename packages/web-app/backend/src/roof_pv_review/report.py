"""Offline English report generated from the same frozen export snapshot."""
from collections import Counter
from html import escape
from pathlib import Path


def make_report(snapshot, export, output):
    manifest = snapshot["manifest"]
    features = snapshot["candidates"]["features"]
    states = Counter(f["properties"]["status"] for f in features)
    categories = Counter(f["properties"]["category"] for f in features)
    sources = manifest.get("source_tiles", [])
    config = manifest["identity"].get("config", {})
    def table(headers, rows):
        head = ''.join('<th>' + escape(str(value)) + '</th>' for value in headers)
        body = ''.join('<tr>' + ''.join('<td>' + escape(str(value)) + '</td>' for value in row) + '</tr>' for row in rows)
        return '<div class="table"><table><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></div>'
    state_table = table(["Review state", "Active objects"], [(key, states.get(key, 0)) for key in ["pending", "confirmed", "rejected", "uncertain"]])
    model_table = table(["Track", "Variant", "Checkpoint SHA-256"], [(key, spec["variant"], spec["sha256"]) for key, spec in config.get("models", {}).items()])
    source_table = table(["Tile", "Captured", "Published", "SHA-256"], [(s["id"], s["acquisition_date"], s["publication_date"], s["sha256"]) for s in sources])
    category_table = table(["Model agreement", "Candidates"], sorted(categories.items()))
    valid = manifest["input"]["valid_pixels"]
    missing = manifest["input"]["missing_pixels"]
    rooftop = sum(f["properties"]["status"] == "confirmed" and f["properties"].get("mounting") == "roof" for f in features)
    overlap = sum((f["properties"].get("building_overlap_ratio") or 0) > 0 for f in features)
    captured = escape(manifest["input"]["acquisition_date"])
    run_id = escape(snapshot["run_id"])
    note = "Human validation is pending." if not states.get("confirmed") else "Review is versioned; use the confirmed layers for accepted objects."
    repair_count = sum(export.get("geometry_repair_counts", {}).values())
    matching = export.get("matching")
    matching_section = ""
    scope_note = "MaStR matching, installed power and battery assignment are excluded."
    if matching:
        scope_note = "A separate frozen MaStR snapshot supplies provisional association rankings; independently evidenced human identity reviews remain distinct from PV-presence decisions. Battery records remain location relationships without exclusive allocation."
        matching_section = '<h2>MaStR associations</h2>' + table(["Measure", "Value"], matching.items()) + '<p>Ranking scores are experimental and are not probabilities. Unmatched and post-capture registry units are retained in attribute tables. Stale groups and identity decisions are explicitly flagged and excluded from verified identity layers. Public small-unit coordinates are not invented. Storage capacity must be counted once per storage-system ID, not once per PV association.</p>'
    content = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PV image survey report</title><style>
    :root{{font:15px/1.65 system-ui,sans-serif;color:#203e35;background:#f0f3ed}}main{{max-width:1100px;margin:auto;padding:40px 24px}}h1{{font-size:38px;letter-spacing:-1px}}h2{{margin-top:35px}}.eyebrow{{font-size:11px;letter-spacing:2px;color:#71876b}}.notice{{background:#e0ecda;padding:20px;border-left:4px solid #478060}}.stats{{display:flex;gap:18px;flex-wrap:wrap;margin:25px 0}}.stats div{{background:white;border:1px solid #dae3d2;padding:18px;min-width:160px}}.stats strong{{font-size:30px;display:block}}table{{width:100%;border-collapse:collapse;background:white;font-size:12px}}td,th{{padding:10px 12px;text-align:left;border-bottom:1px solid #e2e9dc;overflow-wrap:anywhere;max-width:450px}}th{{background:#254c3b;color:#fff}}.table{{overflow:auto}}code{{overflow-wrap:anywhere}}a{{color:#287650}}footer{{font-size:12px;color:#7f8f76;margin-top:40px}}@media print{{:root{{background:white}}main{{padding:0}}h2{{break-after:avoid}}tr{{break-inside:avoid}}}}</style></head><body><main>
    <p class="eyebrow">ROOF PV / REPRODUCIBLE IMAGE SURVEY</p><h1>PV recognition and review report</h1><div class="notice">{escape(note)} Model candidates are observations, not validated installations or registry identities.</div>
    <div class="stats"><div>Active candidates<strong>{len(features):,}</strong></div><div>Confirmed PV<strong>{states.get('confirmed',0):,}</strong></div><div>Confirmed rooftop PV<strong>{rooftop:,}</strong></div><div>Missing target pixels<strong>{missing:,}</strong></div></div>
    <h2>Data and scope</h2><p>Run: <code>{run_id}</code>. Review revision: <b>r{snapshot['revision']}</b>. Capture dates: {captured}. The survey uses fixed original RGBI imagery and official administrative boundaries. Cadastral buildings provide spatial evidence; their current geometry is not asserted contemporaneous with the aerial imagery.</p>
    <p>Target pixel coverage: {valid:,} valid, {missing:,} missing. The scope includes image PV recognition, manual geometry review and GIS export. {scope_note}</p>{matching_section}
    <h2>Recognition method</h2><p>DeepLabV3+ / RegNetY-080 uses pinned RGBI and RGB checkpoints. Inputs are uint8, divided by 255; RGB uses ImageNet normalization and NIR uses mean 0.5 / standard deviation 0.25. Native windows are 256 pixels with 24-pixel overlap, sigmoid of output channel 1, arithmetic-mean blending and threshold 0.5. Four-neighbor components smaller than 25 pixels are removed only after cross-block stitching. Horizontal projected area is not module surface area.</p>
    <p>Municipal processing uses a world-anchored 232-pixel stride and bounded context blocks. This global window origin differs from the earlier crop-relative experiment. CPU/MPS probe results and runtime settings are retained in the inference provenance. Model scores are uncalibrated.</p>{model_table}
    <h2>Results and review</h2>{state_table}{category_table}<p>{overlap:,} active candidates have cadastral overlap evidence. Overlap is not a proof of roof mounting. Manual additions and merge/split descendants require explicit confirmation. Superseded objects and all actions remain in the audit history.</p>
    <h2>Quality and interpretation</h2><ul><li>Candidate counts do not measure precision or recall.</li><li>Independent, spatially held-out human annotation is required to establish local accuracy.</li><li>Inspect model-negative roofs through the checked-area workflow to discover missed PV.</li><li>Shadows, roof windows, dark roofs, small arrays and image dates can affect detections.</li><li>Shared-edge stitching preserves four-neighbor connectivity; diagonal point contacts are kept separate.</li><li>Both model outputs remain separate; model agreement does not automatically confirm a candidate.</li><li>Imagery newer than the frozen capture dates requires a new run; existing manual decisions are not silently migrated.</li></ul>
    <h2>GeoPackage delivery</h2><p>{repair_count} exported geometries required topology normalization. Repair flags, reasons and area deltas are stored in the layer attributes; source artifacts remain unchanged. Repairs exceeding the declared small-area tolerance are rejected.</p><p>All spatial layers use EPSG:3035. The file contains active candidates, confirmed PV, confirmed rooftop PV, superseded candidates, raw model observations, checked areas, survey coverage and contextual layers when available. Non-spatial tables contain the export metadata, full inference provenance and audit events.</p><p>Run-manifest SHA-256: <code>{escape(snapshot['manifest_sha256'])}</code><br>GeoPackage SHA-256: <code>{escape(export['sha256'])}</code></p>
    <h2>Source tile inventory</h2>{source_table}<footer>Imagery, administrative boundaries and cadastral data: © GeoBasis-DE/LGB, dl-de/by-2-0; derived crops, masks and overlays. Model weights: <a href="https://huggingface.co/Muemmel/solar-pv-segmentation-brandenburg">Muemmel/solar-pv-segmentation-brandenburg</a>, CC-BY-4.0. Generated from a transactional review snapshot.</footer></main></body></html>'''
    Path(output).write_text(content)
