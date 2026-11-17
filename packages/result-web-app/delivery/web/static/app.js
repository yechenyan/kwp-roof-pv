/* Result exploration; editable confidence never implies verified identity. */
"use strict";
const $ = (id) => document.getElementById(id);
const colors = { medium: "#417b61", low: "#aaa043", very_low: "#df8a36", unlocated: "#89968e" };
const state = { records: [], filtered: [], selected: null, shown: 60, markers: new Map(), api: false, buildings: null, arrays: null, map: null };
const number = (value, digits = 1) => value == null ? "Unavailable" : Number(value).toLocaleString("en-GB", { maximumFractionDigits: digits });
const text = (value) => value == null || value === "" ? "Unavailable" : String(value);
const escapeHTML = (value) => text(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const confidenceLabel = (value) => ({ medium: "Medium", low: "Low", very_low: "Very low", unlocated: "Unlocated" })[value] || value;
const badge = (level) => `<span class="badge ${level}">${confidenceLabel(level)}</span>`;

async function json(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Request failed (${response.status}): ${url}`);
  return response.json();
}

function filter() {
  const search = $("search").value.toLowerCase().trim();
  const municipality = $("municipality").value;
  const evidence = $("evidence").value;
  const confidence = $("confidence").value;
  const minPower = Number($("min-power").value || 0);
  state.filtered = state.records.filter((r) => {
    if ($("scope").value === "target" && !["active_building_under30", "active_building_exactly30"].includes(r.cohort)) return false;
    if (search && !`${r.solar_id} ${r.postcode || ""} ${r.location_id || ""}`.toLowerCase().includes(search)) return false;
    if (municipality && r.municipality !== municipality) return false;
    if (confidence && r.confidence !== confidence) return false;
    if ((r.power_kw || 0) < minPower) return false;
    if (evidence === "unlocated" && r.point) return false;
    if (evidence === "conflicts" && r.competing_registrations < 2 && !r.capacity_conflict) return false;
    if (evidence && !["unlocated", "conflicts"].includes(evidence) && r.association_status !== evidence) return false;
    return true;
  }).sort((a, b) => (b.score || 0) - (a.score || 0) || a.solar_id.localeCompare(b.solar_id));
  if (state.selected && !state.filtered.some((r) => r.solar_id === state.selected)) {
    state.selected = null;
    state.selectionLayer?.clearLayers();
    $("details").innerHTML = '<p class="eyebrow">INSTALLATION DETAILS</p><h2>Select a result</h2><p>Choose a filtered record to inspect its selected position, technical attributes and evidence.</p>';
  }
  state.shown = 60;
  renderList();
  renderMarkers();
}

function renderList() {
  $("count").textContent = number(state.filtered.length, 0);
  $("results-list").innerHTML = state.filtered.slice(0, state.shown).map((r) => `<button class="result-row ${state.selected === r.solar_id ? "selected" : ""}" data-id="${r.solar_id}" role="listitem"><strong>${r.solar_id}</strong><div class="row-meta"><span>${escapeHTML(r.municipality)} · ${number(r.power_kw)} kW</span></div><div class="row-meta" style="margin-top:7px">${badge(r.confidence)}<span>${r.score == null ? "No location" : `Score ${number(r.score)}`}</span></div></button>`).join("") || '<p class="reason">No results match these filters.</p>';
  $("more").hidden = state.shown >= state.filtered.length;
}

function renderMarkers() {
  if (!state.map) return;
  state.markerLayer.clearLayers();
  state.markers.clear();
  let located = 0;
  for (const r of state.filtered) {
    if (!r.point) continue;
    located++;
    const marker = L.circleMarker([r.point[1], r.point[0]], { radius: r.solar_id === state.selected ? 8 : 4.5, weight: 1.3, color: "#fff", fillColor: colors[r.confidence] || colors.low, fillOpacity: .88 });
    marker.bindTooltip(`${r.solar_id} · ${number(r.power_kw)} kW · ${confidenceLabel(r.confidence)}`);
    marker.on("click", () => select(r.solar_id, false));
    marker.addTo(state.markerLayer);
    state.markers.set(r.solar_id, marker);
  }
  $("map-count").textContent = `${number(located, 0)} mapped / ${number(state.filtered.length, 0)} filtered records`;
}

function field(label, value) { return `<dt>${label}</dt><dd>${escapeHTML(value)}</dd>`; }

function select(id, fly = true) {
  const r = state.records.find((row) => row.solar_id === id);
  if (!r) return;
  state.selected = id;
  renderList();
  renderMarkers();
  state.selectionLayer.clearLayers();
  if (r.geometry) {
    L.geoJSON(r.geometry, { style: { color: colors[r.confidence] || colors.low, weight: 3, fillOpacity: .25 } }).addTo(state.selectionLayer);
    if (fly) state.map.fitBounds(state.selectionLayer.getBounds().pad(1.8), { maxZoom: 20 });
  }
  const status = r.association_status === "statistical_building_allocation" ? "STATISTICAL BUILDING ALLOCATION" : r.point ? "INFERRED IMAGE ASSOCIATION" : "UNLOCATED REGISTRY RECORD";
  $("details").innerHTML = `<p class="eyebrow">${status}</p><h2>${r.solar_id}</h2>${badge(r.confidence)}<div class="score">${r.score == null ? "—" : number(r.score)} <small>/ 100 compatibility</small></div><dl>${field("Municipality", r.municipality)}${field("Postcode", r.postcode)}${field("Gross power", `${number(r.power_kw, 3)} kW`)}${field("Modules", number(r.module_count, 0))}${field("Commissioned", r.commissioned)}${field("Main direction", r.main_direction)}${field("Secondary direction", r.secondary_direction)}${field("Main tilt", r.main_tilt)}${field("Capture dates", (r.capture_dates || []).join(" / "))}${field("Image area", r.observed_area_m2 == null ? "Not observed" : `${number(r.observed_area_m2)} m²`)}${field("Score gap", number(r.score_gap, 4))}${field("Near-best candidates", number(r.near_best_count, 0))}${field("Competing registrations", number(r.competing_registrations, 0))}${field("Selected site spread", r.location_cluster_diameter_m == null ? "No position" : `${number(r.location_cluster_diameter_m)} m`)}${field("Site spread limit", `${number(r.location_cluster_limit_m || 100, 0)} m (allocation rule)`)}</dl><h3>ASSOCIATION REASON</h3><p class="reason">${escapeHTML(r.reason)}</p>${r.capacity_conflict ? '<p class="reason error">Assigned power exceeds the broad area screen. This association has an unresolved capacity conflict.</p>' : ""}<h3>STORAGE AT REGISTRY LOCATION</h3><dl>${field("Net power", r.storage_power_kw == null ? "No supported link" : `${number(r.storage_power_kw, 3)} kW`)}${field("Usable capacity", r.storage_capacity_kwh == null ? "No supported link" : `${number(r.storage_capacity_kwh, 3)} kWh`)}${field("Storage units", r.storage_unit_ids.length)}${field("Shared relationship", r.storage_shared ? "Yes - do not sum across PV rows" : "No shared link in this snapshot")}</dl><h3>TECHNICAL ATTRIBUTES</h3><details><summary>View retained MaStR fields</summary><dl>${Object.entries(r.mastr_attributes).map(([k, v]) => field(k, v)).join("")}</dl></details><h3>CONFIDENCE & REASON</h3><p style="font-size:10px;color:#6d7b72">Edits annotate the selected result; they do not verify identity. The PDF describes the original generated snapshot.</p><label>Confidence<select id="edit-confidence">${["medium", "low", "very_low", "unlocated"].map((v) => `<option value="${v}" ${v === r.confidence ? "selected" : ""}>${confidenceLabel(v)}</option>`).join("")}</select></label><label>Reason<textarea id="edit-reason" maxlength="4000">${escapeHTML(r.reason)}</textarea></label><button id="save-annotation" ${!state.api ? "disabled" : ""}>Save annotation</button><p class="status" id="annotation-status">${state.api ? "Edits are saved to the local result database." : "Read-only static mode. Start the result server to save edits."}</p><a href="${state.api ? "/api/export" : "../geodata/roof-pv-results.gpkg"}" download style="font-size:11px">${state.api ? "Export with current annotations ↓" : "Download original GeoPackage ↓"}</a>`;
  $("save-annotation").addEventListener("click", () => save(r));
}

async function save(r) {
  const button = $("save-annotation");
  button.disabled = true;
  try {
    const reason = $("edit-reason").value.trim();
    if (reason.length < 10) throw new Error("Please enter a reason of at least 10 characters.");
    const confidence = $("edit-confidence").value;
    const response = await fetch(`/api/annotations/${encodeURIComponent(r.solar_id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: r.annotation_version || 0, confidence, reason }) });
    if (!response.ok) { const body = await response.json(); throw new Error(body.detail || "Unable to save."); }
    const data = await response.json();
    Object.assign(r, { reason, confidence, annotation_version: data.version });
    $("annotation-status").textContent = "Saved. The edited GeoPackage includes this annotation.";
    filter();
  } catch (error) { $("annotation-status").textContent = error.message; }
  finally { button.disabled = false; }
}

function fit() {
  const points = state.filtered.filter((r) => r.point).map((r) => [r.point[1], r.point[0]]);
  if (points.length) state.map.fitBounds(L.latLngBounds(points).pad(.06), { maxZoom: 17 });
}

function drawContext() {
  const zoom = state.map.getZoom();
  const bounds = state.map.getBounds().pad(.1);
  const intersects = (f) => { const b = f.bbox; return b && b[0] <= bounds.getEast() && b[2] >= bounds.getWest() && b[1] <= bounds.getNorth() && b[3] >= bounds.getSouth(); };
  state.buildingLayer.clearLayers(); state.arrayLayer.clearLayers();
  if (zoom >= 16 && state.buildings && $("show-buildings").checked) {
    state.buildingLayer.addData(state.buildings.features.filter(intersects).slice(0, 2500));
  }
  if (zoom >= 16 && state.arrays && $("show-arrays").checked) {
    state.arrayLayer.addData(state.arrays.features.filter(intersects).slice(0, 1500));
  }
}

async function start() {
  const [metadata, records] = await Promise.all([json("summary.json"), json("api/results").then((r) => { state.api = true; return r; }).catch(() => json("records.json"))]);
  state.records = records;
  $("registry-date").textContent = metadata.registry_snapshot_date;
  $("delivery-version").textContent = `${metadata.algorithm} · ${metadata.registry_snapshot_date}`;
  const s = metadata.summary;
  $("metrics").innerHTML = [[s.registry_records,"Registry records","All types and statuses retained"],[s.image_candidates,"Image regions","RGB / RGBI model observations"],[s.states.inferred_image_association || 0,"Image associations","Highest technical score selected"],[s.states.statistical_building_allocation || 0,"Statistical allocations","Post-capture, very low confidence"],[s.unique_storage_capacity_kwh,"Storage capacity · kWh","Unique supported storage-system IDs"]].map(([value, title, sub]) => `<div class="metric"><span>${title}</span><strong>${number(value, 0)}</strong><small>${sub}</small></div>`).join("");
  state.map = L.map("map", { preferCanvas: true, maxZoom: 21 }).setView([52.686, 13.287], 13);
  const wmsOptions = { format:"image/png",transparent:false,version:"1.1.1",maxZoom:21,attribution:"© GeoBasis-DE/LGB, dl-de/by-2-0" };
  const layers = {
    rgb: L.tileLayer.wms("https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms", { ...wmsOptions, layers:"bebb_dop20c" }),
    cir: L.tileLayer.wms("https://isk.geobasis-bb.de/mapproxy/dop20cir/service/wms", { ...wmsOptions, layers:"bb_dop20cir" }),
    osm: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom:19, attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>' }),
  };
  state.base = layers.rgb.addTo(state.map);
  for (const layer of [layers.rgb, layers.cir]) layer.on("tileerror", () => { $("map-status").textContent = "Some WMS tiles are unavailable. Retry or select OpenStreetMap; result geometry remains available."; });
  state.boundaryLayer = L.geoJSON(null, { style:{color:"#153e31",weight:1.5,fillOpacity:0,dashArray:"5 5"},interactive:false }).addTo(state.map);
  state.buildingLayer = L.geoJSON(null, { style:{color:"#e7e9c2",weight:1,fillOpacity:0},interactive:false }).addTo(state.map);
  state.arrayLayer = L.geoJSON(null, { style:{color:"#71c9d4",weight:1.5,fillOpacity:.08},interactive:false }).addTo(state.map);
  state.markerLayer = L.layerGroup().addTo(state.map);
  state.selectionLayer = L.featureGroup().addTo(state.map);
  state.map.on("moveend", drawContext);
  $("basemap").addEventListener("change", () => { state.map.removeLayer(state.base); state.base = layers[$("basemap").value].addTo(state.map); state.base.bringToBack(); });
  $("fit").addEventListener("click", fit);
  for (const id of ["municipality","evidence","confidence","scope","min-power"]) $(id).addEventListener("change", filter);
  let searchTimer; $("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(filter, 160); });
  $("more").addEventListener("click", () => { state.shown += 60; renderList(); });
  $("results-list").addEventListener("click", (e) => { const row = e.target.closest("[data-id]"); if (row) select(row.dataset.id); });
  for (const id of ["show-buildings","show-arrays"]) $(id).addEventListener("change", drawContext);
  filter(); fit();
  json("municipalities.geojson").then((data) => state.boundaryLayer.addData(data)).catch(console.error);
  Promise.all([json("buildings.geojson"), json("arrays.geojson")]).then(([buildings, arrays]) => {
    for (const dataset of [buildings, arrays]) for (const f of dataset.features) {
      const bbox = [Infinity, Infinity, -Infinity, -Infinity];
      const visit = (coords) => { if (typeof coords[0] === "number") { bbox[0] = Math.min(bbox[0], coords[0]); bbox[1] = Math.min(bbox[1], coords[1]); bbox[2] = Math.max(bbox[2], coords[0]); bbox[3] = Math.max(bbox[3], coords[1]); } else for (const child of coords) visit(child); };
      visit(f.geometry.coordinates); f.bbox = bbox;
    }
    state.buildings = buildings; state.arrays = arrays; drawContext();
  }).catch((error) => { $("map-status").textContent = error.message; });
}
start().catch((error) => { $("map-status").textContent = error.message; $("results-list").innerHTML = '<p class="reason error">Unable to load the result delivery. Serve this directory over HTTP.</p>'; });
