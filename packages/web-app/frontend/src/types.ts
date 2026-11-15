import type {
  Feature,
  FeatureCollection,
  Polygon,
  MultiPolygon,
  Geometry,
  LineString,
} from "geojson";

export type Status = "pending" | "confirmed" | "rejected" | "uncertain";
export type ReviewGeometry = Polygon | MultiPolygon;
export type DrawnGeometry = ReviewGeometry | LineString;
export interface CandidateProperties {
  id: string;
  category: string;
  default_model: string | null;
  observation_ids: string[];
  score_mean: number | null;
  status: Status;
  reason: string;
  note: string;
  reviewer: string;
  version: number;
  geometry_modified: boolean;
  projected_area_m2: number;
  original_geometry: ReviewGeometry;
  active?: boolean;
  mounting?: "unknown" | "roof" | "ground" | "other";
  acquisition_dates?: string[];
  source_tiles?: string[];
  municipalities?: string[];
  parent_ids?: string[];
  building_ids?: string[];
  building_overlap_ratio?: number;
  boundary_clipped?: boolean;
  review_point?: [number, number];
}
export type Candidate = Feature<ReviewGeometry, CandidateProperties>;
export interface Manifest {
  run_id: string;
  kind?: string;
  input: {
    name?: string;
    area_m2?: number;
    acquisition_date: string;
    width: number;
    height: number;
    crs: string;
    valid_pixels: number;
    missing_pixels: number;
  };
  preview_bounds: [[number, number], [number, number]];
  counts: Record<string, number>;
  identity: {
    config: { models: Record<string, { variant: string; sha256: string }> };
    input_sha256: string;
  };
  limitations: string[];
}
export interface Snapshot {
  run_id: string;
  revision: number;
  manifest: Manifest;
  footprint: Geometry;
  candidates: FeatureCollection<ReviewGeometry, CandidateProperties>;
  reviewed_areas: FeatureCollection;
  events: {
    revision: number;
    item_id: string;
    kind: string;
    reviewer: string;
    created_at: string;
  }[];
}
export interface Run {
  run_id: string;
  revision: number;
  manifest: Manifest;
}
export type Mode = "none" | "draw" | "area" | "edit" | "split";
