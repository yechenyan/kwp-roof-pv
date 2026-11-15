import { useState } from "react";
import type { Candidate } from "../../types";

export interface QueueFilters {
  status: string;
  category: string;
  building: string;
}

interface Props {
  candidates: Candidate[];
  total: number;
  selectedId: string;
  checkedIds: string[];
  filters: QueueFilters;
  onFilter: (key: keyof QueueFilters, value: string) => void;
  onSelect: (id: string) => void;
  onCheck: (id: string, checked: boolean) => void;
  t: (key: string) => string;
}

export function CandidateQueue(props: Props) {
  const [limit, setLimit] = useState(100);
  function filter(key: keyof QueueFilters, value: string) {
    setLimit(100);
    props.onFilter(key, value);
  }
  return (
    <section className="queue-panel">
      <div className="section-title">
        <h2>
          {props.t("queue")}{" "}
          <span className="pill">
            {props.candidates.length} {props.t("of")} {props.total}
          </span>
        </h2>
        <div className="filters">
          <select
            aria-label={props.t("buildingFilter")}
            value={props.filters.building}
            onChange={(e) => filter("building", e.target.value)}
          >
            {["all", "with", "without", "unknown"].map((value) => (
              <option key={value} value={value}>
                {props.t("building_" + value)}
              </option>
            ))}
          </select>
          <select
            aria-label={props.t("status")}
            value={props.filters.status}
            onChange={(e) => filter("status", e.target.value)}
          >
            {["all", "pending", "confirmed", "uncertain", "rejected"].map(
              (value) => (
                <option key={value} value={value}>
                  {props.t(value)}
                </option>
              ),
            )}
          </select>
          <select
            aria-label={props.t("category")}
            value={props.filters.category}
            onChange={(e) => filter("category", e.target.value)}
          >
            {[
              "all",
              "both",
              "rgbi_only",
              "rgb_only",
              "geometry_conflict",
              "manual",
            ].map((value) => (
              <option key={value} value={value}>
                {props.t(value)}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="candidate-list">
        {props.candidates.slice(0, limit).map((feature) => (
          <div className="candidate-line" key={feature.properties.id}>
            <input
              type="checkbox"
              aria-label={`${props.t("selectForMerge")} ${feature.properties.id}`}
              checked={props.checkedIds.includes(feature.properties.id)}
              onChange={(e) =>
                props.onCheck(feature.properties.id, e.target.checked)
              }
            />
            <button
              className={`candidate-row ${props.selectedId === feature.id ? "active" : ""}`}
              onClick={() => props.onSelect(feature.properties.id)}
            >
              <span className={`dot ${feature.properties.status}`} />
              <b>#{feature.properties.id.split(":").at(-1)?.slice(-8)}</b>
              <span>{props.t(feature.properties.category)}</span>
              <strong>
                {feature.properties.projected_area_m2.toFixed(2)} m²
              </strong>
              <small>{props.t(feature.properties.status)}</small>
            </button>
          </div>
        ))}
        {props.candidates.length > limit && (
          <button
            className="load-more"
            onClick={() => setLimit((previous) => previous + 100)}
          >
            {props.t("loadMore")} ({limit}/{props.candidates.length})
          </button>
        )}
        {!props.candidates.length && (
          <p className="muted">{props.t("empty")}</p>
        )}
      </div>
    </section>
  );
}
