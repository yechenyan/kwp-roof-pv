/* Result exploration; editable confidence never implies verified identity. */
"use strict";
const $ = (id) => document.getElementById(id);
const colors = { medium: "#417b61", low: "#aaa043", very_low: "#df8a36", unlocated: "#89968e" };
const observedStyle = { color: "#064dc4", fillColor: "#28d9ff", weight: 2, fillOpacity: .75 };
const candidateOutline = { color: "#915200", fillColor: "#ffd24d", weight: 2.5, fillOpacity: .48, dashArray: "5 3" };
const activeCandidateOutline = { color: "#943100", fillColor: "#ff9c30", weight: 4, fillOpacity: .72, dashArray: "6 3" };
const jointProposalOutline = { color: "#6d23a8", fillColor: "#c781ff", weight: 3.5, fillOpacity: .65 };
const candidateLabelSize = [32, 18];
const state = { records: null, recordsRequest: null, filtered: [], selected: null, shown: 60, markers: new Map(), api: false,
  buildings: null, observed: new Map(), postcodes: [], postcodeGeometry: new Map(), candidateBounds: null, candidateMap: null,
  rankingIndex: null, rankingShard: null, snapshot: null, map: null };
const number = (value, digits = 1) => value == null ? "Unavailable" : Number(value).toLocaleString("en-GB", { maximumFractionDigits: digits });
const pointsLabel = (value) => `${value > 0 ? "+" : ""}${number(value, 2)}`;
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
  if (!state.records) {
    renderMarkers();
    return;
  }
  const search = $("search").value.toLowerCase().trim();
  const municipality = $("municipality").value;
  const evidence = $("evidence").value;
  const confidence = $("confidence").value;
  const minPower = Number($("min-power").value || 0);
  state.filtered = state.records.filter((r) => {
    if ($("scope").value === "target" && r.cohort !== "active_building_under30") return false;
    if (search && !`${r.solar_id} ${r.postcode || ""} ${r.location_id || ""}`.toLowerCase().includes(search)) return false;
    if (municipality && r.municipality !== municipality) return false;
    if (confidence && r.confidence !== confidence) return false;
    if ((r.power_kw || 0) < minPower) return false;
    if (evidence === "low_compatibility" && r.candidate_status !== evidence) return false;
    if (evidence === "joint_proposal" && !r.joint_proposal) return false;
    if (evidence && !["low_compatibility", "joint_proposal"].includes(evidence) && r.association_status !== evidence) return false;
    return true;
  }).sort((a, b) => (b.score || 0) - (a.score || 0) || a.solar_id.localeCompare(b.solar_id));
  if (state.selected && !state.filtered.some((r) => r.solar_id === state.selected)) {
    state.selected = null;
    clearSelection();
    $("details").innerHTML = '<p class="eyebrow">INSTALLATION DETAILS</p><h2>Select a result</h2><p>Choose a filtered record to inspect its selected position, technical attributes and evidence.</p>';
  }
  state.shown = 60;
  renderList();
  renderMarkers();
}

function renderList() {
  $("count").textContent = number(state.filtered.length, 0);
  $("results-list").innerHTML = state.filtered.slice(0, state.shown).map((r) => `<button class="result-row ${state.selected === r.solar_id ? "selected" : ""}" data-id="${r.solar_id}" role="listitem"><strong>${r.solar_id}${r.joint_proposal ? ' <small class="joint-badge">Joint proposal</small>' : ""}</strong><div class="row-meta"><span>${escapeHTML(r.municipality)} · ${number(r.power_kw)} kW</span></div><div class="row-meta" style="margin-top:7px">${badge(r.confidence)}<span>${r.score == null ? "No location" : `Score ${number(r.score, 2)}${r.association_status === "building_candidates" ? " pts" : ""}`}</span></div></button>`).join("") || '<p class="reason">No results match these filters.</p>';
  $("more").hidden = state.shown >= state.filtered.length;
}

function renderMarkers() {
  if (!state.map) return;
  state.markerLayer.clearLayers();
  state.markers.clear();
  if ($("view").value === "inventory") {
    $("map-count").textContent = `${number(state.observed.size, 0)} observed PV buildings`;
    return;
  }
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
  $("map-count").textContent = `${number(located, 0)} coordinate proposals / ${number(state.filtered.length, 0)} filtered records`;
}

function field(label, value) { return `<dt>${escapeHTML(label)}</dt><dd>${escapeHTML(value)}</dd>`; }

const postalKey = ({ municipality, postcode }) => `${municipality}:${postcode}`;
const area = (value) => value == null ? "Unavailable" : `${number(value, 2)} m²`;
const areaLabel = (method) => ({
  roof_parallel_estimate: "Roof-parallel estimate",
  projected_area_fallback: "Projected area with flat or incomplete roof support",
  no_lod2_id: "Projected area with no matching LoD2 ID",
  footprint_disagreement: "Projected area due to a footprint mismatch",
  no_usable_roof_intersection: "Projected area with no usable roof intersection",
  roof_projection_disagreement: "Projected area due to inconsistent roof overlap",
})[method] || "Projected area";

function showPostcode(row) {
  const feature = state.postcodeGeometry.get(postalKey(row));
  clearSelection();
  if (feature) {
    const selected = L.geoJSON(feature, { interactive: false, style: { color: "#826227", weight: 3, fillOpacity: 0 } }).addTo(state.selectionLayer);
    state.map.fitBounds(selected.getBounds().pad(.08), { maxZoom: 16 });
  }
  $("details").innerHTML = `<p class="eyebrow">MaStR POSTCODE TOTAL</p><h2>${escapeHTML(row.postcode || "No postcode")} · ${escapeHTML(row.municipality)}</h2><dl>` +
    `${field("Active anonymous PV <30 kW", number(row.target_count, 0))}${field("Target capacity", `${number(row.target_power_kw, 3)} kW`)}` +
    `${field("Targets after capture", number(row.post_capture_count, 0))}` +
    `${field("All registry records", number(row.registry_count, 0))}${field("All registered capacity", `${number(row.registry_power_kw, 3)} kW`)}</dl>` +
    (feature ? "" : '<p class="reason">This registry postcode total has no matching mapped area.</p>');
}

function clearSelection() {
  state.selectionLayer?.clearLayers();
  state.candidateBounds = null;
  state.candidateMap = null;
  $("candidate-legend").hidden = true;
  $("joint-proposal-legend").hidden = true;
  $("candidate-label-control").hidden = true;
}

async function showBuilding(feature) {
  const roof = feature.properties;
  clearSelection();
  const outline = L.geoJSON(feature, { interactive: false, style: { ...observedStyle, weight: 4 } }).addTo(state.selectionLayer);
  state.map.fitBounds(outline.getBounds().pad(2), { maxZoom: 20 });
  $("details").innerHTML = `<p class="eyebrow">IMAGE OBSERVATION</p><h2>${escapeHTML(roof.building_id)}</h2><dl>` +
    `${field("Postcode", roof.postcode)}${field("Municipality", roof.municipalities.join(" / "))}` +
    `${field("Capture dates", roof.capture_dates.join(" / "))}${field("Projected PV area", area(roof.projected_area_m2))}` +
    `${field("Estimated module area", area(roof.roof_parallel_area_m2))}${field("Area method", areaLabel(roof.area_method))}` +
    `${field("Source array groups", roof.group_ids.length)}${field("Observed roof faces", roof.roof_face_count)}</dl>` +
    (roof.roof_face_count ? '<h3>LoD2 roof faces</h3><div id="face-details" role="status">Loading roof faces</div>' : "");
  if (!roof.roof_face_count) return;
  const detail = $("face-details");
  state.roofFaces ??= json("roof-faces.geojson").catch((error) => { state.roofFaces = null; throw error; });
  try {
    const data = await state.roofFaces;
    const faces = data.features.filter((face) => face.properties.building_id === roof.building_id);
    detail.innerHTML = faces.map(({ properties: face }) => `<p class="reason">Face ${face.face_number} · ${area(face.projected_area_m2)} projected` +
      `<br>${number(face.slope_deg)}° slope${face.azimuth_deg == null ? "" : ` · ${number(face.azimuth_deg)}° azimuth`}` +
      `${face.roof_parallel_area_m2 == null ? "" : ` · ${area(face.roof_parallel_area_m2)} estimated`}</p>`).join("");
    if (detail.isConnected) L.geoJSON(faces, { interactive: false, style: { color: "#163be2", weight: 2, fillOpacity: .14 } }).addTo(state.selectionLayer);
  } catch {
    detail.textContent = "Roof face details are unavailable.";
  }
}

function select(id, fly = true) {
  const r = state.records.find((row) => row.solar_id === id);
  if (!r) return;
  state.selected = id;
  renderList();
  renderMarkers();
  clearSelection();
  if (r.geometry) {
    L.geoJSON(r.geometry, { style: { color: colors[r.confidence] || colors.low, weight: 3, fillOpacity: .25 } }).addTo(state.selectionLayer);
    if (fly) state.map.fitBounds(state.selectionLayer.getBounds().pad(1.8), { maxZoom: 20 });
  }
  const status = r.candidate_status === "low_compatibility" ? "BUILDING REVIEW ONLY"
    : r.association_status === "building_candidates" ? "BUILDING CANDIDATES"
    : r.point ? "PUBLIC-COORDINATE PROPOSAL" : "UNLOCATED REGISTRY RECORD";
  $("details").innerHTML = `<p class="eyebrow">${status}</p><h2>${r.solar_id}</h2>${badge(r.confidence)}<div class="score">${r.score == null ? "—" : number(r.score, r.association_status === "building_candidates" ? 2 : 1)} <small>${r.association_status === "building_candidates" ? "best candidate points" : "/ 100 compatibility"}</small></div><dl>${field("Municipality", r.municipality)}${field("Postcode", r.postcode)}${field("Gross power", `${number(r.power_kw, 3)} kW`)}${field("Modules", number(r.module_count, 0))}${field("Commissioned", r.commissioned)}${field("Main direction", r.main_direction)}${field("Secondary direction", r.secondary_direction)}${field("Main tilt", r.main_tilt)}${field("Capture dates", (r.capture_dates || []).join(" / "))}${field("Image area", r.observed_area_m2 == null ? "Not observed" : `${number(r.observed_area_m2)} m²`)}${field("Score gap", number(r.score_gap, 4))}${field("Near-best candidates", number(r.near_best_count, 0))}${field("Competing registrations", number(r.competing_registrations, 0))}${field("Selected site spread", r.location_cluster_diameter_m == null ? "No position" : `${number(r.location_cluster_diameter_m)} m`)}${field("Site spread limit", `${number(r.location_cluster_limit_m || 100, 0)} m (allocation rule)`)}</dl><h3>ASSOCIATION REASON</h3><p class="reason">${escapeHTML(r.reason)}</p>${r.capacity_conflict ? '<p class="reason error">Assigned power exceeds the broad area screen. This association has an unresolved capacity conflict.</p>' : ""}<h3>STORAGE AT REGISTRY LOCATION</h3><dl>${field("Net power", r.storage_power_kw == null ? "No supported link" : `${number(r.storage_power_kw, 3)} kW`)}${field("Usable capacity", r.storage_capacity_kwh == null ? "No supported link" : `${number(r.storage_capacity_kwh, 3)} kWh`)}${field("Storage units", r.storage_unit_ids.length)}${field("Shared relationship", r.storage_shared ? "Yes - do not sum across PV rows" : "No shared link in this snapshot")}</dl><h3>TECHNICAL ATTRIBUTES</h3><details><summary>View retained MaStR fields</summary><dl>${Object.entries(r.mastr_attributes).map(([k, v]) => field(k, v)).join("")}</dl></details><h3>CONFIDENCE & REASON</h3><p style="font-size:10px;color:#6d7b72">Edits annotate the selected result; they do not verify identity. The PDF describes the original generated snapshot.</p><label>Confidence<select id="edit-confidence">${["medium", "low", "very_low", "unlocated"].map((v) => `<option value="${v}" ${v === r.confidence ? "selected" : ""}>${confidenceLabel(v)}</option>`).join("")}</select></label><label>Reason<textarea id="edit-reason" maxlength="4000">${escapeHTML(r.reason)}</textarea></label><button id="save-annotation" ${!state.api ? "disabled" : ""}>Save annotation</button><p class="status" id="annotation-status">${state.api ? "Edits are saved to the local result database." : "Read-only static mode. Start the result server to save edits."}</p><a href="${state.api ? "/api/export" : "roof-pv-results.gpkg"}" download style="font-size:11px">${state.api ? "Export with current annotations ↓" : "Download original GeoPackage ↓"}</a>`;
  $("save-annotation").addEventListener("click", () => save(r));
  if (r.association_status === "building_candidates") showCandidateReview(r, fly);
}

async function loadCandidateRanking(record) {
  state.rankingIndex ??= json("candidate-rankings/index.json").catch((error) => { state.rankingIndex = null; throw error; });
  const index = await state.rankingIndex;
  if (index.snapshot !== state.snapshot || !index.shards.includes(record.ranking_shard) || !Array.isArray(index.array_options)) {
    throw new Error("The candidate data belongs to a different atlas build. Reload the page.");
  }
  const name = record.ranking_shard;
  if (state.rankingShard?.name !== name) {
    const pending = json(`candidate-rankings/${name}`).catch((error) => {
      if (state.rankingShard?.pending === pending) state.rankingShard = null;
      throw error;
    });
    state.rankingShard = { name, pending };
  }
  const shard = await state.rankingShard.pending;
  const entries = shard.records[record.solar_id];
  if (shard.snapshot !== index.snapshot || !Array.isArray(entries) || entries.length !== record.candidate_pool_size) {
    throw new Error("The complete candidate ranking is unavailable for this record.");
  }
  const ranking = entries.map(([i, area, direction, tilt, option, method]) => ({
    building_id: index.building_ids[i], score: (area + direction + tilt) / index.score_scale,
    area: area / index.score_scale, direction: direction / index.score_scale, tilt: tilt / index.score_scale,
    array: index.array_options[i][option], areaMode: index.area_modes[method],
  }));
  if (record.alternatives.some((roof, i) => roof.building_id !== ranking[i]?.building_id ||
      roof.score !== ranking[i].score)) {
    throw new Error("The record and candidate ranking come from different atlas builds. Reload the page.");
  }
  const qualified = ranking.filter((item) => item.score >= index.threshold);
  if (qualified.length !== record.candidate_qualified_count) {
    throw new Error("The candidate threshold belongs to a different atlas build. Reload the page.");
  }
  const proposal = record.joint_proposal;
  if (proposal) {
    const candidate = qualified[proposal.rank - 1];
    const roofIndex = entries[proposal.rank - 1]?.[0];
    const array = index.array_options[roofIndex]?.[proposal.array_option];
    if (candidate?.building_id !== proposal.building_id || !array || proposal.score < index.threshold || proposal.score > candidate.score) {
      throw new Error("The joint proposal belongs to a different atlas build. Reload the page.");
    }
    const [area, direction, tilt] = proposal.points.map((point) => point / index.score_scale);
    candidate.proposal = { area, direction, tilt, array, areaMode: proposal.area_method, score: proposal.score };
  }
  return { visible: qualified.length ? qualified : ranking.slice(0, index.fallback_count), total: ranking.length,
    threshold: index.threshold, fallback: qualified.length === 0 };
}

function showCandidateReview(record, fly) {
  const list = document.createElement("section");
  list.className = "candidate-review";
  $("details").querySelector("dl").before(list);
  async function load() {
    list.innerHTML = `<h3>Building candidates</h3><p role="status">Loading all ${number(record.candidate_pool_size, 0)} candidates.</p>`;
    try {
      const pool = await loadCandidateRanking(record);
      if (!list.isConnected || state.selected !== record.solar_id) return;
      if (pool.fallback) renderCandidateReviewFallback(list, pool, record, fly);
      else renderCandidatePool(list, pool, record, fly);
    } catch (error) {
      list.innerHTML = `<h3>Building candidates</h3><p class="reason error" role="alert">${escapeHTML(error.message)}</p>` +
        '<button type="button" data-retry-ranking>Retry candidate ranking</button>';
      list.querySelector("[data-retry-ranking]").addEventListener("click", load, { once: true });
    }
  }
  load();
}

function renderCandidateReviewFallback(list, pool, record, fly) {
  list.innerHTML = '<h3>No qualifying building candidates</h3>' +
    `<p>None of the ${number(pool.total, 0)} roofs scored at least ${number(pool.threshold)} points.</p>` +
    `<details class="candidate-fallback"><summary>Review the ${number(pool.visible.length, 0)} highest-scoring roofs</summary><div></div></details>`;
  const review = list.querySelector("details");
  const content = review.querySelector("div");
  review.addEventListener("toggle", () => {
    if (!review.isConnected) return;
    if (review.open) renderCandidatePool(content, pool, record, fly);
    else {
      clearSelection();
      content.replaceChildren();
    }
  });
}

function renderCandidatePool(list, { visible: ranking, total, threshold, fallback }, record, fly) {
  const proposalIndex = ranking.findIndex((candidate) => candidate.proposal);
  list.innerHTML = (fallback ? '<p>Review only. These scores are below the candidate threshold.</p>' :
    `<h3>${number(ranking.length, 0)} building candidates</h3>` +
    `<p>${number(ranking.length, 0)} of ${number(total, 0)} roofs scored at least ${number(threshold)} points.</p>` +
    (proposalIndex >= 0 ? `<p class="joint-summary"><button type="button" data-show-proposal>Show joint proposal · rank ${number(proposalIndex + 1, 0)}</button></p>` :
      '<p class="reason">No joint proposal fits the shared array and roof constraints.</p>')) +
    '<form class="candidate-jump"><label for="candidate-rank">Go to rank</label>' +
    `<input id="candidate-rank" type="number" min="1" max="${ranking.length}" required><button type="submit">Go</button></form>` +
    '<div class="candidate-rows" role="group" aria-label="Ranked building candidates"></div>' +
    '<div class="candidate-detail" aria-live="polite"></div>';
  const rows = list.querySelector(".candidate-rows");
  const detail = list.querySelector(".candidate-detail");
  rows.innerHTML = ranking.map((candidate, index) =>
    `<button type="button" data-index="${index}" aria-pressed="false"${candidate.proposal ? ' class="joint-proposal-row"' : ""}>` +
    `<span>${number(index + 1, 0)}. ${escapeHTML(candidate.building_id)}` +
    `${candidate.proposal ? '<small class="joint-badge">Joint proposal</small>' : ""}</span><strong>${pointsLabel(candidate.score)}</strong></button>`
  ).join("");
  const roofs = ranking.map((candidate) => state.observed.get(candidate.building_id));
  const outlines = new Map();
  const candidateStyle = (index, active = false) => index === proposalIndex
    ? { ...jointProposalOutline, weight: active ? 5 : jointProposalOutline.weight }
    : active ? activeCandidateOutline : { ...candidateOutline, fillOpacity: fallback ? .24 : .48 };
  const overlay = L.geoJSON(roofs.flatMap((feature, i) => feature ? [{ ...feature, properties: { index: i } }] : []), {
    style: (feature) => candidateStyle(feature.properties.index),
    onEachFeature: (feature, layer) => {
      const index = feature.properties.index;
      outlines.set(index, layer);
      layer.on("click", () => previewCandidate(index, true));
    },
  }).addTo(state.selectionLayer);
  const bounds = overlay.getBounds();
  const dots = new Map();
  const labels = L.layerGroup().addTo(state.selectionLayer);
  const candidateMap = { dots, labels, active: null, proposalIndex };
  const dotIcon = L.divIcon({ className: "candidate-map-dot", iconSize: [14, 14] });
  const proposalIcon = L.divIcon({ className: "candidate-map-dot joint-proposal-dot", iconSize: [18, 18] });
  state.candidateMap = candidateMap;
  for (const [index, outline] of outlines) {
    const proposal = ranking[index].proposal;
    const title = `Rank ${number(index + 1, 0)} · ${pointsLabel(proposal?.score ?? ranking[index].score)} points` +
      (proposal ? " · joint proposal" : fallback ? " · review only" : "");
    const dot = L.marker(outline.getBounds().getCenter(), { alt: title, icon: proposal ? proposalIcon : dotIcon,
      zIndexOffset: proposal ? 500 : 0, keyboard: false, riseOnHover: true })
      .bindTooltip(title, { direction: "top", offset: [0, -25] })
      .on("click", () => previewCandidate(index, true))
      .addTo(state.selectionLayer);
    dots.set(index, dot);
  }
  if (bounds.isValid()) {
    state.candidateBounds = bounds;
    $("candidate-legend").textContent = `${number(dots.size, 0)} ${fallback ? "review-only candidates" : "candidate markers (unverified)"}`;
    $("candidate-legend").hidden = false;
    $("joint-proposal-legend").hidden = proposalIndex < 0;
    $("candidate-label-control").hidden = false;
  }

  function previewCandidate(index, move) {
    const candidate = ranking[index];
    const previous = candidateMap.active;
    if (previous !== null && previous !== index) {
      rows.children[previous].setAttribute("aria-pressed", "false");
      outlines.get(previous)?.setStyle(candidateStyle(previous));
      const previousDot = dots.get(previous);
      previousDot?.getElement().classList.remove("active");
      previousDot?.setZIndexOffset(previous === proposalIndex ? 500 : 0);
    }
    candidateMap.active = index;
    const row = rows.children[index];
    row.setAttribute("aria-pressed", "true");
    const bottom = row.offsetTop + row.offsetHeight - rows.clientHeight;
    rows.scrollTop = Math.max(bottom, Math.min(rows.scrollTop, row.offsetTop));

    const outline = outlines.get(index);
    if (!outline) {
      layoutCandidateLabels();
      detail.textContent = `Candidate ${number(index + 1, 0)} has no roof geometry in this atlas.`;
      return;
    }
    outline.setStyle(candidateStyle(index, true)).bringToFront();
    const dot = dots.get(index);
    dot.getElement().classList.add("active");
    dot.setZIndexOffset(state.map.getSize().y + (index === proposalIndex ? 501 : 0));
    if (move) state.map.fitBounds(outline.getBounds().pad(2), { maxZoom: 20, animate: false });
    else if (!fly) layoutCandidateLabels();
    const evidence = candidate.proposal ?? candidate;
    const method = { roof_parallel: "LoD2 roof-parallel area", projected: "Projected area and registry tilt range" }[evidence.areaMode];
    const expected = { roof_parallel: record.candidate_registered_area_m2, projected: record.candidate_projected_area_m2 }[evidence.areaMode];
    const measured = { roof_parallel: evidence.array.parallel_m2, projected: evidence.array.projected_m2 }[evidence.areaMode];
    detail.innerHTML = `<dl>${field("Rank", number(index + 1, 0))}${field("Candidate score", pointsLabel(candidate.score))}` +
      (candidate.proposal ? `${field("Joint proposal score", pointsLabel(evidence.score))}${field("Identity", "Unverified")}` : "") +
      `${field("Candidate threshold", `${number(threshold)} points`)}` +
      `${field("Area points", pointsLabel(evidence.area))}${field("Direction points", pointsLabel(evidence.direction))}` +
      `${field("Tilt points", pointsLabel(evidence.tilt))}${field("Timing points", "0 · no independent installation dates")}` +
      `${field("Registration area source", { modules: "Module count", power: "Power estimate" }[record.candidate_area_source])}` +
      `${field("Comparison method", method)}${field("Registry area estimate", area(expected))}${field("Building PV area", area(measured))}` +
      `${field("Source groups merged", evidence.array.groups.length)}</dl>`;
  }

  rows.addEventListener("click", (event) => {
    const button = event.target.closest("[data-index]");
    if (button) previewCandidate(Number(button.dataset.index), true);
  });
  list.querySelector(".candidate-jump").addEventListener("submit", (event) => {
    event.preventDefault();
    previewCandidate($("candidate-rank").valueAsNumber - 1, true);
  });
  list.querySelector("[data-show-proposal]")?.addEventListener("click", () => previewCandidate(proposalIndex, true));
  previewCandidate(Math.max(0, proposalIndex), false);
  if (fly && state.candidateBounds) fit();
}

function layoutCandidateLabels() {
  if (!state.candidateMap) return;
  const { dots, labels, active, proposalIndex } = state.candidateMap;
  labels.clearLayers();
  const placed = [];
  const size = state.map.getSize();
  const showAll = $("show-candidate-labels").checked;
  const [width, height] = candidateLabelSize;
  const iconAnchor = [width / 2, height + 6];

  function addLabel(index, dot) {
    const center = dot.getLatLng();
    const point = state.map.latLngToContainerPoint(center);
    if (point.x < 0 || point.x > size.x || point.y < 0 || point.y > size.y) return;
    if (!showAll && placed.some((other) => Math.abs(other.x - point.x) < width + 4 && Math.abs(other.y - point.y) < height + 5)) return;
    placed.push(point);
    const selected = index === active;
    const icon = L.divIcon({ className: `candidate-map-pin${selected ? " active" : ""}${index === proposalIndex ? " joint-proposal-pin" : ""}`, html: String(index + 1),
      iconSize: candidateLabelSize, iconAnchor });
    L.marker(center, { pane: "candidate-labels", interactive: false, keyboard: false, zIndexOffset: selected ? size.y : 0, icon }).addTo(labels);
  }
  if (dots.has(active)) addLabel(active, dots.get(active));
  if (proposalIndex !== active && dots.has(proposalIndex)) addLabel(proposalIndex, dots.get(proposalIndex));
  for (const [index, dot] of dots) if (index !== active && index !== proposalIndex) addLabel(index, dot);
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
  if (state.candidateBounds) {
    state.map.fitBounds(state.candidateBounds, { padding: [60, 60], maxZoom: 19, animate: false });
    return;
  }
  const points = $("view").value === "review" ? state.filtered.filter((r) => r.point).map((r) => [r.point[1], r.point[0]]) : [];
  if (points.length) state.map.fitBounds(L.latLngBounds(points).pad(.06), { maxZoom: 17 });
  else if (state.boundaryLayer.getBounds().isValid()) state.map.fitBounds(state.boundaryLayer.getBounds().pad(.05));
}

function drawContext() {
  layoutCandidateLabels();
  const zoom = state.map.getZoom();
  const bounds = state.map.getBounds().pad(.1);
  const intersects = (f) => { const b = f.bbox; return b && b[0] <= bounds.getEast() && b[2] >= bounds.getWest() && b[1] <= bounds.getNorth() && b[3] >= bounds.getSouth(); };
  state.buildingLayer.clearLayers();
  if (zoom >= 16 && state.buildings && $("show-buildings").checked) {
    state.buildingLayer.addData(state.buildings.features.filter(intersects).slice(0, 2500));
  }
  for (const [id, layer] of [["show-arrays", state.arrayLayer], ["show-postcodes", state.postcodeLayer]]) {
    if ($(id).checked) layer.addTo(state.map); else layer.remove();
  }
}

async function fetchReviewRecords() {
  const response = await fetch("api/results");
  if (response.status === 404 || response.status === 405) return json("records.json");
  if (!response.ok) throw new Error(`Registry review request failed (${response.status}).`);
  const records = await response.json();
  state.api = true;
  return records;
}

async function openRegistryReview() {
  if (state.records) {
    filter();
    return;
  }
  $("results-list").innerHTML = '<p class="reason" role="status">Loading registry records.</p>';
  $("more").hidden = true;
  state.recordsRequest ??= fetchReviewRecords();
  try {
    state.records = await state.recordsRequest;
    if ($("view").value === "review") filter();
  } catch (error) {
    if ($("view").value === "review") {
      $("results-list").innerHTML = `<p class="reason error" role="alert">${escapeHTML(error.message)}</p>` +
        '<button type="button" data-retry-review>Retry registry review</button>';
    }
  } finally {
    state.recordsRequest = null;
  }
}

async function start() {
  const [metadata, observations, postcodes, totals, municipalities] = await Promise.all([
    json("summary.json"),
    json("observed-pv.geojson"), json("postcodes.geojson"), json("postcode-totals.json"), json("municipalities.geojson"),
  ]);
  state.observed = new Map(observations.features.map((feature) => [feature.properties.building_id, feature]));
  state.postcodes = totals;
  state.snapshot = metadata.created_at;
  state.postcodeGeometry = new Map(postcodes.features.map((feature) => [postalKey(feature.properties), feature]));
  $("registry-date").textContent = metadata.registry_snapshot_date;
  $("capture-dates").textContent = metadata.capture_dates.join(" / ");
  $("delivery-version").textContent = `${metadata.algorithm} · ${metadata.registry_snapshot_date}`;
  const s = metadata.summary;
  $("metrics").innerHTML = [[s.observed_buildings,"PV-observed buildings","ALKIS building union"],
    [s.projected_pv_area_m2,"Projected PV area · m²","Observed horizontal area"],
    [s.roof_area_estimates,"LoD2 area estimates","Roof-parallel assumption"],
    [s.strict_under30_targets,"Anonymous PV records <30 kW","Registry target cohort"],
    [s.verified_identities,"Verified building links","Independent identity evidence"]
  ].map(([value, title, sub]) => `<div class="metric"><span>${title}</span><strong>${number(value, 0)}</strong><small>${sub}</small></div>`).join("");
  $("postcode-list").innerHTML = totals.map((row, index) => `<button class="result-row" type="button" data-postcode-index="${index}">` +
    `<strong>${escapeHTML(row.postcode || "No postcode")} · ${escapeHTML(row.municipality)}</strong>` +
    `<div class="row-meta"><span>${number(row.target_count, 0)} target records</span><span>${number(row.target_power_kw, 3)} kW</span></div></button>`).join("");
  state.map = L.map("map", { preferCanvas: true, maxZoom: 21, zoomAnimation: false }).setView([52.686, 13.287], 13);
  const wmsOptions = { format:"image/png",transparent:false,version:"1.1.1",maxZoom:21,attribution:"© GeoBasis-DE/LGB, dl-de/by-2-0" };
  const layers = {
    rgb: L.tileLayer.wms("https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms", { ...wmsOptions, layers:"bebb_dop20c" }),
    cir: L.tileLayer.wms("https://isk.geobasis-bb.de/mapproxy/dop20cir/service/wms", { ...wmsOptions, layers:"bb_dop20cir" }),
    osm: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom:19, attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>' }),
  };
  state.base = layers.rgb.addTo(state.map);
  for (const layer of [layers.rgb, layers.cir]) layer.on("tileerror", () => { $("map-status").textContent = "Some WMS tiles are unavailable. Retry or select OpenStreetMap; result geometry remains available."; });
  state.boundaryLayer = L.geoJSON(municipalities, { style:{color:"#153e31",weight:1.5,fillOpacity:0,dashArray:"5 5"},interactive:false }).addTo(state.map);
  state.map.createPane("postcodes").style.zIndex = 390;
  state.postcodeLayer = L.geoJSON(postcodes, { pane: "postcodes", style: { color: "#9b7338", weight: 1.5, fillOpacity: .09 },
    onEachFeature: (feature, layer) => layer.on("click", () => showPostcode(feature.properties)),
  }).addTo(state.map);
  state.buildingLayer = L.geoJSON(null, { style:{color:"#e7e9c2",weight:1,fillOpacity:0},interactive:false }).addTo(state.map);
  state.arrayLayer = L.geoJSON(observations, { style: observedStyle,
    onEachFeature: (feature, layer) => layer.on("click", () => showBuilding(feature)),
  }).addTo(state.map);
  state.markerLayer = L.layerGroup().addTo(state.map);
  state.selectionLayer = L.featureGroup().addTo(state.map);
  const candidateLabels = state.map.createPane("candidate-labels");
  candidateLabels.style.zIndex = 610;
  candidateLabels.style.pointerEvents = "none";
  state.map.on("moveend", drawContext);
  $("basemap").addEventListener("change", () => { state.map.removeLayer(state.base); state.base = layers[$("basemap").value].addTo(state.map); state.base.bringToBack(); });
  $("fit").addEventListener("click", fit);
  $("show-candidate-labels").addEventListener("change", layoutCandidateLabels);
  for (const id of ["municipality","evidence","confidence","scope","min-power"]) $(id).addEventListener("change", filter);
  let searchTimer; $("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(filter, 160); });
  $("more").addEventListener("click", () => { state.shown += 60; renderList(); });
  $("results-list").addEventListener("click", (event) => {
    if (event.target.closest("[data-retry-review]")) {
      openRegistryReview();
      return;
    }
    const row = event.target.closest("[data-id]");
    if (row) select(row.dataset.id);
  });
  $("postcode-list").addEventListener("click", (e) => {
    const button = e.target.closest("[data-postcode-index]");
    if (button) showPostcode(state.postcodes[Number(button.dataset.postcodeIndex)]);
  });
  $("view").addEventListener("change", () => {
    const review = $("view").value === "review";
    $("postcode-directory").hidden = review;
    $("review-directory").hidden = !review;
    state.selected = null;
    clearSelection();
    $("details").innerHTML = review ? "<h2>Candidate review</h2><p>Select a MaStR record to inspect its evidence.</p>"
      : "<h2>Inspect the inventory</h2><p>Select an outlined PV observation or a postcode.</p>";
    if (review) openRegistryReview(); else renderMarkers();
  });
  for (const id of ["show-buildings", "show-arrays", "show-postcodes"]) $(id).addEventListener("change", drawContext);
  filter(); fit();
  $("show-buildings").addEventListener("change", async () => {
    if (!$("show-buildings").checked || state.buildingsLoading || state.buildings) return;
    state.buildingsLoading = true;
    try {
      const buildings = await json("buildings.geojson");
      for (const f of buildings.features) {
      const bbox = [Infinity, Infinity, -Infinity, -Infinity];
      const visit = (coords) => { if (typeof coords[0] === "number") { bbox[0] = Math.min(bbox[0], coords[0]); bbox[1] = Math.min(bbox[1], coords[1]); bbox[2] = Math.max(bbox[2], coords[0]); bbox[3] = Math.max(bbox[3], coords[1]); } else for (const child of coords) visit(child); };
      visit(f.geometry.coordinates); f.bbox = bbox;
      }
      state.buildings = buildings;
      drawContext();
    } catch (error) {
      $("map-status").textContent = error.message;
    } finally {
      state.buildingsLoading = false;
    }
  });
}
start().catch((error) => { $("map-status").textContent = error.message; $("results-list").innerHTML = '<p class="reason error">Unable to load the result delivery. Serve this directory over HTTP.</p>'; });
