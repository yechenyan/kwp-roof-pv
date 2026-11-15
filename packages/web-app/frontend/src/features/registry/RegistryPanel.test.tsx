import React, { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RegistryPanel } from "./RegistryPanel";
import type {
  RegistryLocations,
  RegistryGroup,
  RegistryUnitDetail,
} from "./types";
import en from "../../locales/en.json";

const unit = {
  id: "SEE1",
  municipality: "Hohen Neuendorf",
  postcode: "16540",
  power_kw: 10,
  module_count: 25,
  commissioned: "2020-01-01",
  longitude: null,
  latitude: null,
  flags: [],
  decoded: {},
  association_state: "unreviewed",
};
const secondUnit = { ...unit, id: "SEE2", power_kw: 8 };
const alternative = {
  proposal_id: "link1",
  solar_id: "SEE1",
  group_id: "array1",
  score: 0.7,
  state: "ambiguous",
  member_ids: ["candidate1"],
  position: [13.28, 52.67] as [number, number],
  evidence: {
    observed_area_m2: 40,
    coordinate_distance_m: null,
    timing: "pre_capture",
    module_projected_area_interval_m2: [20, 60],
    power_projected_area_interval_m2: [30, 80],
  },
};
let group: RegistryGroup;
let writes: { url: string; body: Record<string, unknown> }[];
let urls: string[];

beforeEach(() => {
  writes = [];
  urls = [];
  group = {
    batch_id: "batch1",
    stale: false,
    registry_snapshot_changed: false,
    group: {
      type: "Feature",
      id: "array1",
      geometry: { type: "Polygon", coordinates: [] },
      properties: {
        member_ids: ["candidate1"],
        area_m2: 40,
        flags: [],
        capture_dates: ["2026-04-22"],
      },
    },
    alternatives: [
      alternative,
      { ...alternative, proposal_id: "link2", solar_id: "SEE2" },
    ],
    units: { SEE1: unit, SEE2: secondUnit },
    reviews: [],
    competing_reviews: [],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      urls.push(url);
      let value: unknown;
      if (init?.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        writes.push({ url, body });
        const decision = {
          ...body,
          group_id: "array1",
          version: body.expected_version + 1,
          group_confirmed: Number(body.group_confirmed),
        };
        group = { ...group, reviews: [decision] };
        value = decision;
      } else if (url.endsWith("/matching"))
        value = {
          available: true,
          registry_ready: true,
          registry: { snapshot_date: "2026-11-15", summary: { solar: 2 } },
          batch: { batch_id: "batch1" },
        };
      else if (
        url.includes("/matching/candidates/") ||
        url.includes("/matching/groups/")
      )
        value = group;
      else if (url.includes("/units/SEE")) {
        const u = url.endsWith("SEE2") ? secondUnit : unit;
        value = {
          unit: u,
          raw: {
            EinheitMastrNummer: u.id,
            NameStromerzeugungseinheit: `Fixture solar ${u.id}`,
            Nettonennleistung: "9.600",
            ZugeordneteWirkleistungWechselrichter: "9.500",
            Registrierungsdatum: "2020-02-01",
            DatumLetzteAktualisierung: "2026-11-14T12:00:00",
            EinheitBetriebsstatus: "35",
            ArtDerSolaranlage: "853",
            Hauptausrichtung: "701",
            HauptausrichtungNeigungswinkel: "809",
            Nebenausrichtung: "697",
            NebenausrichtungNeigungswinkel: "810",
            LokationMaStRNummer: `SEL-${u.id}`,
            EegMaStRNummer: `EEG-${u.id}`,
            CustomSourceAttribute: `Original field ${u.id}`,
          },
          snapshot_id: "snapshot1",
          ranking: {
            state: "ambiguous",
            flags: [],
            alternatives: [{ ...alternative, solar_id: u.id }],
            candidate_pool_size: 20,
            best_tie_count: 10,
          },
          storage: [],
          reviewed_associations: group.reviews
            .filter((r) => r.solar_id === u.id)
            .map((r) => ({
              ...r,
              position: alternative.position,
              member_ids: ["candidate1"],
              stale: false,
            })),
        } satisfies RegistryUnitDetail;
      } else value = { total: 2, items: [unit, secondUnit] };
      return new Response(JSON.stringify(value));
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mount(reviewer = "Identity reviewer") {
  const onDirty = vi.fn();
  function Harness() {
    const [selected, setSelected] = useState("");
    const [locations, setLocations] = useState<RegistryLocations | null>(null);
    return (
      <RegistryPanel
        runId="run1"
        selectedId={selected}
        imageRevision={0}
        reviewer={reviewer}
        disabled={false}
        onSelect={(id) => {
          setSelected(id);
          return true;
        }}
        onDirty={onDirty}
        onBusy={vi.fn()}
        onChanged={vi.fn()}
        onGroup={vi.fn()}
        onLocations={setLocations}
        onInspectImage={vi.fn()}
        onRevealRegistry={vi.fn()}
        t={(key) => (en as Record<string, string>)[key] || key}
      >
        {({ browser, inspector }) => (
          <div>
            <aside>{browser}</aside>
            <section aria-label="Test map">
              {locations?.points.map((p) => (
                <button
                  key={p.groupId}
                  onClick={() => setSelected(p.memberIds[0])}
                >
                  Map point {p.number} {p.state}
                </button>
              ))}
            </section>
            <aside>{inspector}</aside>
          </div>
        )}
      </RegistryPanel>
    );
  }
  render(<Harness />);
  return { user: userEvent.setup(), onDirty };
}

async function selectOnMap(user: ReturnType<typeof userEvent.setup>) {
  const browser = screen.getByRole("region", { name: en.registryListTitle });
  await user.click(
    await within(browser).findByRole("button", { name: /^SEE1/ }),
  );
  await user.click(
    await screen.findByRole("button", { name: "Map point 1 candidate" }),
  );
  await screen.findByLabelText(en.identityEvidence);
}

describe("map-centred registry workflow", () => {
  it("shows complete registration details immediately after left-list selection without choosing a map point", async () => {
    const { user } = mount();
    const browser = screen.getByRole("region", { name: en.registryListTitle });
    await user.click(
      await within(browser).findByRole("button", { name: /^SEE1/ }),
    );
    const details = await screen.findByRole("region", {
      name: en.registrationDetailsTitle,
    });
    expect(
      within(details).getAllByText("Fixture solar SEE1").length,
    ).toBeGreaterThan(0);
    expect(within(details).getByText("9.600 kW")).toBeTruthy();
    expect(
      within(details).getAllByText(en.mastrValue_35).length,
    ).toBeGreaterThan(0);
    expect(within(details).getByText(en.mastrValue_701)).toBeTruthy();
    expect(within(details).getByText(en.mastrValue_809)).toBeTruthy();
    expect(within(details).getAllByText("SEL-SEE1").length).toBeGreaterThan(0);
    expect(
      within(details).getAllByText(en.registrationUnavailable).length,
    ).toBeGreaterThan(0);
    await user.click(
      within(details).getByText(new RegExp(en.registrationOriginalFields)),
    );
    expect(within(details).getByText("CustomSourceAttribute")).toBeTruthy();
    expect(within(details).getByText("Original field SEE1")).toBeTruthy();
    expect(screen.queryByLabelText(en.identityEvidence)).toBeNull();
    await user.click(within(browser).getByRole("button", { name: /^SEE2/ }));
    await waitFor(() =>
      expect(screen.getAllByText("Fixture solar SEE2").length).toBeGreaterThan(
        0,
      ),
    );
    expect(screen.queryByText("Fixture solar SEE1")).toBeNull();
    expect(writes).toHaveLength(0);
  });
  it("can inspect the selected registration and return to an unsaved association without losing its draft", async () => {
    const { user } = mount();
    await selectOnMap(user);
    await user.type(
      screen.getByLabelText(en.identityEvidence),
      "Keep this unsaved identity evidence.",
    );
    await user.click(
      screen.getByRole("button", { name: en.viewRegistrationDetails }),
    );
    expect(
      screen.getByRole("region", { name: en.registrationDetailsTitle }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Map point 1 candidate" }),
    ).toBeTruthy();
    await user.click(
      screen.getByRole("button", { name: en.registrationAssociationTab }),
    );
    expect(
      (screen.getByLabelText(en.identityEvidence) as HTMLTextAreaElement).value,
    ).toBe("Keep this unsaved identity evidence.");
    expect(writes).toHaveLength(0);
  });
  it("selects a registration on the left, shows image points, and opens the clicked array on the right", async () => {
    const { user } = mount();
    expect(screen.queryByLabelText(en.identityEvidence)).toBeNull();
    const browser = screen.getByRole("region", { name: en.registryListTitle });
    await user.click(
      await within(browser).findByRole("button", { name: /^SEE1/ }),
    );
    expect(
      await screen.findByRole("button", { name: "Map point 1 candidate" }),
    ).toBeTruthy();
    expect(screen.queryByLabelText(en.identityEvidence)).toBeNull();
    await user.click(
      screen.getByRole("button", { name: "Map point 1 candidate" }),
    );
    expect(await screen.findByLabelText(en.identityEvidence)).toBeTruthy();
    expect(
      screen.getByRole("tab", { name: `${en.linkedRegistry} (0)` }),
    ).toBeTruthy();
    expect(
      screen.getByRole("tab", { name: `${en.potentialRegistry} (2)` }),
    ).toBeTruthy();
    expect(writes).toHaveLength(0);
  });
  it("changes the potential registry partner without changing the left selection or map search", async () => {
    const { user } = mount();
    await selectOnMap(user);
    const inspector = screen.getByRole("region", {
      name: en.registryInspectorTitle,
    });
    await user.click(within(inspector).getByRole("button", { name: /^SEE2/ }));
    await waitFor(() =>
      expect(
        within(inspector).getByRole("heading", { name: "SEE2" }),
      ).toBeTruthy(),
    );
    const browser = screen.getByRole("region", { name: en.registryListTitle });
    expect(
      within(browser)
        .getByRole("button", { name: /^SEE1/ })
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByRole("button", { name: "Map point 1 candidate" }),
    ).toBeTruthy();
  });
  it("saves an independent association and moves it into saved links", async () => {
    const { user, onDirty } = mount();
    await selectOnMap(user);
    await user.type(
      screen.getByLabelText(en.identityEvidence),
      "Area and module count are compatible, but independent evidence is still missing.",
    );
    await user.selectOptions(
      screen.getByLabelText(en.identityDecision),
      "probable_assignment",
    );
    expect(onDirty).toHaveBeenCalledWith(true);
    await user.click(screen.getByRole("button", { name: en.saveIdentity }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].url).toBe("/api/runs/run1/matching/groups/array1");
    expect(writes[0].body).toMatchObject({
      batch_id: "batch1",
      solar_id: "SEE1",
      expected_version: 0,
      state: "probable_assignment",
      reviewer: "Identity reviewer",
    });
    expect(writes[0].body).not.toHaveProperty("status");
    expect(
      await screen.findByRole("tab", { name: `${en.linkedRegistry} (1)` }),
    ).toBeTruthy();
    await screen.findByRole("button", {
      name: "Map point 1 probable_assignment",
    });
  });
  it("requires a reviewer before writing a decision", async () => {
    const { user } = mount("");
    await selectOnMap(user);
    await user.click(screen.getByRole("button", { name: en.saveIdentity }));
    expect(screen.getByRole("alert").textContent).toContain(en.needReviewer);
    expect(writes).toHaveLength(0);
  });
  it("filters the registry by saved association state", async () => {
    const { user } = mount();
    await user.selectOptions(
      screen.getByLabelText(en.associationFilter),
      "associated",
    );
    await waitFor(() =>
      expect(urls.some((url) => url.includes("association=associated"))).toBe(
        true,
      ),
    );
  });
});
