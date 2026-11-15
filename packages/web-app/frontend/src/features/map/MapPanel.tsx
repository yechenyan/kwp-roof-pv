import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import type { Feature, FeatureCollection, Position } from "geojson";
import type {
  Candidate,
  Mode,
  ReviewGeometry,
  Snapshot,
  DrawnGeometry,
} from "../../types";

interface Props {
  snapshot: Snapshot;
  candidates: Candidate[];
  selected?: Candidate;
  observations: {
    rgbi?: FeatureCollection;
    rgb?: FeatureCollection;
    buildings?: FeatureCollection;
  };
  mode: Mode;
  draft: ReviewGeometry | null;
  onSelect: (id: string) => void;
  onDraft: (geometry: ReviewGeometry) => void;
  onFinish: (geometry: DrawnGeometry) => void;
  onCancel: () => void;
  t: (key: string) => string;
}

const colors: Record<string, string> = {
  pending: "#f4c773",
  confirmed: "#3cdfa7",
  rejected: "#e38b87",
  uncertain: "#c2afff",
};

export function MapPanel(props: Props) {
  const element = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const current = useRef(props);
  current.current = props;
  const [points, setPoints] = useState<Position[]>([]);
  const [showOsm, setShowOsm] = useState(false);
  const [showRgbi, setShowRgbi] = useState(false);
  const [showRgb, setShowRgb] = useState(false);
  const [showAreas, setShowAreas] = useState(true);
  const [showBuildings, setShowBuildings] = useState(true);
  const [opacity, setOpacity] = useState(1);
  const [display, setDisplay] = useState("rgb");
  const [viewport, setViewport] = useState<{
    bounds: L.LatLngBounds;
    zoom: number;
  } | null>(null);
  const municipal = props.snapshot.manifest.kind === "municipal";
  const minimumPoints = props.mode === "split" ? 2 : 3;

  function boundsOf(geometry: ReviewGeometry) {
    const positions =
      geometry.type === "Polygon"
        ? geometry.coordinates.flat()
        : geometry.coordinates.flat(2);
    return L.latLngBounds(
      positions.map((point) => [point[1], point[0]] as L.LatLngTuple),
    );
  }
  const indexed = useMemo(
    () =>
      props.candidates.map((feature) => ({
        feature,
        bounds: boundsOf(feature.geometry),
      })),
    [props.candidates],
  );
  const visible = useMemo(() => {
    if (municipal && (!viewport || viewport.zoom < 16))
      return props.selected ? [props.selected] : [];
    return indexed
      .filter((item) => !viewport || viewport.bounds.intersects(item.bounds))
      .slice(0, 1000)
      .map((item) => item.feature);
  }, [indexed, viewport, municipal, props.selected]);

  useEffect(() => {
    if (!element.current) return;
    const instance = L.map(element.current, { maxZoom: 24, zoomControl: true });
    map.current = instance;
    instance.on("click", (event) => {
      if (["draw", "area", "split"].includes(current.current.mode)) {
        setPoints((previous) => [
          ...previous,
          [event.latlng.lng, event.latlng.lat],
        ]);
      }
    });
    instance.on("moveend", () =>
      setViewport({ bounds: instance.getBounds(), zoom: instance.getZoom() }),
    );
    const observer = new ResizeObserver(() => instance.invalidateSize());
    observer.observe(element.current);
    return () => {
      observer.disconnect();
      instance.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    setPoints([]);
  }, [props.mode, props.snapshot.run_id]);

  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    instance.fitBounds(props.snapshot.manifest.preview_bounds, {
      padding: [8, 8],
    });
  }, [props.snapshot.run_id]);

  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    const group = L.layerGroup().addTo(instance);
    if (showOsm)
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxNativeZoom: 19,
        maxZoom: 24,
        attribution: "© OpenStreetMap contributors",
      }).addTo(group);
    if (municipal)
      L.tileLayer(
        `/api/runs/${encodeURIComponent(props.snapshot.run_id)}/tiles/${display}/{z}/{x}/{y}.png`,
        {
          opacity,
          maxNativeZoom: 20,
          maxZoom: 24,
          bounds: props.snapshot.manifest.preview_bounds,
        },
      ).addTo(group);
    else
      L.imageOverlay(
        `/api/runs/${encodeURIComponent(props.snapshot.run_id)}/assets/${display === "cir" ? "preview_cir.png" : "preview.png"}`,
        props.snapshot.manifest.preview_bounds,
        { opacity, interactive: false },
      ).addTo(group);
    return () => {
      group.remove();
    };
  }, [props.snapshot.run_id, opacity, showOsm, display, municipal]);

  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    const group = L.layerGroup().addTo(instance);
    if (municipal && viewport && viewport.zoom < 16) {
      const groups = new Map<
        string,
        { count: number; lat: number; lng: number }
      >();
      for (const item of indexed) {
        if (!viewport.bounds.intersects(item.bounds)) continue;
        const coordinate = item.feature.properties.review_point;
        const center = coordinate
          ? L.latLng(coordinate[1], coordinate[0])
          : item.bounds.getCenter();
        const pixel = instance.project(center, viewport.zoom);
        const key = `${Math.floor(pixel.x / 64)}:${Math.floor(pixel.y / 64)}`;
        const bucket = groups.get(key) || { count: 0, lat: 0, lng: 0 };
        bucket.count++;
        bucket.lat += center.lat;
        bucket.lng += center.lng;
        groups.set(key, bucket);
      }
      for (const bucket of groups.values()) {
        const center: L.LatLngTuple = [
          bucket.lat / bucket.count,
          bucket.lng / bucket.count,
        ];
        const marker = L.circleMarker(center, {
          radius: Math.min(22, 9 + Math.sqrt(bucket.count)),
          color: "#d7f0dd",
          weight: 1,
          fillColor: "#287b60",
          fillOpacity: 0.9,
        }).addTo(group);
        marker.bindTooltip(String(bucket.count), {
          permanent: true,
          direction: "center",
          className: "candidate-cluster",
        });
        marker.on("click", (event) => {
          L.DomEvent.stopPropagation(event.originalEvent);
          instance.setView(center, Math.min(16, viewport.zoom + 2));
        });
      }
    }
    if (municipal)
      L.geoJSON(
        {
          type: "Feature",
          geometry: props.snapshot.footprint,
          properties: {},
        } as Feature,
        {
          style: { color: "#63c6b6", weight: 2, fillOpacity: 0 },
          interactive: false,
        },
      ).addTo(group);
    if (
      showBuildings &&
      props.observations.buildings &&
      viewport &&
      viewport.zoom >= 16
    ) {
      const buildings = props.observations.buildings.features
        .filter((feature) =>
          viewport.bounds.intersects(
            boundsOf(feature.geometry as ReviewGeometry),
          ),
        )
        .slice(0, 2000);
      L.geoJSON(
        { type: "FeatureCollection", features: buildings } as FeatureCollection,
        {
          style: { color: "#b1d3f0", weight: 1, fillOpacity: 0 },
          interactive: false,
        },
      ).addTo(group);
    }
    if (showAreas)
      L.geoJSON(props.snapshot.reviewed_areas, {
        style: {
          color: "#65b9ff",
          fillOpacity: 0.07,
          weight: 1,
          dashArray: "5 5",
        },
        interactive: false,
      }).addTo(group);
    for (const variant of ["rgbi", "rgb"] as const) {
      if (
        (variant === "rgbi" ? showRgbi : showRgb) &&
        props.observations[variant]
      ) {
        const inView = props.observations[variant]?.features
          .filter((feature) => {
            if (municipal && (!viewport || viewport.zoom < 16)) return false;
            return (
              !viewport ||
              viewport.bounds.intersects(
                boundsOf(feature.geometry as ReviewGeometry),
              )
            );
          })
          .slice(0, 1000);
        L.geoJSON(
          {
            type: "FeatureCollection",
            features: inView || [],
          } as FeatureCollection,
          {
            style: {
              color: variant === "rgbi" ? "#25d9e7" : "#ff8bd7",
              weight: 2,
              fillOpacity: 0.05,
              dashArray: variant === "rgb" ? "4 3" : undefined,
            },
            interactive: false,
          },
        ).addTo(group);
      }
    }
    for (const feature of visible) {
      const active = feature.id === props.selected?.id;
      const layer = L.geoJSON(feature, {
        style: {
          color: active ? "#ffffff" : colors[feature.properties.status],
          weight: active ? 3 : 1.5,
          fillColor: colors[feature.properties.status],
          fillOpacity: active ? 0.28 : 0.1,
        },
      });
      layer.on("click", (event) => {
        if (current.current.mode === "none") {
          L.DomEvent.stopPropagation(event.originalEvent);
          current.current.onSelect(feature.properties.id);
        }
      });
      layer.bindTooltip(
        `${feature.properties.id.split(":").at(-1)} · ${props.t(feature.properties.status)}`,
        { sticky: true },
      );
      layer.addTo(group);
    }
    return () => {
      group.remove();
    };
  }, [
    visible,
    props.selected?.id,
    props.observations,
    props.snapshot.reviewed_areas,
    showRgbi,
    showRgb,
    showAreas,
    showBuildings,
    props.t,
    viewport,
    municipal,
    indexed,
  ]);

  useEffect(() => {
    if (props.selected && map.current) {
      map.current.fitBounds(L.geoJSON(props.selected).getBounds(), {
        maxZoom: 22,
        padding: [90, 90],
      });
    }
  }, [props.selected?.id]);

  useEffect(() => {
    if (!map.current || !points.length) return;
    const layer = L.polyline(
      points.map((p) => [p[1], p[0]] as L.LatLngTuple),
      { color: "#ffffff", weight: 3, dashArray: "4 4" },
    ).addTo(map.current);
    return () => {
      layer.remove();
    };
  }, [points]);

  useEffect(() => {
    const instance = map.current;
    const source = props.draft || props.selected?.geometry;
    if (!instance || props.mode !== "edit" || !source) return;
    const geometry: ReviewGeometry = structuredClone(source);
    const group = L.layerGroup().addTo(instance);
    const outline = L.geoJSON(
      { type: "Feature", geometry, properties: {} } as Feature<ReviewGeometry>,
      { style: { color: "#fff", weight: 3, dashArray: "3 3", fillOpacity: 0 } },
    ).addTo(group);
    const polygons =
      geometry.type === "Polygon"
        ? [geometry.coordinates]
        : geometry.coordinates;
    polygons.forEach((polygon) =>
      polygon.forEach((ring) =>
        ring.slice(0, -1).forEach((point, index) => {
          const marker = L.marker([point[1], point[0]], {
            draggable: true,
            icon: L.divIcon({
              className: "vertex",
              iconSize: [12, 12],
              iconAnchor: [6, 6],
            }),
          }).addTo(group);
          marker.on("drag", () => {
            const latlng = marker.getLatLng();
            ring[index] = [latlng.lng, latlng.lat];
            if (index === 0) ring[ring.length - 1] = [...ring[0]];
            outline.clearLayers();
            outline.addData({
              type: "Feature",
              geometry,
              properties: {},
            } as Feature<ReviewGeometry>);
          });
          marker.on("dragend", () =>
            current.current.onDraft(structuredClone(geometry)),
          );
        }),
      ),
    );
    return () => {
      group.remove();
    };
  }, [props.mode, props.selected?.id, props.draft]);

  const finish = () => {
    if (points.length < minimumPoints) return;
    if (props.mode === "split")
      props.onFinish({ type: "LineString", coordinates: points });
    else
      props.onFinish({
        type: "Polygon",
        coordinates: [[...points, [...points[0]]]],
      });
  };

  return (
    <section className="map-card">
      <div className="section-title">
        <div>
          <h2>{props.t("map")}</h2>
          <p>{props.t("mapHint")}</p>
        </div>
        <span className="live-dot">DOP20</span>
      </div>
      <div className="map-layer-controls">
        <label>
          <input
            type="checkbox"
            checked={showBuildings}
            onChange={(event) => setShowBuildings(event.target.checked)}
          />
          {props.t("buildings")}
        </label>
        <button
          onClick={() =>
            map.current?.fitBounds(props.snapshot.manifest.preview_bounds)
          }
        >
          {props.t("fit")}
        </button>
        <select
          aria-label={props.t("display")}
          value={display}
          onChange={(event) => setDisplay(event.target.value)}
        >
          <option value="rgb">RGB</option>
          <option value="cir">CIR · NIR / R / G</option>
        </select>
        <label>
          <input
            type="checkbox"
            checked={showOsm}
            onChange={(e) => setShowOsm(e.target.checked)}
          />
          {props.t("osm")}
        </label>
        <label className="cyan">
          <input
            type="checkbox"
            checked={showRgbi}
            onChange={(e) => setShowRgbi(e.target.checked)}
          />
          {props.t("rgbi")}
        </label>
        <label className="pink">
          <input
            type="checkbox"
            checked={showRgb}
            onChange={(e) => setShowRgb(e.target.checked)}
          />
          {props.t("rgb")}
        </label>
        <label>
          <input
            type="checkbox"
            checked={showAreas}
            onChange={(e) => setShowAreas(e.target.checked)}
          />
          {props.t("areas")}
        </label>
        <label>
          {props.t("opacity")}
          <input
            aria-label={props.t("opacity")}
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={opacity}
            onChange={(e) => setOpacity(Number(e.target.value))}
          />
        </label>
      </div>
      <div className="map-surface" ref={element} data-testid="map" />
      {municipal && viewport && viewport.zoom < 16 && (
        <div className="map-edit-bar">{props.t("zoomHint")}</div>
      )}
      {["draw", "area", "split"].includes(props.mode) && (
        <div className="map-edit-bar">
          <span>
            {props.t(props.mode === "split" ? "splitHint" : "drawingHint")} (
            {points.length})
          </span>
          <button disabled={points.length < minimumPoints} onClick={finish}>
            {props.t("finish")}
          </button>
          <button onClick={props.onCancel}>{props.t("cancel")}</button>
        </div>
      )}
      {props.mode === "edit" && (
        <div className="map-edit-bar">
          <span>{props.t("editingHint")}</span>
          <button onClick={props.onCancel}>{props.t("cancel")}</button>
        </div>
      )}
      <div className="map-footnote">
        <span>
          {props.t("snapshot")} ·{" "}
          {props.snapshot.manifest.input.acquisition_date}
        </span>
        <span>{props.t("attribution")}</span>
      </div>
    </section>
  );
}
