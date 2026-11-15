import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import type { Snapshot } from "../types";
import zh from "../locales/zh.json";

vi.mock("../features/map/MapPanel", () => ({
  MapPanel: (props: { onFinish: (geometry: object) => void }) => (
    <button
      onClick={() =>
        props.onFinish({
          type: "Polygon",
          coordinates: [
            [
              [13, 52],
              [13.1, 52],
              [13.1, 52.1],
              [13, 52],
            ],
          ],
        })
      }
    >
      Finish test polygon
    </button>
  ),
}));

let data: Snapshot;
let calls: { url: string; body: Record<string, unknown> }[];
let conflict = false;

beforeEach(() => {
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    clear: () => storage.clear(),
  });
  conflict = false;
  calls = [];
  data = {
    run_id: "test-run",
    revision: 0,
    manifest: {
      run_id: "test-run",
      input: {
        acquisition_date: "2026-04-22",
        width: 800,
        height: 800,
        crs: "EPSG:25833",
        valid_pixels: 640000,
        missing_pixels: 0,
      },
      preview_bounds: [
        [52, 13],
        [52.1, 13.1],
      ],
      counts: { rgbi: 1, rgb: 1 },
      limitations: [],
      identity: {
        input_sha256: "sample",
        config: {
          models: {
            rgbi: { variant: "regnety_rgbi_s42", sha256: "a".repeat(64) },
            rgb: { variant: "regnety_rgb_s44", sha256: "b".repeat(64) },
          },
        },
      },
    },
    footprint: { type: "Polygon", coordinates: [] },
    reviewed_areas: { type: "FeatureCollection", features: [] },
    events: [],
    candidates: {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          id: "candidate-1",
          geometry: { type: "Polygon", coordinates: [] },
          properties: {
            id: "candidate-1",
            category: "both",
            default_model: "rgbi",
            observation_ids: ["a"],
            score_mean: 0.8,
            status: "pending",
            reason: "",
            note: "",
            reviewer: "",
            version: 0,
            geometry_modified: false,
            projected_area_m2: 10,
            original_geometry: { type: "Polygon", coordinates: [] },
          },
        },
      ],
    },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options?: RequestInit) => {
      const body = options?.body ? JSON.parse(String(options.body)) : {};
      if (options?.method === "PATCH") {
        calls.push({ url, body });
        if (conflict)
          return new Response(JSON.stringify({ detail: "Version conflict" }), {
            status: 409,
          });
        data.candidates.features[0].properties = {
          ...data.candidates.features[0].properties,
          ...body,
          version: 1,
        };
        data.revision++;
        return new Response(JSON.stringify(data.candidates.features[0]));
      }
      if (options?.method === "POST") {
        calls.push({ url, body });
        return new Response(
          JSON.stringify(
            url.endsWith("/exports")
              ? { download_url: "/api/exports/test.gpkg" }
              : { id: "manual-1" },
          ),
        );
      }
      const value =
        url === "/api/runs"
          ? [{ run_id: "test-run", revision: 0, manifest: data.manifest }]
          : url.endsWith("/matching")
            ? { available: false, registry_ready: false }
            : url.includes("/candidates/")
              ? data.candidates.features[0]
              : url.includes("/assets/")
                ? { type: "FeatureCollection", features: [] }
                : data;
      return new Response(JSON.stringify(value));
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function ready() {
  render(<App />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("tab", { name: zh.imageReviewTab }));
  await screen.findByRole("button", { name: `✓ ${zh.confirm}` });
  return user;
}

describe("review workspace", () => {
  it("loads an overview and selected detail without downloading complete contextual layers", async () => {
    await ready();
    const urls = vi.mocked(fetch).mock.calls.map((call) => String(call[0]));
    expect(urls).toContain("/api/runs/test-run/workspace");
    expect(urls).toContain("/api/runs/test-run/candidates/candidate-1");
    expect(urls.some((url) => url.includes("/assets/"))).toBe(false);
  });
  it("requires saving edits before exporting a snapshot", async () => {
    const user = await ready();
    await user.type(screen.getByLabelText(zh.note), "Unsaved annotation");
    const button = screen.getByRole("button", {
      name: `↓ ${zh.export}`,
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText(zh.saveBeforeExport)).toBeTruthy();
    expect(calls).toHaveLength(0);
  });

  it("requires a reviewer and saves a versioned confirmation", async () => {
    const user = await ready();
    await user.click(screen.getByRole("button", { name: `✓ ${zh.confirm}` }));
    expect(calls).toHaveLength(0);
    expect(screen.getByRole("alert").textContent).toContain(zh.needReviewer);
    await user.type(
      screen.getByPlaceholderText(zh.reviewerHint),
      "Reviewer One",
    );
    await user.click(screen.getByRole("button", { name: `✓ ${zh.confirm}` }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toMatchObject({
      status: "confirmed",
      expected_version: 0,
      reviewer: "Reviewer One",
    });
    await screen.findByText(zh.saved);
  });

  it("requires a reason before rejecting a candidate", async () => {
    const user = await ready();
    await user.type(
      screen.getByPlaceholderText(zh.reviewerHint),
      "Reviewer One",
    );
    await user.click(screen.getByRole("button", { name: zh.reject }));
    expect(calls).toHaveLength(0);
    await user.type(screen.getByLabelText(zh.reason), "Roof window");
    await user.click(screen.getByRole("button", { name: zh.reject }));
    await waitFor(() => expect(calls[0].body.status).toBe("rejected"));
    expect(calls[0].body.reason).toBe("Roof window");
  });

  it("surfaces a stale version without falsely announcing success", async () => {
    conflict = true;
    const user = await ready();
    await user.type(
      screen.getByPlaceholderText(zh.reviewerHint),
      "Reviewer One",
    );
    await user.click(screen.getByRole("button", { name: `✓ ${zh.confirm}` }));
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("409"),
    );
    expect(screen.queryByText(zh.saved)).toBeNull();
  });

  it("creates a separate area inspection record", async () => {
    const user = await ready();
    await user.type(
      screen.getByPlaceholderText(zh.reviewerHint),
      "Reviewer One",
    );
    await user.click(screen.getByRole("button", { name: `▧ ${zh.checkArea}` }));
    await user.click(
      screen.getByRole("button", { name: "Finish test polygon" }),
    );
    await waitFor(() =>
      expect(calls[0].url.endsWith("/reviewed-areas")).toBe(true),
    );
    expect(calls[0].body).not.toHaveProperty("status");
  });

  it("exports a server snapshot and exposes its actual download URL", async () => {
    const user = await ready();
    await user.click(screen.getByRole("button", { name: `↓ ${zh.export}` }));
    const link = await screen.findByRole("link", { name: zh.download });
    expect(link.getAttribute("href")).toBe("/api/exports/test.gpkg");
    expect(calls[0].url).toBe("/api/runs/test-run/exports");
  });

  it("supports the English interface and filters the queue", async () => {
    const user = await ready();
    await user.selectOptions(screen.getByLabelText(zh.status), "confirmed");
    expect(screen.getByText(zh.empty)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: zh.languageSwitch }));
    expect(screen.getByText("Review queue")).toBeTruthy();
    expect(document.documentElement.lang).toBe("en");
  });
});
