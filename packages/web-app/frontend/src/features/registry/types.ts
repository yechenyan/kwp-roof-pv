import type { Feature } from "geojson";
import type { IdentityReview } from "../../api";
import type { ReviewGeometry } from "../../types";

export interface RegistryUnit {
  id: string;
  municipality: string;
  postcode: string;
  power_kw: number | null;
  module_count: number | null;
  commissioned: string | null;
  longitude: number | null;
  latitude: number | null;
  flags: string[];
  decoded: Record<string, string | null>;
  association_state?: string;
  association_count?: number;
  proposal_state?: string;
}
export interface RegistryAlternative {
  proposal_id: string;
  solar_id: string;
  group_id: string;
  score: number;
  state: string;
  member_ids: string[];
  position: [number, number];
  area_m2?: number;
  stale?: boolean;
  evidence: {
    observed_area_m2: number;
    coordinate_distance_m: number | null;
    timing: string;
    module_projected_area_interval_m2: number[] | null;
    power_projected_area_interval_m2: number[] | null;
  };
}
export interface IdentityDecision {
  solar_id: string;
  group_id: string;
  state: IdentityReview["state"];
  version: number;
  evidence_type: NonNullable<IdentityReview["evidence_type"]>;
  evidence: string;
  note: string;
  reviewer: string;
  group_confirmed: number;
  member_ids?: string[];
  position?: [number, number];
  stale?: boolean;
}
export interface RegistryGroup {
  batch_id: string;
  stale: boolean;
  registry_snapshot_changed: boolean;
  group:
    | (Feature<ReviewGeometry> & {
        id: string;
        properties: {
          member_ids: string[];
          area_m2: number;
          flags: string[];
          capture_dates: string[];
        };
      })
    | null;
  alternatives: RegistryAlternative[];
  units: Record<string, RegistryUnit>;
  reviews: IdentityDecision[];
  competing_reviews: { solar_id: string; group_id: string; state: string }[];
  ranking_statistics?: { candidate_pool_size: number; best_tie_count: number };
}
export interface RegistryUnitDetail {
  unit: RegistryUnit;
  raw: Record<string, string>;
  snapshot_id: string;
  ranking: {
    state: string;
    flags: string[];
    alternatives: RegistryAlternative[];
    candidate_pool_size?: number;
    best_tie_count?: number;
  } | null;
  reviewed_associations?: IdentityDecision[];
  storage: {
    storage_id: string;
    system_id: string;
    net_power_kw: number | null;
    usable_capacity_kwh: number | null;
    relationship_status: string;
  }[];
}
export interface RegistryLocations {
  unitId: string;
  focusKey: string;
  points: {
    groupId: string;
    memberIds: string[];
    position: [number, number];
    number: number;
    state: string;
    stale: boolean;
    score?: number;
  }[];
}
