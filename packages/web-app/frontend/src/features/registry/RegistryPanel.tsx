import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { request, runUrl, type IdentityReview } from "../../api";
import type { Feature } from "geojson";
import type { ReviewGeometry } from "../../types";
import { IdentityForm } from "./IdentityForm";
import { RegistrationDetails } from "./RegistrationDetails";
import type {
  RegistryGroup,
  RegistryLocations,
  RegistryUnit,
  RegistryUnitDetail,
} from "./types";

interface Summary {
  available: boolean;
  registry_ready: boolean;
  stale_groups?: number;
  registry?: { snapshot_date: string; summary: { solar: number } };
  batch?: { batch_id: string };
}
interface Props {
  runId: string;
  selectedId: string;
  imageRevision: number;
  reviewer: string;
  disabled: boolean;
  onSelect: (id: string) => boolean;
  onDirty: (dirty: boolean) => void;
  onBusy: (busy: boolean) => void;
  onChanged: () => void;
  onGroup: (feature: Feature<ReviewGeometry> | null) => void;
  onLocations: (locations: RegistryLocations | null) => void;
  onPoint?: (
    point: { id: string; coordinates: [number, number]; focus: number } | null,
  ) => void;
  onInspectImage: () => void;
  onRevealRegistry: () => void;
  children: (parts: { browser: ReactNode; inspector: ReactNode }) => ReactNode;
  t: (key: string) => string;
}

function useUnit(
  base: string,
  id: string,
  revision: number,
  onError: (error: string) => void,
) {
  const [value, setValue] = useState<RegistryUnitDetail | null>(null);
  useEffect(() => {
    setValue((previous) => (previous?.unit.id === id ? previous : null));
    if (!id) return;
    const controller = new AbortController();
    request<RegistryUnitDetail>(`${base}/units/${encodeURIComponent(id)}`, {
      signal: controller.signal,
    })
      .then((data) => {
        if (!controller.signal.aborted) setValue(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted) onError(String(e));
      });
    return () => controller.abort();
  }, [base, id, revision, onError]);
  return value?.unit.id === id ? value : null;
}

function UnitFacts({ unit, t }: { unit: RegistryUnit; t: Props["t"] }) {
  return (
    <div className="registry-unit-facts">
      <b>
        {unit.power_kw ?? "—"} kWp · {unit.module_count ?? "—"}{" "}
        {t("moduleCountShort")}
      </b>
      <p>
        {unit.municipality} · {unit.postcode}
      </p>
      <p>
        {t("commissioned")}: {unit.commissioned || "—"}
      </p>
      <dl className="registry-facts">
        <div>
          <dt>{t("mainDirection")}</dt>
          <dd>{unit.decoded.Hauptausrichtung || "—"}</dd>
        </div>
        <div>
          <dt>{t("secondaryDirection")}</dt>
          <dd>{unit.decoded.Nebenausrichtung || "—"}</dd>
        </div>
        <div>
          <dt>{t("mainInclination")}</dt>
          <dd>{unit.decoded.HauptausrichtungNeigungswinkel || "—"}</dd>
        </div>
      </dl>
    </div>
  );
}

export function RegistryPanel(props: Props) {
  const { t } = props;
  const base = `${runUrl(props.runId)}/matching`;
  const [summary, setSummary] = useState<Summary | null>(null);
  const [query, setQuery] = useState("");
  const [cohort, setCohort] = useState("target");
  const [association, setAssociation] = useState("all");
  const [offset, setOffset] = useState(0);
  const [units, setUnits] = useState<{ total: number; items: RegistryUnit[] }>({
    total: 0,
    items: [],
  });
  const [browseId, setBrowseId] = useState("");
  const [pairId, setPairId] = useState("");
  const [group, setGroup] = useState<RegistryGroup | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [formReset, setFormReset] = useState(0);
  const [tab, setTab] = useState("potential");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [detailsVisible, setDetailsVisible] = useState(false);
  const dirty = useRef(false);
  const inspectorElement = useRef<HTMLElement>(null);
  const groupRef = useRef(group);
  groupRef.current = group;
  const browseRef = useRef(browseId);
  browseRef.current = browseId;
  const markDirty = useCallback(
    (value: boolean) => {
      dirty.current = value;
      props.onDirty(value);
    },
    [props.onDirty],
  );
  const browse = useUnit(base, browseId, refresh, setError);
  const otherPair = useUnit(
    base,
    pairId === browseId ? "" : pairId,
    refresh,
    setError,
  );
  const pair = pairId === browseId ? browse : otherPair;
  const previous = group?.reviews?.find((r) => r.solar_id === pairId);
  const batchChanged = Boolean(
    group && summary?.batch && group.batch_id !== summary.batch.batch_id,
  );

  useEffect(() => {
    if (!detailsVisible) return;
    const pane = inspectorElement.current?.closest(
      ".registry-inspector-column",
    );
    if (pane instanceof HTMLElement) pane.scrollTop = 0;
  }, [detailsVisible, browse?.unit.id]);

  useEffect(() => {
    const controller = new AbortController();
    const poll = () =>
      request<Summary>(base, { signal: controller.signal })
        .then((data) => {
          if (!controller.signal.aborted) setSummary(data);
        })
        .catch((e) => {
          if (!controller.signal.aborted) setError(String(e));
        });
    void poll();
    const timer = window.setInterval(() => {
      if (!document.hidden) void poll();
    }, 15000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [base, refresh, props.imageRevision]);

  useEffect(() => {
    if (!summary?.registry_ready) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({
        q: query,
        cohort,
        association,
        offset: String(offset),
        limit: "30",
      });
      request<typeof units>(`${base}/units?${params}`, {
        signal: controller.signal,
      })
        .then((data) => {
          if (!controller.signal.aborted) setUnits(data);
        })
        .catch((e) => {
          if (!controller.signal.aborted) setError(String(e));
        });
    }, 150);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [
    base,
    summary?.registry_ready,
    query,
    cohort,
    association,
    offset,
    refresh,
  ]);

  useEffect(() => {
    setGroup(null);
    setPairId(browseRef.current);
    setTab("potential");
    markDirty(false);
    setDetailsVisible(!props.selectedId && Boolean(browseRef.current));
  }, [props.selectedId, markDirty]);

  useEffect(() => {
    if (!props.selectedId || !summary?.available) return;
    const controller = new AbortController();
    request<RegistryGroup>(
      `${base}/candidates/${encodeURIComponent(props.selectedId)}`,
      { signal: controller.signal },
    )
      .then((data) => {
        if (controller.signal.aborted) return;
        if (
          dirty.current &&
          groupRef.current &&
          groupRef.current.batch_id !== data.batch_id
        )
          return;
        const nextPair =
          browseRef.current ||
          data.reviews?.find((r) =>
            ["verified_identity", "probable_assignment"].includes(r.state),
          )?.solar_id ||
          data.alternatives?.[0]?.solar_id;
        if (
          !groupRef.current &&
          nextPair &&
          data.reviews?.some(
            (r) =>
              r.solar_id === nextPair &&
              ["verified_identity", "probable_assignment"].includes(r.state),
          )
        )
          setTab("linked");
        setGroup(data);
        setPairId(
          (current) =>
            current ||
            browseRef.current ||
            data.reviews?.find((r) =>
              ["verified_identity", "probable_assignment"].includes(r.state),
            )?.solar_id ||
            data.alternatives?.[0]?.solar_id ||
            "",
        );
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(String(e));
      });
    return () => controller.abort();
  }, [
    base,
    props.selectedId,
    props.imageRevision,
    summary?.available,
    summary?.batch?.batch_id,
    refresh,
  ]);

  useEffect(() => {
    props.onGroup(group?.group || null);
    return () => props.onGroup(null);
  }, [group?.group, props.onGroup]);
  useEffect(() => {
    const u = browse?.unit;
    props.onPoint?.(
      u?.longitude != null && u.latitude != null
        ? { id: u.id, coordinates: [u.longitude, u.latitude], focus: 0 }
        : null,
    );
    return () => props.onPoint?.(null);
  }, [browse?.unit, props.onPoint]);
  const locations = useMemo<RegistryLocations | null>(() => {
    if (!browse) return null;
    const points = new Map<string, RegistryLocations["points"][number]>();
    for (const [index, a] of (browse.ranking?.alternatives || []).entries()) {
      if (!a.position || !a.member_ids?.length) continue;
      points.set(a.group_id, {
        groupId: a.group_id,
        memberIds: a.member_ids,
        position: a.position,
        number: index + 1,
        score: a.score,
        state: "candidate",
        stale: Boolean(a.stale),
      });
    }
    for (const decision of browse.reviewed_associations || []) {
      if (
        !decision.position ||
        !decision.member_ids?.length ||
        ["rejected", "pending"].includes(decision.state)
      )
        continue;
      const existing = points.get(decision.group_id);
      points.set(decision.group_id, {
        groupId: decision.group_id,
        memberIds: decision.member_ids,
        position: decision.position,
        number: existing?.number || points.size + 1,
        score: existing?.score,
        state: decision.state,
        stale: Boolean(decision.stale),
      });
    }
    return {
      unitId: browse.unit.id,
      focusKey: browse.unit.id,
      points: [...points.values()],
    };
  }, [browse]);
  useEffect(() => {
    props.onLocations(locations);
    return () => props.onLocations(null);
  }, [locations, props.onLocations]);

  function chooseBrowse(id: string) {
    if (props.disabled || saving) return;
    if (id === browseId) {
      setDetailsVisible(true);
      props.onRevealRegistry();
      return;
    }
    if (!props.onSelect("")) return;
    markDirty(false);
    setBrowseId(id);
    setPairId(id);
    setGroup(null);
    setDetailsVisible(true);
    setMessage("");
    setError("");
    setFormReset((v) => v + 1);
  }
  function choosePair(id: string) {
    if (props.disabled || saving || id === pairId) return;
    if (dirty.current && !window.confirm(t("discardIdentity"))) return;
    markDirty(false);
    setPairId(id);
    setMessage("");
    setFormReset((v) => v + 1);
  }
  async function save(
    values: Pick<
      IdentityReview,
      "state" | "evidence_type" | "evidence" | "note" | "group_confirmed"
    >,
  ) {
    if (!group?.group || !pairId) return;
    if (!props.reviewer.trim()) {
      setError(t("needReviewer"));
      return;
    }
    if (values.state !== "pending" && !values.evidence?.trim()) {
      setError(t("identityEvidenceRequired"));
      return;
    }
    setSaving(true);
    props.onBusy(true);
    setError("");
    try {
      const update: IdentityReview = {
        ...values,
        batch_id: group.batch_id,
        solar_id: pairId,
        expected_version: previous?.version || 0,
        reviewer: props.reviewer.trim(),
      };
      await request(`${base}/groups/${encodeURIComponent(group.group.id)}`, {
        method: "PATCH",
        body: JSON.stringify(update),
      });
      const updated = await request<RegistryGroup>(
        `${base}/groups/${encodeURIComponent(group.group.id)}`,
      );
      setGroup(updated);
      markDirty(false);
      setFormReset((v) => v + 1);
      setRefresh((v) => v + 1);
      props.onChanged();
      setMessage(t("identitySaved"));
      if (["probable_assignment", "verified_identity"].includes(values.state))
        setTab("linked");
      else setTab("history");
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
      props.onBusy(false);
    }
  }
  async function rebuild() {
    if (dirty.current && !window.confirm(t("discardIdentity"))) return;
    markDirty(false);
    setFormReset((v) => v + 1);
    setSaving(true);
    props.onBusy(true);
    setError("");
    try {
      await request(`${base}/build`, { method: "POST" });
      setRefresh((v) => v + 1);
      setMessage(t("matchingScheduled"));
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
      props.onBusy(false);
    }
  }
  const linked =
    group?.reviews?.filter((r) =>
      ["verified_identity", "probable_assignment"].includes(r.state),
    ) || [];
  const potentialIds = [
    ...new Set([
      ...(browseId ? [browseId] : []),
      ...(group?.alternatives || []).map((a) => a.solar_id),
    ]),
  ].filter((id) => !linked.some((r) => r.solar_id === id));
  const history =
    group?.reviews?.filter(
      (r) => !["verified_identity", "probable_assignment"].includes(r.state),
    ) || [];
  const tabIds =
    tab === "linked"
      ? linked.map((r) => r.solar_id)
      : tab === "history"
        ? history.map((r) => r.solar_id)
        : potentialIds;
  const proposal =
    group?.alternatives?.find((a) => a.solar_id === pairId) ||
    pair?.ranking?.alternatives.find((a) => a.group_id === group?.group?.id);
  const browser = (
    <section
      className="registry-browser registry-controls"
      aria-label={t("registryListTitle")}
    >
      <div className="registry-browser-heading">
        <span className="step-number">1</span>
        <div>
          <h2>{t("registryListTitle")}</h2>
          <small>
            {summary?.registry?.snapshot_date || t("registryAcquiring")}
          </small>
        </div>
      </div>
      <label>
        {t("registrySearch")}
        <input
          aria-label={t("registrySearch")}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOffset(0);
          }}
        />
      </label>
      <label>
        {t("registryCohort")}
        <select
          aria-label={t("registryCohort")}
          value={cohort}
          onChange={(e) => {
            setCohort(e.target.value);
            setOffset(0);
          }}
        >
          {["target", "coordinates", "all"].map((v) => (
            <option key={v} value={v}>
              {t("cohort_" + v)}
            </option>
          ))}
        </select>
      </label>
      <label>
        {t("associationFilter")}
        <select
          aria-label={t("associationFilter")}
          value={association}
          onChange={(e) => {
            setAssociation(e.target.value);
            setOffset(0);
          }}
        >
          {["all", "unreviewed", "associated", "verified"].map((v) => (
            <option key={v} value={v}>
              {t("association_" + v)}
            </option>
          ))}
        </select>
      </label>
      {browse && (
        <div className="registry-map-summary">
          <b>
            {t("selectedRegistryRecord")}: {browse.unit.id}
          </b>
          <p>
            {browse.unit.power_kw} kWp · {browse.unit.module_count ?? "—"}{" "}
            {t("moduleCountShort")}
          </p>
          <p>
            {t("mapCandidateCount")}: {locations?.points.length || 0} ·{" "}
            {t("bestTieCount")}: {browse.ranking?.best_tie_count ?? 0}
          </p>
          <small>{t("candidateExamplesHint")}</small>
          <button
            onClick={() => {
              setDetailsVisible(true);
              props.onRevealRegistry();
            }}
          >
            {t("viewRegistrationDetails")}
          </button>
          <div className="registry-point-shortcuts">
            {locations?.points.map((point) => (
              <button
                key={point.groupId}
                aria-label={`${t("mapCandidate")} ${point.number}`}
                disabled={props.disabled || saving}
                onClick={() => props.onSelect(point.memberIds[0])}
              >
                {point.number}
              </button>
            ))}
          </div>
          {!!locations?.points.length && (
            <button
              onClick={() =>
                props.onLocations({
                  ...locations,
                  focusKey: `${browse.unit.id}:${Date.now()}`,
                })
              }
            >
              {t("fitRegistryCandidates")}
            </button>
          )}
          {browse.unit.longitude != null && browse.unit.latitude != null && (
            <button
              onClick={() =>
                props.onPoint?.({
                  id: browse.unit.id,
                  coordinates: [browse.unit.longitude!, browse.unit.latitude!],
                  focus: Date.now(),
                })
              }
            >
              {t("locateRegistryPoint")}
            </button>
          )}
        </div>
      )}
      <p className="registry-list-count">
        {units.total} {t("registryUnits")}
      </p>
      <div className="registry-unit-list">
        {units.items.map((u) => (
          <button
            aria-pressed={u.id === browseId}
            disabled={props.disabled || saving}
            key={u.id}
            className={u.id === browseId ? "active" : ""}
            onClick={() => chooseBrowse(u.id)}
          >
            <b>{u.id}</b>
            <span>
              {u.power_kw ?? "—"} kWp · {u.postcode}
            </span>
            <small>{u.commissioned || "—"}</small>
            <span
              className={`association-badge ${u.association_state || "unreviewed"}`}
            >
              {t("associationState_" + (u.association_state || "unreviewed"))}
              {u.association_count ? ` · ${u.association_count}` : ""}
            </span>
          </button>
        ))}
      </div>
      {!units.items.length && (
        <p className="muted">
          {summary?.registry_ready
            ? t("emptyRegistryList")
            : t("registryAcquiring")}
        </p>
      )}
      <div className="registry-pagination">
        <button
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - 30))}
        >
          {t("previousPage")}
        </button>
        <span>
          {units.total ? offset + 1 : 0}–{Math.min(offset + 30, units.total)}
        </span>
        <button
          disabled={offset + 30 >= units.total}
          onClick={() => setOffset(offset + 30)}
        >
          {t("nextPage")}
        </button>
      </div>
      <details className="registry-maintenance">
        <summary>{t("registryTools")}</summary>
        <p>
          {summary?.registry?.summary.solar ?? 0} {t("registryUnits")}
        </p>
        <button
          disabled={!summary?.registry_ready || props.disabled || saving}
          onClick={() => void rebuild()}
        >
          {t("rebuildMatching")}
        </button>
        {!!summary?.stale_groups && (
          <p>
            {summary.stale_groups} · {t("staleGroups")}
          </p>
        )}
      </details>
    </section>
  );
  const inspector = (
    <section
      ref={inspectorElement}
      className="registry-inspector registry-controls"
      aria-label={t("registryInspectorTitle")}
    >
      <div className="registry-inspector-heading">
        <span className="step-number">3</span>
        <h2>
          {t(
            detailsVisible
              ? "registrationDetailsTitle"
              : "registryInspectorTitle",
          )}
        </h2>
      </div>
      {browseId && (
        <div className="registration-view-switch">
          <button
            aria-pressed={detailsVisible}
            onClick={() => setDetailsVisible(true)}
          >
            {t("registrationDetailsTab")}
          </button>
          <button
            aria-pressed={!detailsVisible}
            disabled={!group?.group}
            onClick={() => setDetailsVisible(false)}
          >
            {t("registrationAssociationTab")}
          </button>
        </div>
      )}
      {error && (
        <div role="alert" className="notice error">
          {error}
          <button
            onClick={() => {
              if (!dirty.current || window.confirm(t("discardIdentity"))) {
                markDirty(false);
                setError("");
                setFormReset((v) => v + 1);
                setRefresh((v) => v + 1);
              }
            }}
          >
            {t("reload")}
          </button>
        </div>
      )}
      {message && (
        <p role="status" className="notice success">
          {message}
        </p>
      )}
      <div hidden={!detailsVisible}>
        {browse ? (
          <>
            <RegistrationDetails detail={browse} t={t} />
            <p className="fine-print">{t("chooseCandidateOnMap")}</p>
          </>
        ) : (
          <p>{t("registrationLoading")}</p>
        )}
      </div>
      <div hidden={detailsVisible}>
        {!group?.group ? (
          <div className="registry-empty-inspector">
            <b>{t("clickCandidatePoint")}</b>
            <p>
              {browseId ? t("chooseCandidateOnMap") : t("chooseRegistryFirst")}
            </p>
          </div>
        ) : (
          <>
            <div className="array-summary">
              <b>
                {t("selectedArrayGroup")} ·{" "}
                {group.group.properties.area_m2.toFixed(2)} m²
              </b>
              <p>
                {group.group.properties.member_ids.length} {t("memberRegions")}{" "}
                · {group.group.properties.capture_dates.join(", ")}
              </p>
              <button className="text-button" onClick={props.onInspectImage}>
                {t("inspectImageMembers")}
              </button>
            </div>
            {(group.stale ||
              group.registry_snapshot_changed ||
              batchChanged) && (
              <p role="status" className="notice">
                {t("staleIdentity")}
              </p>
            )}
            <div
              className="association-tabs"
              role="tablist"
              aria-label={t("associationTabs")}
            >
              {[
                ["linked", "linkedRegistry", linked.length],
                ["potential", "potentialRegistry", potentialIds.length],
                ["history", "identityHistory", history.length],
              ].map(([value, label, count]) => (
                <button
                  role="tab"
                  aria-selected={tab === value}
                  key={value}
                  onClick={() => setTab(String(value))}
                >
                  {t(String(label))} ({count})
                </button>
              ))}
            </div>
            <div className="registry-pair-list">
              {tabIds.map((id) => {
                const u =
                  group.units?.[id] ||
                  (browse?.unit.id === id ? browse.unit : null) ||
                  (pair?.unit.id === id ? pair.unit : null);
                const decision = group.reviews?.find((r) => r.solar_id === id);
                const a = group.alternatives?.find((v) => v.solar_id === id);
                return (
                  <button
                    key={id}
                    disabled={props.disabled || saving}
                    aria-pressed={pairId === id}
                    className={pairId === id ? "active" : ""}
                    onClick={() => choosePair(id)}
                  >
                    <b>{id}</b>
                    <span>
                      {u?.power_kw ?? "—"} kWp · {u?.postcode || "—"}
                    </span>
                    {id === browseId && (
                      <small>{t("currentRegistrySelection")}</small>
                    )}
                    {decision ? (
                      <span className={`association-badge ${decision.state}`}>
                        {t("identity_" + decision.state)} · {decision.reviewer}
                      </span>
                    ) : (
                      a && (
                        <small>
                          {t("rankingScore")}: {a.score.toFixed(3)}
                        </small>
                      )
                    )}
                  </button>
                );
              })}
            </div>
            {!tabIds.length && (
              <p className="muted">
                {t(
                  tab === "linked"
                    ? "noLinkedRegistry"
                    : tab === "history"
                      ? "noIdentityHistory"
                      : "noPotentialRegistry",
                )}
              </p>
            )}
            {group.ranking_statistics && (
              <p className="fine-print">
                {t("candidatePool")}:{" "}
                {group.ranking_statistics.candidate_pool_size} ·{" "}
                {t("bestTieCount")}: {group.ranking_statistics.best_tie_count}
              </p>
            )}
            {pair && (
              <div className="registry-pair-detail">
                <h3>{pair.unit.id}</h3>
                <UnitFacts unit={pair.unit} t={t} />
                {proposal && (
                  <p className="fine-print">
                    {t("rankingScore")}: {proposal.score.toFixed(3)} ·{" "}
                    {t("timing_" + proposal.evidence.timing)}
                  </p>
                )}
                <p className="fine-print">{t("registryScoreCaution")}</p>
                {group.competing_reviews?.some(
                  (r) => r.solar_id === pairId,
                ) && <p className="notice">{t("competingIdentity")}</p>}
                <IdentityForm
                  key={`${group.batch_id}:${group.group.id}:${pairId}:${previous?.version || 0}:${formReset}`}
                  previous={previous}
                  disabled={
                    props.disabled ||
                    saving ||
                    group.stale ||
                    group.registry_snapshot_changed ||
                    batchChanged
                  }
                  onDirty={markDirty}
                  onSave={(values) => void save(values)}
                  t={t}
                />
                <details>
                  <summary>{t("evidenceDetails")}</summary>
                  {[
                    ...new Set([
                      ...(pair.unit.flags || []),
                      ...(pair.ranking?.flags || []),
                      ...group.group.properties.flags,
                    ]),
                  ].map((flag) => (
                    <p className="fine-print" key={flag}>
                      {t("flag_" + flag)}
                    </p>
                  ))}
                  <p className="fine-print">{t("rankingLimitations")}</p>
                </details>
                {pair.storage.length > 0 && (
                  <details>
                    <summary>
                      {t("storageRelationships")} ({pair.storage.length})
                    </summary>
                    <p className="fine-print">{t("storageNoAllocation")}</p>
                    {pair.storage.map((s) => (
                      <p className="fine-print" key={s.storage_id}>
                        {s.storage_id} · {s.net_power_kw ?? "—"} kW ·{" "}
                        {s.usable_capacity_kwh ?? "—"} kWh ·{" "}
                        {t("storage_" + s.relationship_status)}
                      </p>
                    ))}
                  </details>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
  return props.children({ browser, inspector });
}
