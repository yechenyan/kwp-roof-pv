export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    const detail =
      typeof error.detail === "string"
        ? error.detail
        : JSON.stringify(error.detail);
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.json();
}

export function runUrl(id: string) {
  return `/api/runs/${encodeURIComponent(id)}`;
}
import type { components } from "./api/schema";
import type { ReviewGeometry } from "./types";

export type ReviewUpdate = Omit<components["schemas"]["Review"], "geometry"> & {
  geometry?: ReviewGeometry | null;
};
export type IdentityReview = components["schemas"]["IdentityReview"];
