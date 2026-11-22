/* Display the published match for each assigned MaStR record. */
"use strict";

const byId = (id) => document.getElementById(id);
const number = (value, digits = 0) => new Intl.NumberFormat("en-GB", { maximumFractionDigits: digits }).format(value);
const kinds = {
  joint: { label: "Joint match", color: "#6c31a5" },
  coordinate: { label: "Public-coordinate association", color: "#086c92" },
};

function element(tag, content, className) {
  const node = document.createElement(tag);
  node.textContent = content;
  if (className) node.className = className;
  return node;
}

async function read(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`Could not load ${path}.`);
  return response.json();
}

function showDetails(match) {
  const data = match.properties;
  const kind = kinds[data.kind];
  const fields = [
    ["Municipality", data.municipality], ["Postcode", data.postcode],
    ["Registered power", data.power_kw == null ? null : `${number(data.power_kw, 3)} kW`],
    ["Commissioned", data.commissioned], ["Survey dates", data.capture_dates.join(" / ")],
    ["Building ID", data.building_id], ["Matched panel groups", data.group_count],
  ];
  const details = document.createElement("dl");
  for (const [label, value] of fields) {
    if (value == null || value === "") continue;
    details.append(element("dt", label), element("dd", String(value)));
  }
  const score = element("p", number(data.score, 2), "evidence-score");
  score.append(element("small", data.kind === "joint" ? "Evidence points" : "Coordinate compatibility / 100"));
  if (data.evidence_points) {
    for (const [label, points] of ["Area points", "Direction points", "Tilt points"].map(
      (label, i) => [label, data.evidence_points[i]],
    )) {
      details.append(element("dt", label), element("dd", number(points, 2)));
    }
  }
  const identity = data.identity_verified ? "Building identity verified." : "Building identity remains unverified.";
  byId("details").replaceChildren(
    element("p", kind.label, "detail-kind"), element("h2", data.solar_id),
    score, details, element("p", identity, "reason"),
  );
}

function attachBasemap(map) {
  const wms = { format: "image/png", transparent: false, version: "1.1.1", maxZoom: 21,
    attribution: "© GeoBasis-DE/LGB, dl-de/by-2-0" };
  const layers = {
    rgb: L.tileLayer.wms("https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms", { ...wms, layers: "bebb_dop20c" }),
    cir: L.tileLayer.wms("https://isk.geobasis-bb.de/mapproxy/dop20cir/service/wms", { ...wms, layers: "bb_dop20cir" }),
    osm: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 21, maxNativeZoom: 19,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
    }),
  };
  let active = layers.rgb.addTo(map);
  for (const layer of Object.values(layers)) {
    layer.on("tileerror", () => {
      if (active === layer) byId("map-status").textContent = "Some basemap tiles are unavailable. Match locations remain visible.";
    });
  }
  byId("basemap").addEventListener("change", (event) => {
    active.remove();
    active = layers[event.target.value].addTo(map);
    byId("map-status").textContent = "Live imagery may have a different flight date from the frozen survey.";
  });
}

async function start() {
  const [metadata, collection, municipalities, postcodes] = await Promise.all([
    read("summary.json"), read("matches.geojson"), read("municipalities.geojson"), read("postcodes.geojson"),
  ]);
  if (collection.snapshot !== metadata.created_at) throw new Error("The matches and summary belong to different builds.");

  const matches = collection.features.sort((a, b) => a.id.localeCompare(b.id));
  const jointCount = matches.filter(({ properties }) => properties.kind === "joint").length;
  byId("total-matches").textContent = number(matches.length);
  byId("joint-matches").textContent = number(jointCount);
  byId("coordinate-matches").textContent = number(matches.length - jointCount);
  byId("registry-date").textContent = metadata.registry_snapshot_date;
  byId("capture-dates").textContent = metadata.capture_dates.join(" / ");
  byId("delivery-version").textContent = metadata.algorithm;

  const map = L.map("map", { maxZoom: 21, zoomAnimation: false });
  attachBasemap(map);
  const boundaries = L.geoJSON(municipalities, { interactive: false, style: { color: "#204f40", weight: 1.5, fill: false } }).addTo(map);
  const postalLayer = L.geoJSON(postcodes, { interactive: false,
    style: { color: "#9b7338", weight: 1.5, fillOpacity: 0.06, dashArray: "5 4" } }).addTo(map);
  const outlineLayer = L.featureGroup().addTo(map);
  const markers = L.featureGroup().addTo(map);
  const entries = [];
  const detailsPlaceholder = [...byId("details").childNodes];
  let active = null;

  function clearDetails() {
    if (active) {
      active.button.removeAttribute("aria-current");
      active.marker.setRadius(5.5);
    }
    active = null;
    outlineLayer.clearLayers();
    byId("details").replaceChildren(...detailsPlaceholder);
  }

  function inspect(entry, zoom) {
    clearDetails();
    active = entry;
    active.marker.setRadius(8);
    active.marker.bringToFront();
    active.button.setAttribute("aria-current", "true");
    const outline = L.geoJSON(active.match, { interactive: false,
      style: { color: active.color, weight: 3, fillColor: active.color, fillOpacity: 0.42 } });
    outline.addTo(outlineLayer).bringToBack();
    showDetails(active.match);
    if (zoom) map.fitBounds(outline.getBounds().pad(1), { maxZoom: 20, animate: false });
  }

  for (const match of matches) {
    const { point, municipality, postcode, kind } = match.properties;
    const color = kinds[kind].color;
    const item = document.createElement("li");
    const button = element("button", "", "result-row");
    button.type = "button";
    const meta = element("span", [municipality, postcode].filter(Boolean).join(" · "), "row-meta");
    button.append(element("strong", match.id), meta, element("span", kinds[kind].label, `result-key ${kind}`));
    item.append(button);

    const marker = L.circleMarker([point[1], point[0]], { radius: 5.5, weight: 1.7, color: "#fff",
      fillColor: color, fillOpacity: 1, className: `published-match ${kind}` });
    marker.bindTooltip(() => element("span", match.id));
    const entry = { match, marker, item, button, color, search: `${match.id} ${postcode || ""}`.toLowerCase() };
    entries.push(entry);
    marker.on("click", () => inspect(entry, false));
    button.addEventListener("click", () => inspect(entry, true));
  }

  function filter() {
    const query = byId("search").value.toLowerCase().trim();
    const kind = byId("result-type").value;
    const visible = entries.filter((entry) =>
      (!kind || entry.match.properties.kind === kind) && entry.search.includes(query));
    if (active && !visible.includes(active)) clearDetails();
    markers.clearLayers();
    for (const entry of visible) entry.marker.addTo(markers);
    active?.marker.bringToFront();
    byId("results-list").replaceChildren(...visible.map((entry) => entry.item));
    byId("list-status").textContent = visible.length ? "" : "No published results match this search.";
    byId("count").textContent = number(visible.length);
    byId("map-count").textContent = `${number(visible.length)} displayed`;
  }

  byId("search").addEventListener("input", filter);
  byId("result-type").addEventListener("change", filter);
  byId("show-postcodes").addEventListener("change", (event) => {
    if (event.target.checked) postalLayer.addTo(map);
    else postalLayer.remove();
  });
  function fit() {
    const bounds = markers.getBounds();
    map.fitBounds(bounds.isValid() ? bounds : boundaries.getBounds(), { padding: [24, 24], maxZoom: 18, animate: false });
  }
  byId("fit").addEventListener("click", fit);
  filter();
  fit();
}

start().catch((error) => {
  byId("list-status").textContent = error.message;
  byId("map-status").textContent = "Serve the delivery folder over HTTP to load the map.";
});
